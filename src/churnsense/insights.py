"""What the dashboard pages show about the book, computed once, here.

Pure functions over a ``churnsense.book.Book``: no IO, no web framework, no
plotting. The API serialises their results and the browser only draws them, so
a figure on a page and a figure in a test come from the same code.

Two partitions, two purposes, and every function says which it reads:

* **train + validation** - the outcomes ``churnsense.book`` allows a page to
  show, for "does a probability hold up against what happened";
* **train only** - the descriptive patterns, read off the partition every
  modelling decision was taken on, exactly as ``make eda`` reads it.

The test partition's outcomes are never reached: every outcome comes through
``book.labelled``, which refuses it. All-row figures are scores and charges,
which every customer has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from churnsense import analysis as an
from churnsense import book as books
from churnsense.data import schema
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
    reliability_table,
    segment_table,
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
#: Fewer customers than this, and no churn rate or churn count is reported for
#: the group: a rate over a handful of people is noise, not a finding. A
#: reliability threshold for what a page presents - not disclosure control:
#: training and validation customers' own outcomes are listed one by one.
MIN_CELL = 20
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
    #: Observed churn among this band's training and validation customers.
    observed: Observed | None


@dataclass(frozen=True, slots=True)
class HistogramBin:
    low: float
    high: float
    band: str
    customers: int
    monthly_charges: float
    #: Training and validation customers in the bin, and how many churned.
    observed: Observed | None


@dataclass(frozen=True, slots=True)
class Cell:
    """One level (or pair of levels) of a breakdown. Small cells carry no rate."""

    row: str
    column: str | None
    customers: int
    churned: int | None
    rate: float | None
    #: ``rate`` relative to the partition's overall churn rate.
    lift: float | None

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

    Observed churn counts training and validation customers only, so a bin's
    observed rate can be read against its probability range: the calibration
    check, bin by bin.
    """
    frame = book.frame
    bins = histogram_bins(frame["churn_probability"])
    seen = books.labelled(frame, shown=book.outcomes)
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
                observed=_observed(seen[seen_bins == i]) if len(seen) else None,
            )
        )
    return out


def high_or_critical(book: books.Book, cfg: Config) -> Share:
    """Customers in the two highest bands, and their monthly charges."""
    top = _band_names(cfg)[-2:]
    rows = book.frame[book.frame["risk_band"].isin(top)]
    return Share(len(rows), _rate(len(rows), len(book.frame)), float(rows["MonthlyCharges"].sum()))


def flagged_share(book: books.Book) -> Share:
    rows = book.frame[book.frame["flagged"]]
    return Share(len(rows), _rate(len(rows), len(book.frame)), float(rows["MonthlyCharges"].sum()))


def observed_overall(book: books.Book) -> Observed | None:
    """Observed churn over every customer whose outcome may be shown."""
    seen = books.labelled(book.frame, shown=book.outcomes)
    return _observed(seen) if len(seen) else None


# --- breakdowns of the training partition --------------------------------------


def breakdown(
    book: books.Book, rows: str, columns: str | None = None, *, top: int = 7
) -> Breakdown | None:
    """A one- or two-way breakdown of the training partition, small cells suppressed.

    ``None`` when no outcome may be shown. ``rows`` and ``columns`` are keys of
    ``DIMENSIONS``; anything else raises ``KeyError``. Every combination of
    levels is returned, an empty one with zero customers.
    """
    row_dim = DIMENSIONS[rows]
    col_dim = DIMENSIONS[columns] if columns is not None else None
    train = books.labelled(book.frame, "train", shown=book.outcomes)
    if train.empty:
        return None
    base = _rate(train[schema.TARGET].sum(), len(train))

    def cells(dim_a: Dimension, dim_b: Dimension | None) -> list[Cell]:
        table = an.cross_tab(
            train, dim_a.column, dim_b.column if dim_b else None, min_cell=MIN_CELL
        )
        keys = [dim_a.column] if dim_b is None else [dim_a.column, dim_b.column]
        found = {tuple(str(v) for v in row[keys]): row for _, row in table.iterrows()}
        combos = (
            [(a,) for a in dim_a.levels]
            if dim_b is None
            else [(a, b) for a in dim_a.levels for b in dim_b.levels]
        )
        return [_cell(combo, found.get(combo), base) for combo in combos]

    grid = cells(row_dim, col_dim)
    ranked = sorted((c for c in grid if c.rate is not None), key=lambda c: -c.rate)[:top]
    return Breakdown(
        rows=rows,
        columns=columns,
        partition_customers=len(train),
        partition_churned=int(train[schema.TARGET].sum()),
        base_rate=base,
        row_totals=cells(row_dim, None),
        column_totals=cells(col_dim, None) if col_dim else [],
        cells=grid,
        ranked=ranked,
    )


def _cell(combo: tuple[str, ...], found: pd.Series | None, base: float | None) -> Cell:
    customers = 0 if found is None else int(found["customers"])
    reported = found is not None and customers >= MIN_CELL
    rate = float(found["churn_rate"]) if reported else None
    return Cell(
        row=combo[0],
        column=combo[1] if len(combo) > 1 else None,
        customers=customers,
        churned=int(found["churned"]) if reported else None,
        rate=rate,
        lift=rate / base if rate is not None and base else None,
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


def with_outcomes(book: books.Book) -> pd.DataFrame:
    """The book's frame plus each row's outcome code and its monthly revenue at risk."""
    frame = book.frame
    return frame.assign(
        outcome=books.outcome_codes(frame, book.outcomes),
        at_risk=frame["churn_probability"] * frame["MonthlyCharges"],
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
    return frame[mask]


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
