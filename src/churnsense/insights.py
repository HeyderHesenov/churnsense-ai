"""What the dashboard pages show about the book, computed once, here.

Pure functions over a ``churnsense.book.Book``: no IO, no web framework, no
plotting. The API serialises their results and the browser only draws them, so
a figure on a page and a figure in a test come from the same code.

Two partitions, two purposes, and every function says which it reads:

* **validation** - for setting a stated probability against what happened, on
  customers the model was not fitted on (they did inform the cut);
* **train + validation** - the outcomes ``churnsense.book`` allows a page to show;
* **train only** - the descriptive patterns, read off the partition every
  modelling decision was taken on, exactly as ``make eda`` reads it.

The test partition's outcomes are never reached: every outcome comes through
``book.labelled``, which refuses it. All-row figures are scores and charges,
which every customer has.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from churnsense import analysis as an
from churnsense import book as books
from churnsense import suppression
from churnsense.data import schema
from churnsense.evaluation.disclosure import MIN_CELL
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
    reliability_table,
    segment_table,
    wilson_interval,
)
from churnsense.evaluation.threshold import (
    ThresholdScenario,
    indifference_band,
    recommend_threshold_from,
    scenario_at,
    sweep_thresholds,
    threshold_sensitivity,
)

if TYPE_CHECKING:
    from churnsense.config import Business, Config

#: The probability histogram's resolution: 20 bins of 0.05.
HISTOGRAM_BINS = 20
# ``MIN_CELL``: fewer customers than this, and no churn rate or churn count is
# reported for the group. A breakdown also hides the further counts that would
# give a small group's away (``churnsense.suppression``). Not disclosure control
# for the whole app: training and validation customers' own outcomes are listed
# one by one. The test partition's are not: ``evaluation.disclosure``.
#: A segment row's fields that state an outcome, or rest on one: a segment of
#: fewer than ``MIN_CELL`` customers shows none of them. A 95 % interval is a
#: rate in disguise, a within-segment ROC-AUC is read off the outcomes, and
#: "not scanned" (nobody flagged though someone churned) says whether anyone did.
SEGMENT_OUTCOMES: tuple[str, ...] = (
    "churners",
    "churn_rate",
    "flagged_rate",
    "precision",
    "precision_low",
    "precision_high",
    "recall",
    "recall_low",
    "recall_high",
    "roc_auc",
    "unscanned",
)


@dataclass(frozen=True, slots=True)
class Dimension:
    """A breakdown a page may cross: a book column and its levels in display order."""

    key: str
    column: str
    levels: tuple[str, ...]


_NO_YES = ("No", "Yes")
_ADDON = ("No", "Yes", "No internet service")

#: The only dimensions a cross-tab accepts, in the design's level order.
DIMENSIONS: dict[str, Dimension] = {
    d.key: d
    for d in (
        Dimension("Contract", "Contract", ("Month-to-month", "One year", "Two year")),
        Dimension("tenure", "tenure_bucket", an.TENURE_LABELS),
        Dimension("monthly", "charge_band", an.CHARGE_LABELS),
        Dimension("InternetService", "InternetService", ("Fiber optic", "DSL", "No")),
        Dimension(
            "PaymentMethod",
            "PaymentMethod",
            (
                "Electronic check",
                "Mailed check",
                "Bank transfer (automatic)",
                "Credit card (automatic)",
            ),
        ),
        Dimension("OnlineSecurity", "OnlineSecurity", _ADDON),
        Dimension("TechSupport", "TechSupport", _ADDON),
        Dimension("PaperlessBilling", "PaperlessBilling", _NO_YES),
        Dimension("SeniorCitizen", "SeniorCitizen", ("0", "1")),
        Dimension("Partner", "Partner", _NO_YES),
        Dimension("Dependents", "Dependents", _NO_YES),
        Dimension("StreamingTV", "StreamingTV", _ADDON),
        Dimension("MultipleLines", "MultipleLines", ("No", "Yes", "No phone service")),
        Dimension("PhoneService", "PhoneService", _NO_YES),
    )
}

#: The four breakdowns the overview compares, in tab order.
OVERVIEW_SEGMENTS: tuple[str, ...] = ("Contract", "PaymentMethod", "InternetService", "monthly")


# --- small helpers ----------------------------------------------------------


def _rate(numerator: float, denominator: float) -> float | None:
    """A share, or ``None`` when there is nothing to divide by."""
    return float(numerator) / float(denominator) if denominator else None


def _finite(value: object) -> object:
    """``value``, with an undefined float (NaN) as ``None``: JSON has no NaN."""
    return None if isinstance(value, float) and np.isnan(value) else value


def small_group(row: dict) -> dict:
    """A segment row, or the same row with every outcome field null below ``MIN_CELL``.

    The one rule for a segment table, recorded (test) or measured (validation).
    """
    if row["customers"] >= MIN_CELL:
        return row
    return row | dict.fromkeys(SEGMENT_OUTCOMES)


def histogram_bins(probabilities: pd.Series | np.ndarray) -> np.ndarray:
    """Each probability's 0.05-wide bin, 0-19; a probability of 1 joins the last bin."""
    p = np.asarray(probabilities, dtype=float)
    return np.minimum(HISTOGRAM_BINS - 1, np.floor(p * HISTOGRAM_BINS).astype(int))


def _band_names(cfg: Config) -> list[str]:
    return [band.name for band in cfg.risk_bands]


# --- results ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observed:
    """Outcomes counted over the rows whose outcome may be shown.

    ``churned`` and ``rate`` are ``None`` for fewer than ``MIN_CELL`` customers.
    """

    customers: int
    churned: int | None
    rate: float | None


@dataclass(frozen=True, slots=True)
class Share:
    customers: int
    share: float | None
    monthly_charges: float
    #: Of those, who the retention queue lists: flagged and not recorded as churned,
    #: split into those recorded active and those whose status is unknown.
    in_queue: int
    in_queue_active: int
    in_queue_unknown: int
    in_queue_share: float | None
    #: Of those recorded active only: an unknown customer may no longer be paying.
    in_queue_monthly: float


@dataclass(frozen=True, slots=True)
class BandSummary:
    name: str
    min: float
    max: float
    customers: int
    share: float | None
    monthly_charges: float
    monthly_share: float | None
    flagged: int
    #: Of those, the customers not recorded as churned: withheld outcomes count.
    flagged_not_churned: int
    #: Observed churn among this band's training and validation customers.
    observed: Observed | None


@dataclass(frozen=True, slots=True)
class HistogramBin:
    low: float
    high: float
    band: str
    customers: int
    monthly_charges: float
    #: Validation customers in the bin, and how many churned.
    observed: Observed | None
    #: What those validation customers were told on average: where their rate is drawn.
    observed_mean: float | None


#: Why a cell's churn is hidden: it has fewer than ``MIN_CELL`` customers, or
#: showing it would let a smaller group's count be worked out.
HiddenReason = Literal["too_few", "complementary"]


@dataclass(frozen=True, slots=True)
class Cell:
    """One level (or pair of levels) of a breakdown. A hidden cell carries its size only."""

    row: str
    column: str | None
    customers: int
    churned: int | None
    rate: float | None
    #: ``rate`` relative to the partition's overall churn rate, this cell included.
    lift: float | None
    #: Why the churn is hidden, or ``None`` when it is reported.
    reason: HiddenReason | None = None
    #: ``rate``'s 95 % Wilson interval.
    low: float | None = None
    high: float | None = None

    @property
    def suppressed(self) -> bool:
        return self.rate is None


@dataclass(frozen=True, slots=True)
class Breakdown:
    """A one- or two-way breakdown of the training partition."""

    rows: str
    columns: str | None
    partition_customers: int
    partition_churned: int
    base_rate: float | None
    row_totals: list[Cell]
    column_totals: list[Cell]
    cells: list[Cell]
    #: The highest churn rates among reported cells, highest first.
    ranked: list[Cell]


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """Churn by tenure bucket and the median tenure of each outcome (training)."""

    partition_customers: int
    base_rate: float | None
    buckets: list[Cell]
    median_tenure_churned: float | None
    median_tenure_stayed: float | None


@dataclass(frozen=True, slots=True)
class Economics:
    """The validation sweep under one set of business assumptions."""

    business: Business
    customers: int
    churners: int
    sweep: pd.DataFrame
    recommended: ThresholdScenario
    peak: ThresholdScenario
    #: Thresholds whose net benefit is within one offer's cost of the peak.
    band_low: float
    band_high: float
    band_floor: float
    sensitivity: pd.DataFrame


@dataclass(frozen=True, slots=True)
class ValidationEvaluation:
    """The served model measured on the validation partition at one threshold."""

    metrics: ClassificationMetrics
    #: Churners over customers: the precision of flagging everyone, PR-AUC's no-skill line.
    churn_rate: float
    #: The accuracy of flagging nobody.
    no_skill_accuracy: float
    #: Expected calibration error over ``reliability``'s bins, on the served probabilities.
    ece: float
    reliability: list[dict]
    segments: list[dict]


# --- the book, scored --------------------------------------------------------


def band_summaries(book: books.Book, cfg: Config) -> list[BandSummary]:
    """Customers, charges, flags and observed churn per fixed risk band."""
    frame = book.frame
    seen = books.labelled(frame, shown=book.outcomes)
    total, charges = len(frame), float(frame["MonthlyCharges"].sum())
    can_leave = frame["flagged"] & (books.outcome_codes(frame, book.outcomes) != books.CHURNED)
    out = []
    for band in cfg.risk_bands:
        rows = frame[frame["risk_band"] == band.name]
        seen_rows = seen[seen["risk_band"] == band.name]
        monthly = float(rows["MonthlyCharges"].sum())
        out.append(
            BandSummary(
                name=band.name,
                min=band.min,
                max=band.max,
                customers=len(rows),
                share=_rate(len(rows), total),
                monthly_charges=monthly,
                monthly_share=_rate(monthly, charges),
                flagged=int(rows["flagged"].sum()),
                flagged_not_churned=int(can_leave[rows.index].sum()),
                observed=_observed(seen_rows) if len(seen) else None,
            )
        )
    return out


def _observed(rows: pd.DataFrame) -> Observed:
    """The one place a group's observed churn is counted, and where small groups lose it."""
    if len(rows) < MIN_CELL:
        return Observed(customers=len(rows), churned=None, rate=None)
    churned = int(rows[schema.TARGET].sum())
    return Observed(customers=len(rows), churned=churned, rate=_rate(churned, len(rows)))


def probability_histogram(book: books.Book, cfg: Config) -> list[HistogramBin]:
    """Customers and charges per 0.05 of probability, with observed churn per bin.

    Observed churn counts validation customers only, which the model was not
    fitted on, so a bin's rate can be read against its probability range. They
    did inform the cut and the choice to calibrate, so it is no independent test.
    """
    frame = book.frame
    bins = histogram_bins(frame["churn_probability"])
    seen = books.labelled(frame, "validation", shown=book.outcomes)
    seen_bins = histogram_bins(seen["churn_probability"])
    names = _band_names(cfg)
    edges = [band.min for band in cfg.risk_bands]
    out = []
    for i in range(HISTOGRAM_BINS):
        low = i / HISTOGRAM_BINS
        rows = frame[bins == i]
        band = names[int(np.searchsorted(edges, low, side="right")) - 1]
        out.append(
            HistogramBin(
                low=low,
                high=(i + 1) / HISTOGRAM_BINS,
                band=band,
                customers=len(rows),
                monthly_charges=float(rows["MonthlyCharges"].sum()),
                observed=(observed := _observed(seen[seen_bins == i]) if len(seen) else None),
                observed_mean=None
                if observed is None or observed.rate is None
                else float(seen.loc[seen_bins == i, "churn_probability"].mean()),
            )
        )
    return out


def _status(book: books.Book) -> pd.Series:
    return books.status_codes(book.frame, book.outcomes)


def _share(book: books.Book, rows: pd.Series) -> Share:
    """``rows`` of the book, and the part of them the retention queue lists."""
    total = len(book.frame)
    charges = book.frame["MonthlyCharges"]
    status = _status(book)
    active = rows & book.frame["flagged"] & (status == books.ACTIVE)
    unknown = rows & book.frame["flagged"] & (status == books.UNKNOWN)
    in_queue = int(active.sum() + unknown.sum())
    return Share(
        int(rows.sum()),
        _rate(int(rows.sum()), total),
        float(charges[rows].sum()),
        in_queue,
        int(active.sum()),
        int(unknown.sum()),
        _rate(in_queue, total),
        float(charges[active].sum()),
    )


def high_or_critical(book: books.Book, cfg: Config) -> Share:
    """Customers in the two highest bands, and their monthly charges."""
    return _share(book, book.frame["risk_band"].isin(_band_names(cfg)[-2:]))


def flagged_share(book: books.Book) -> Share:
    return _share(book, book.frame["flagged"])


def current_monthly_charges(book: books.Book) -> float | None:
    """Monthly charges of customers recorded active; ``None`` when nobody is known to be."""
    active = _status(book) == books.ACTIVE
    return float(book.frame.loc[active, "MonthlyCharges"].sum()) if active.any() else None


def unknown_customers(book: books.Book) -> int:
    """Customers whose status is unknown: counted as neither active nor churned."""
    return int((_status(book) == books.UNKNOWN).sum())


def unknown_monthly_charges(book: books.Book) -> float:
    """What those customers were charged, for saying what ``current`` leaves out."""
    return float(book.frame.loc[_status(book) == books.UNKNOWN, "MonthlyCharges"].sum())


def observed_overall(book: books.Book) -> Observed | None:
    """Observed churn over every customer whose outcome may be shown."""
    seen = books.labelled(book.frame, shown=book.outcomes)
    return _observed(seen) if len(seen) else None


# --- breakdowns of the training partition --------------------------------------


def breakdown(
    book: books.Book, rows: str, columns: str | None = None, *, top: int = 7
) -> Breakdown | None:
    """A one- or two-way breakdown of the training partition, small groups' churn hidden.

    ``None`` when no outcome may be shown. ``rows`` and ``columns`` are keys of
    ``DIMENSIONS``; anything else raises ``KeyError``. Every combination of
    levels is returned, an empty one with zero customers. A group of fewer than
    ``MIN_CELL`` customers keeps only its size, and so does every further group
    that would let one's churn - alone or summed with others - be worked out
    (``churnsense.suppression``).

    The margins are each dimension's own one-way breakdown, and a grid is
    protected in ``DIMENSIONS`` order whichever way round it is asked for, so
    responses agree on what they hide. The guarantee is per response; see
    ``churnsense.suppression`` for where combining them could narrow a count.
    """
    row_dim = DIMENSIONS[rows]
    col_dim = DIMENSIONS[columns] if columns is not None else None
    train = books.labelled(book.frame, "train", shown=book.outcomes)
    if train.empty:
        return None
    base = _rate(train[schema.TARGET].sum(), len(train))
    row_totals = _one_way(train, row_dim, base)
    if col_dim is None:
        column_totals, grid = [], row_totals
    else:
        column_totals = _one_way(train, col_dim, base)
        grid = _two_way(train, (row_dim, row_totals), (col_dim, column_totals), base)
    ranked = sorted((c for c in grid if c.rate is not None), key=lambda c: -c.rate)[:top]
    return Breakdown(
        rows=rows,
        columns=columns,
        partition_customers=len(train),
        partition_churned=int(train[schema.TARGET].sum()),
        base_rate=base,
        row_totals=row_totals,
        column_totals=column_totals,
        cells=grid,
        ranked=ranked,
    )


#: Where each dimension stands in ``DIMENSIONS``: a grid is protected rows-first in this order.
_ORDER = {key: i for i, key in enumerate(DIMENSIONS)}


def _counts(train: pd.DataFrame, *dims: Dimension) -> tuple[np.ndarray, np.ndarray]:
    """Customers and churners per level (or pair of levels), in the dimensions' level order."""
    table = an.cross_tab(
        train, dims[0].column, dims[1].column if len(dims) > 1 else None, min_cell=0
    )
    shape = tuple(len(d.levels) for d in dims)
    customers, churned = np.zeros(shape, dtype=int), np.zeros(shape, dtype=int)
    positions = [{level: i for i, level in enumerate(d.levels)} for d in dims]
    for _, found in table.iterrows():
        # A level outside the dimension's list is a data error, never a dropped group.
        at = tuple(p[str(found[d.column])] for p, d in zip(positions, dims, strict=True))
        customers[at], churned[at] = int(found["customers"]), int(found["churned"])
    return customers, churned


def _one_way(train: pd.DataFrame, dim: Dimension, base: float | None) -> list[Cell]:
    customers, churned = _counts(train, dim)
    hidden = suppression.one_way(customers.tolist(), churned.tolist(), MIN_CELL)
    return [
        _cell(level, None, int(customers[i]), int(churned[i]), i in hidden, base)
        for i, level in enumerate(dim.levels)
    ]


def _two_way(
    train: pd.DataFrame,
    rows: tuple[Dimension, list[Cell]],
    columns: tuple[Dimension, list[Cell]],
    base: float | None,
) -> list[Cell]:
    """The grid of two dimensions, protected around their one-way margins in canonical order."""
    flip = _ORDER[rows[0].key] > _ORDER[columns[0].key]
    first, second = (columns, rows) if flip else (rows, columns)
    customers, churned = _counts(train, first[0], second[0])
    hidden = suppression.two_way(
        customers,
        churned,
        hidden_rows=_hidden_levels(first[1]),
        hidden_columns=_hidden_levels(second[1]),
        minimum=MIN_CELL,
    )
    if flip:
        customers, churned = customers.T, churned.T
        hidden = frozenset((k, r) for r, k in hidden)
    return [
        _cell(a, b, int(customers[i, j]), int(churned[i, j]), (i, j) in hidden, base)
        for i, a in enumerate(rows[0].levels)
        for j, b in enumerate(columns[0].levels)
    ]


def _hidden_levels(margins: list[Cell]) -> set[int]:
    """The positions of the non-empty margins whose churn is hidden."""
    return {i for i, cell in enumerate(margins) if cell.rate is None and cell.customers}


def _cell(
    row: str, column: str | None, customers: int, churned: int, hidden: bool, base: float | None
) -> Cell:
    if customers < MIN_CELL or hidden:
        reason: HiddenReason = "too_few" if customers < MIN_CELL else "complementary"
        return Cell(row, column, customers, None, None, None, reason)
    rate = churned / customers
    low, high = wilson_interval(churned, customers)
    return Cell(
        row=row,
        column=column,
        customers=customers,
        churned=churned,
        rate=rate,
        lift=rate / base if base else None,
        low=low,
        high=high,
    )


def lifecycle(book: books.Book) -> Lifecycle | None:
    """Churn by tenure bucket, and the median tenure of each outcome, on training."""
    split = breakdown(book, "tenure")
    if split is None:
        return None
    train = books.labelled(book.frame, "train", shown=book.outcomes)
    medians = an.median_tenure_by_outcome(train)
    return Lifecycle(
        partition_customers=split.partition_customers,
        base_rate=split.base_rate,
        buckets=split.row_totals,
        median_tenure_churned=medians.get(1),
        median_tenure_stayed=medians.get(0),
    )


# --- threshold economics on validation ---------------------------------------------


def economics(book: books.Book, business: Business) -> Economics | None:
    """The validation sweep, its recommendation, peak and sensitivity under ``business``.

    Uses the repository's own rule (``recommend_threshold_from``): the highest
    threshold within one offer's cost of the best net benefit. ``None`` when
    validation outcomes may not be shown.
    """
    validation = books.labelled(book.frame, "validation", shown=book.outcomes)
    if validation.empty:
        return None
    y = validation[schema.TARGET].to_numpy()
    p = validation["churn_probability"].to_numpy()
    charges = validation["MonthlyCharges"].to_numpy()
    sweep = sweep_thresholds(y, p, charges, business)
    band = indifference_band(sweep, business)
    peak_threshold = float(sweep.loc[sweep["net_benefit"].idxmax(), "threshold"])
    return Economics(
        business=business,
        customers=len(validation),
        churners=int(y.sum()),
        sweep=sweep,
        recommended=recommend_threshold_from(sweep, business),
        peak=scenario_at(sweep, peak_threshold, business),
        band_low=float(band["threshold"].min()),
        band_high=float(band["threshold"].max()),
        band_floor=float(sweep["net_benefit"].max() - max(business.retention_offer_cost, 0.0)),
        sensitivity=threshold_sensitivity(y, p, charges, business),
    )


def reliability_bins(y: np.ndarray, p: np.ndarray) -> list[dict]:
    """``reliability_table``'s bins with their edges; below ``MIN_CELL`` customers, no rate."""
    return [
        {
            "low": float(row.low),
            "high": float(row.high),
            "customers": int(row.count),
            "mean_predicted": float(row.mean_predicted),
        }
        | (
            {"churned": round(row.observed_rate * row.count), "rate": float(row.observed_rate)}
            if row.count >= MIN_CELL
            else {"churned": None, "rate": None}
        )
        for row in reliability_table(y, p, with_edges=True).itertuples(index=False)
    ]


def validation_evaluation(
    book: books.Book, threshold: float, columns: tuple[str, ...]
) -> ValidationEvaluation | None:
    """The served model on validation at ``threshold``: metrics, reliability and segments.

    The figures the Streamlit performance page computes, from the book's
    validation rows and the served probabilities, so they are the numbers a
    customer's own page shows. Validation chose the threshold and whether
    calibration is kept, so they are somewhat optimistic; nothing here reads
    the test partition. ``None`` when validation outcomes may not be shown, or
    hold a single outcome, for which no ranking metric is defined.
    """
    rows = books.labelled(book.frame, "validation", shown=book.outcomes)
    y = rows[schema.TARGET].to_numpy()
    if np.unique(y).size < 2:
        return None
    p = rows["churn_probability"].to_numpy()
    metrics = evaluate(y, p, threshold)
    churn_rate = metrics.positives / metrics.n
    reliability = reliability_bins(y, p)
    present = [column for column in columns if column in rows.columns]
    table = segment_table(y, p, rows, present, threshold) if present else pd.DataFrame()
    segments = [
        small_group(
            {key: _finite(value) for key, value in row.items()}
            | {"unscanned": row["flagged"] == 0 and row["churners"] > 0}
        )
        for row in table.to_dict(orient="records")
    ]
    return ValidationEvaluation(
        metrics=metrics,
        churn_rate=churn_rate,
        no_skill_accuracy=1 - churn_rate,
        ece=expected_calibration_error(y, p),
        reliability=reliability,
        segments=segments,
    )


# --- customers ---------------------------------------------------------------------


#: Sort keys a customer listing accepts, and the column each sorts by.
SORT_COLUMNS: dict[str, str] = {
    "id": schema.ID_COLUMN,
    "probability": "churn_probability",
    "tenure": "tenure",
    "monthly": "MonthlyCharges",
    "at_risk": "at_risk",
}


@dataclass(frozen=True, slots=True)
class CustomerFilter:
    """What a customer listing narrows the book to. ``None`` means "any"."""

    query: str = ""
    bands: tuple[str, ...] | None = None
    contract: str | None = None
    internet: str | None = None
    payment: str | None = None
    tenure_min: int | None = None
    tenure_max: int | None = None
    flagged: bool | None = None
    partition: str | None = None
    ids: tuple[str, ...] | None = None
    #: Leave out customers recorded as churned. An unknown status is not one of them.
    exclude_churned: bool = False


def with_outcomes(book: books.Book) -> pd.DataFrame:
    """The book's frame plus each row's outcome, status and monthly revenue at risk.

    Revenue at risk is probability x monthly charges for a customer recorded active.
    One recorded as churned has none, and for one whose status is unknown it cannot be
    established: nothing here may read whether they still pay. Both are NaN, so no sum
    counts them and they sort last.
    """
    frame = book.frame
    outcome = books.outcome_codes(frame, book.outcomes)
    status = books.status_codes(frame, book.outcomes)
    at_risk = frame["churn_probability"] * frame["MonthlyCharges"]
    return frame.assign(
        outcome=outcome, status=status, at_risk=at_risk.where(status == books.ACTIVE)
    )


def select(frame: pd.DataFrame, f: CustomerFilter) -> pd.DataFrame:
    """The rows of ``frame`` (from ``with_outcomes``) that ``f`` keeps, in book order."""
    mask = pd.Series(True, index=frame.index)
    if f.query:
        mask &= frame[schema.ID_COLUMN].str.upper().str.contains(f.query.upper(), regex=False)
    if f.bands is not None:
        mask &= frame["risk_band"].isin(f.bands)
    for column, value in (
        ("Contract", f.contract),
        ("InternetService", f.internet),
        ("PaymentMethod", f.payment),
        ("partition", f.partition),
    ):
        if value is not None:
            mask &= frame[column] == value
    if f.tenure_min is not None:
        mask &= frame["tenure"] >= f.tenure_min
    if f.tenure_max is not None:
        mask &= frame["tenure"] <= f.tenure_max
    if f.flagged is not None:
        mask &= frame["flagged"] == f.flagged
    if f.ids is not None:
        mask &= frame[schema.ID_COLUMN].isin(f.ids)
    if f.exclude_churned:
        mask &= frame["outcome"] != books.CHURNED
    return frame[mask]


def band_counts(frame: pd.DataFrame, f: CustomerFilter, names: Sequence[str]) -> dict[str, int]:
    """How many customers ``f`` keeps in each band, whichever bands it asks for.

    A band's own chip must say what choosing it would list, so its count does not
    depend on the bands chosen now. Every band is named, an empty one with 0.
    """
    bands = select(frame, replace(f, bands=None))["risk_band"]
    return {name: int((bands == name).sum()) for name in names}


def status_counts(selection: pd.DataFrame) -> dict[str, int]:
    """How many of ``selection`` are active, churned and unknown; they add up to its size."""
    return {
        status: int((selection["status"] == status).sum())
        for status in (books.ACTIVE, books.CHURNED, books.UNKNOWN)
    }


def ordered(frame: pd.DataFrame, sort: str, descending: bool) -> pd.DataFrame:
    """``frame`` sorted by ``sort`` (a ``SORT_COLUMNS`` key), ties broken by customer ID."""
    column = SORT_COLUMNS[sort]
    if column == schema.ID_COLUMN:
        return frame.sort_values(column, ascending=not descending, kind="mergesort")
    return frame.sort_values(
        [column, schema.ID_COLUMN], ascending=[not descending, True], kind="mergesort"
    )


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    """What the explorer's summary row says about the customers a filter keeps."""

    customers: int
    share_of_book: float | None
    flagged: int
    flagged_share: float | None
    mean_probability: float | None
    expected_churners: float
    #: Observed churn among the selection's training and validation customers.
    observed: Observed | None
    monthly_charges: float
    #: Of those recorded active: an unknown status is not counted as a paying customer.
    active_monthly_charges: float
    at_risk: float
    histogram: list[int]


def summarise(selection: pd.DataFrame, book_size: int, shown: pd.Series) -> SelectionSummary:
    """Totals over ``selection``; outcomes from its training and validation rows only."""
    n = len(selection)
    seen = books.labelled(selection, shown=shown)
    counts = np.bincount(
        histogram_bins(selection["churn_probability"]), minlength=HISTOGRAM_BINS
    ).tolist()
    return SelectionSummary(
        customers=n,
        share_of_book=_rate(n, book_size),
        flagged=int(selection["flagged"].sum()),
        flagged_share=_rate(selection["flagged"].sum(), n),
        mean_probability=float(selection["churn_probability"].mean()) if n else None,
        expected_churners=float(selection["churn_probability"].sum()),
        observed=_observed(seen) if len(seen) else None,
        monthly_charges=float(selection["MonthlyCharges"].sum()),
        active_monthly_charges=float(
            selection.loc[selection["status"] == books.ACTIVE, "MonthlyCharges"].sum()
        ),
        at_risk=float(selection["at_risk"].sum()),
        histogram=counts,
    )


#: The columns every customer export carries, in order.
EXPORT_COLUMNS: tuple[str, ...] = (
    schema.ID_COLUMN,
    "churn_probability",
    "risk_band",
    "flagged",
    "partition",
    "outcome",
    "status",
)


def export_frame(selection: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    """A selection as it is exported: identity, the model's inputs, its outputs, the outcome."""
    columns = [schema.ID_COLUMN, *feature_columns, *EXPORT_COLUMNS[1:]]
    return selection.loc[:, columns]


# --- example customers -------------------------------------------------------------

#: The cases the single-customer page shows side by side: (band, flagged).
EXAMPLE_CASES: tuple[tuple[str, bool], ...] = (
    ("Low", False),
    ("Moderate", False),
    ("Moderate", True),
    ("High", True),
)


def example_customers(book: books.Book) -> list[str]:
    """One validation customer per ``EXAMPLE_CASES`` entry: the group's median probability.

    A rule rather than a list of IDs, so no customer is named in the
    repository: the lower median of each group, ties broken by ID. Empty when
    the partitions are unverified, because "validation" then means nothing.
    """
    frame = book.frame[book.frame["partition"] == "validation"]
    chosen = []
    for band, flagged in EXAMPLE_CASES:
        group = frame[(frame["risk_band"] == band) & (frame["flagged"] == flagged)]
        if group.empty:
            continue
        group = group.sort_values(["churn_probability", schema.ID_COLUMN], kind="mergesort")
        chosen.append(str(group[schema.ID_COLUMN].iloc[(len(group) - 1) // 2]))
    return chosen


def collinearity(book: books.Book) -> float | None:
    """Pearson r of TotalCharges against tenure x MonthlyCharges, on the training partition.

    The figure ``make eda`` reports. Features only - no outcome is read - and
    only from verified training rows.
    """
    if book.train_index.empty:
        return None
    train = book.frame.loc[book.train_index]
    product = train["tenure"] * train["MonthlyCharges"]
    return float(np.corrcoef(train["TotalCharges"], product)[0, 1])
