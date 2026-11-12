"""What the dataset's training and validation partitions show, as aggregates only.

Every observed figure a page shows - a churn rate, a breakdown, the validation
sweep, the calibration bins - is *evidence* about the model, and comes from one
of two sources with one interface:

* ``LiveEvidence`` computes it from the dataset's own book, as the service
  always has. That needs the dataset beside the service.
* ``PublishedEvidence`` reads it from ``public_evidence.json``, which
  ``churnsense.demo.build`` wrote once, offline, from those very functions.
  A public demo serves from this: the dataset is not in its container at all.

The published file holds no customer. It is a closed format: every key is
named here, every count and rate is checked on the way in, and the rule that no
rate is given for fewer than ``MIN_CELL`` customers is enforced again on what
is read, so a file that breaks it is refused rather than served. It is bound to
the model, the dataset and the split it was computed for; anything else reads as
``DemoDataError``, which the service answers with 503 and never with other data.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from churnsense import insights
from churnsense.book import is_sha256
from churnsense.data import schema
from churnsense.evaluation.disclosure import MIN_CELL
from churnsense.evaluation.metrics import ClassificationMetrics
from churnsense.evaluation.threshold import SweepBasis
from churnsense.exceptions import DemoDataError
from churnsense.explainability.shap_explain import read_cache

if TYPE_CHECKING:
    from churnsense.book import Book
    from churnsense.config import Business
    from churnsense.models.predict import Predictor

logger = logging.getLogger(__name__)

#: The dataset's aggregates, published for a service that does not hold the dataset.
EVIDENCE_FILE = "public_evidence.json"
#: Attributions of generated demo customers (``churnsense.demo.build``).
DEMO_EXPLANATIONS_FILE = "demo_explanations.json"
#: Bump when the published file's layout changes: an older one is then refused.
FORMAT = 1
_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Ranking:
    """A feature ranking and the customers it was computed over."""

    ranking: list[dict[str, Any]]
    partition: str
    customers: int


def all_breakdowns() -> list[tuple[str, str | None]]:
    """Every breakdown a page can ask for: each dimension alone, then every ordered pair."""
    return [(rows, None) for rows in insights.DIMENSIONS] + list(
        permutations(insights.DIMENSIONS, 2)
    )


# --- computed from the dataset ---------------------------------------------------------


class LiveEvidence:
    """Evidence computed from the dataset's own book, each figure once."""

    def __init__(self, book: Book, model: Predictor, shap_cache: Path) -> None:
        self._book, self._model, self._shap_cache = book, model, shap_cache
        self._lock = threading.Lock()
        self._kept: dict[Any, Any] = {}

    def _once(self, key: Any, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._kept:
                return self._kept[key]
        value = compute()
        with self._lock:
            return self._kept.setdefault(key, value)

    @property
    def withheld(self) -> str | None:
        return self._book.withheld

    @property
    def dataset_customers(self) -> int:
        return len(self._book.frame)

    def observed(self) -> insights.Observed | None:
        return self._once("observed", lambda: insights.observed_overall(self._book))

    def band_observed(self) -> dict[str, insights.Observed | None]:
        return self._once("bands", lambda: insights.band_observed(self._book, self._model.config))

    def histogram_observed(self) -> list[tuple[insights.Observed | None, float | None]]:
        return self._once("histogram", lambda: insights.histogram_observed(self._book))

    def breakdown(self, rows: str, columns: str | None = None) -> insights.Breakdown | None:
        """Once per pair: protecting a table is the costly part."""
        return self._once(
            ("breakdown", rows, columns), lambda: insights.breakdown(self._book, rows, columns)
        )

    def lifecycle(self) -> insights.Lifecycle | None:
        return self._once("lifecycle", lambda: insights.lifecycle(self._book))

    def sweep_basis(self) -> SweepBasis | None:
        return self._once("basis", lambda: insights.validation_sweep_basis(self._book))

    def economics(self, business: Business) -> insights.Economics | None:
        return insights.economics(self._book, business)

    def validation_evaluation(self) -> insights.ValidationEvaluation | None:
        return self._once(
            "validation",
            lambda: insights.validation_evaluation(
                self._book, self._model.threshold, schema.VALIDATION_SEGMENTS
            ),
        )

    def collinearity(self) -> float | None:
        return self._once("collinearity", lambda: insights.collinearity(self._book))

    def importance(self) -> Ranking | None:
        """``make explain``'s ranking; read each time, so a re-run shows without a restart."""
        cached = read_cache(self._shap_cache, self._model.meta.get("model_sha256", ""))
        if cached is None:
            return None
        try:
            return Ranking(cached["importance"], cached["partition"], cached["n_explained"])
        except KeyError:
            logger.warning("the SHAP cache's importance ranking is malformed; ignoring it")
            return None


# --- published --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedEvidence:
    """Evidence read from the published file: the same answers, with no dataset behind them."""

    dataset_customers: int
    _observed: insights.Observed
    _bands: dict[str, insights.Observed]
    _histogram: list[tuple[insights.Observed | None, float | None]]
    _breakdowns: dict[tuple[str, str | None], insights.Breakdown]
    _lifecycle: insights.Lifecycle
    _basis: SweepBasis
    _validation: insights.ValidationEvaluation | None
    _collinearity: float | None
    _importance: Ranking | None

    #: A published file exists only for verified partitions.
    withheld: None = None

    def observed(self) -> insights.Observed:
        return self._observed

    def band_observed(self) -> dict[str, insights.Observed]:
        return self._bands

    def histogram_observed(self) -> list[tuple[insights.Observed | None, float | None]]:
        return self._histogram

    def breakdown(self, rows: str, columns: str | None = None) -> insights.Breakdown:
        return self._breakdowns[rows, columns]

    def lifecycle(self) -> insights.Lifecycle:
        return self._lifecycle

    def sweep_basis(self) -> SweepBasis:
        return self._basis

    def economics(self, business: Business) -> insights.Economics:
        return insights.economics_from(self._basis, business)

    def validation_evaluation(self) -> insights.ValidationEvaluation | None:
        return self._validation

    def collinearity(self) -> float | None:
        return self._collinearity

    def importance(self) -> Ranking | None:
        return self._importance


Evidence = LiveEvidence | PublishedEvidence


# --- writing ----------------------------------------------------------------------------


def identity_of(model: Predictor) -> dict[str, Any]:
    """What a published file is bound to: the model, its dataset and split, and the rules."""
    meta = model.meta
    return {
        "model_sha256": meta.get("model_sha256"),
        "data_sha256": meta.get("data_sha256"),
        "split_digest": meta.get("split_digest"),
        "threshold": model.threshold,
        "min_cell": MIN_CELL,
        "risk_bands": [[band.name, band.min, band.max] for band in model.config.risk_bands],
    }


def _plain(value: Any) -> Any:
    return None if value is None else asdict(value)


def published_body(live: LiveEvidence, model: Predictor) -> dict[str, Any]:
    """Everything ``PublishedEvidence`` answers, computed by ``live``, as JSON-ready values.

    Refused when the partitions are unverified: there is then nothing observed to publish.
    """
    if live.withheld is not None:
        raise DemoDataError(f"nothing can be published: {live.withheld}")
    validation = live.validation_evaluation()
    ranking = live.importance()
    body = {
        "format": FORMAT,
        "identity": identity_of(model),
        "dataset_customers": live.dataset_customers,
        "observed": _plain(live.observed()),
        "band_observed": {name: _plain(seen) for name, seen in live.band_observed().items()},
        "histogram_observed": [
            {"observed": _plain(seen), "mean": mean} for seen, mean in live.histogram_observed()
        ],
        "breakdowns": [_plain(live.breakdown(rows, columns)) for rows, columns in all_breakdowns()],
        "lifecycle": _plain(live.lifecycle()),
        "sweep_basis": _plain(live.sweep_basis()),
        "validation": None
        if validation is None
        else {
            "metrics": validation.metrics.as_dict(),
            "churn_rate": validation.churn_rate,
            "no_skill_accuracy": validation.no_skill_accuracy,
            "ece": validation.ece,
            "reliability": validation.reliability,
            "segments": validation.segments,
        },
        "collinearity": live.collinearity(),
        "importance": None if ranking is None else asdict(ranking),
    }
    # Parsed back before it is handed over: what is written is what will load.
    parse(json.loads(dumps(body)), model)
    return body


def dumps(body: dict[str, Any]) -> str:
    """The published text. NaN is refused: a figure that is not a number is not published."""
    return json.dumps(body, indent=1, allow_nan=False) + "\n"


# --- reading: a closed format, checked ---------------------------------------------------


def _fail(what: str) -> NoReturn:
    raise DemoDataError(f"the published evidence is malformed: {what}")


def _record(value: Any, keys: tuple[str, ...], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != set(keys):
        _fail(what)
    return value


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(what)
    return value


def _count(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(what)
    return value


def _number(value: Any, what: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        _fail(what)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail(what)
    return float(value)


def _share(value: Any, what: str) -> float:
    number = _number(value, what)
    if not 0.0 <= number <= 1.0:
        _fail(what)
    return number


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        _fail(what)
    return value


def _maybe(value: Any, read: Callable[[Any, str], Any], what: str) -> Any:
    return None if value is None else read(value, what)


def _rate_of(churned: Any, rate: Any, customers: int, what: str) -> tuple[int, float]:
    """A count and its rate over ``customers``: there, consistent, and allowed to be shown."""
    count, share = _count(churned, what), _share(rate, what)
    if customers < MIN_CELL:
        _fail(f"{what}: a rate for fewer than {MIN_CELL} customers")
    if count > customers or not math.isclose(share, count / customers, abs_tol=1e-9):
        _fail(what)
    return count, share


def _observed(value: Any, what: str) -> insights.Observed:
    record = _record(value, ("customers", "churned", "rate"), what)
    customers = _count(record["customers"], what)
    if record["churned"] is None and record["rate"] is None and customers < MIN_CELL:
        return insights.Observed(customers, None, None)
    churned, rate = _rate_of(record["churned"], record["rate"], customers, what)
    return insights.Observed(customers, churned, rate)


_CELL = tuple(field.name for field in fields(insights.Cell))
_HIDDEN = ("churned", "rate", "lift", "low", "high")


def _cell(value: Any, what: str) -> insights.Cell:
    record = _record(value, _CELL, what)
    row = _text(record["row"], what)
    column = _maybe(record["column"], _text, what)
    customers, reason = _count(record["customers"], what), record["reason"]
    if reason is not None:
        if reason not in ("too_few", "complementary") or (reason == "too_few") != (
            customers < MIN_CELL
        ):
            _fail(what)
        if any(record[key] is not None for key in _HIDDEN):
            _fail(f"{what}: a hidden group states its churn")
        return insights.Cell(row, column, customers, None, None, None, reason)
    churned, rate = _rate_of(record["churned"], record["rate"], customers, what)
    lift = _maybe(record["lift"], _number, what)
    low, high = _share(record["low"], what), _share(record["high"], what)
    if (lift is not None and lift < 0) or not low <= rate <= high:
        _fail(what)
    return insights.Cell(row, column, customers, churned, rate, lift, None, low, high)


def _cells(value: Any, labels: list[tuple[str, str | None]], what: str) -> list[insights.Cell]:
    """Cells for exactly ``labels``, in order: no level missing, none added."""
    cells = [_cell(item, what) for item in _list(value, what)]
    if [(cell.row, cell.column) for cell in cells] != labels:
        _fail(f"{what}: not the breakdown's own levels")
    return cells


_BREAKDOWN = tuple(field.name for field in fields(insights.Breakdown))
_TOP = 7


def _breakdown(value: Any) -> insights.Breakdown:
    record = _record(value, _BREAKDOWN, "a breakdown")
    rows, columns = record["rows"], record["columns"]
    what = f"breakdown {rows!r} x {columns!r}"
    if not isinstance(rows, str) or rows not in insights.DIMENSIONS:
        _fail("a breakdown of an unknown dimension")
    if columns is not None and (
        not isinstance(columns, str) or columns not in insights.DIMENSIONS or columns == rows
    ):
        _fail("a breakdown by an unknown dimension")
    row_levels = insights.DIMENSIONS[rows].levels
    column_levels = insights.DIMENSIONS[columns].levels if columns is not None else ()
    row_totals = _cells(record["row_totals"], [(level, None) for level in row_levels], what)
    column_totals = _cells(
        record["column_totals"], [(level, None) for level in column_levels], what
    )
    grid = (
        [(level, None) for level in row_levels]
        if columns is None
        else [(a, b) for a in row_levels for b in column_levels]
    )
    cells = _cells(record["cells"], grid, what)
    ranked = [_cell(item, what) for item in _list(record["ranked"], what)]
    if len(ranked) > _TOP or any(cell.rate is None or cell not in cells for cell in ranked):
        _fail(f"{what}: its ranking is not of its own reported groups")
    customers = _count(record["partition_customers"], what)
    churned = _count(record["partition_churned"], what)
    if churned > customers or sum(cell.customers for cell in row_totals) != customers:
        _fail(what)
    return insights.Breakdown(
        rows=rows,
        columns=columns,
        partition_customers=customers,
        partition_churned=churned,
        base_rate=_maybe(record["base_rate"], _share, what),
        row_totals=row_totals,
        column_totals=column_totals,
        cells=cells,
        ranked=ranked,
    )


def _breakdowns(value: Any) -> dict[tuple[str, str | None], insights.Breakdown]:
    found = {(b.rows, b.columns): b for b in map(_breakdown, _list(value, "breakdowns"))}
    if list(found) != all_breakdowns():
        _fail("not exactly the breakdowns a page can ask for, once each")
    return found


def _lifecycle(value: Any) -> insights.Lifecycle:
    what = "lifecycle"
    record = _record(value, tuple(f.name for f in fields(insights.Lifecycle)), what)
    labels = [(level, None) for level in insights.DIMENSIONS["tenure"].levels]
    return insights.Lifecycle(
        partition_customers=_count(record["partition_customers"], what),
        base_rate=_maybe(record["base_rate"], _share, what),
        buckets=_cells(record["buckets"], labels, what),
        median_tenure_churned=_maybe(record["median_tenure_churned"], _number, what),
        median_tenure_stayed=_maybe(record["median_tenure_stayed"], _number, what),
    )


def _histogram(value: Any) -> list[tuple[insights.Observed | None, float | None]]:
    what = "histogram"
    bins = _list(value, what)
    if len(bins) != insights.HISTOGRAM_BINS:
        _fail(what)
    out = []
    for item in bins:
        record = _record(item, ("observed", "mean"), what)
        seen = _maybe(record["observed"], _observed, what)
        mean = _maybe(record["mean"], _share, what)
        # A mean is drawn only where a rate is: never for a group too small to show.
        if mean is not None and (seen is None or seen.rate is None):
            _fail(what)
        out.append((seen, mean))
    return out


def _basis(value: Any) -> SweepBasis:
    what = "sweep basis"
    record = _record(value, tuple(f.name for f in fields(SweepBasis)), what)
    customers = _count(record["customers"], what)
    thresholds = [_share(t, what) for t in _list(record["thresholds"], what)]
    series = {
        name: [_count(v, what) for v in _list(record[name], what)]
        for name in ("true_positives", "false_positives", "false_negatives")
    }
    charges = [_number(v, what) for v in _list(record["flagged_churner_charges"], what)]
    size = len(thresholds)
    if size < 2 or any(len(values) != size for values in (*series.values(), charges)):
        _fail(what)
    if thresholds != sorted(set(thresholds)):
        _fail(f"{what}: thresholds must rise")
    tp, fp, fn = series["true_positives"], series["false_positives"], series["false_negatives"]
    churners = tp[0] + fn[0]
    for i in range(size):
        if tp[i] + fn[i] != churners or tp[i] + fp[i] > customers or charges[i] < 0:
            _fail(what)
        # Raising the cut flags nobody new.
        if i and (tp[i] > tp[i - 1] or fp[i] > fp[i - 1] or charges[i] > charges[i - 1] + 1e-6):
            _fail(what)
    return SweepBasis(
        customers=customers,
        thresholds=tuple(thresholds),
        true_positives=tuple(tp),
        false_positives=tuple(fp),
        false_negatives=tuple(fn),
        flagged_churner_charges=tuple(charges),
    )


_COUNTS = ("n", "positives", "tn", "fp", "fn", "tp")
_RELIABILITY = ("low", "high", "customers", "mean_predicted", "churned", "rate")
_SEGMENT = ("dimension", "segment", "customers", "flagged", *insights.SEGMENT_OUTCOMES)


def _validation(value: Any) -> insights.ValidationEvaluation:
    what = "validation"
    record = _record(
        value,
        ("metrics", "churn_rate", "no_skill_accuracy", "ece", "reliability", "segments"),
        what,
    )
    names = tuple(field.name for field in fields(ClassificationMetrics))
    raw = _record(record["metrics"], names, what)
    metrics = ClassificationMetrics(
        **{
            name: _count(raw[name], what) if name in _COUNTS else _number(raw[name], what)
            for name in names
        }
    )
    if metrics.n < MIN_CELL or metrics.tn + metrics.fp + metrics.fn + metrics.tp != metrics.n:
        _fail(what)

    reliability = []
    for item in _list(record["reliability"], what):
        row = _record(item, _RELIABILITY, what)
        customers = _count(row["customers"], what)
        out = {
            "low": _share(row["low"], what),
            "high": _share(row["high"], what),
            "customers": customers,
            "mean_predicted": _share(row["mean_predicted"], what),
            "churned": None,
            "rate": None,
        }
        if row["churned"] is not None or row["rate"] is not None:
            out["churned"], out["rate"] = _rate_of(row["churned"], row["rate"], customers, what)
        reliability.append(out)

    segments = []
    for item in _list(record["segments"], what):
        row = _record(item, _SEGMENT, what)
        customers = _count(row["customers"], what)
        shown = {key: row[key] for key in insights.SEGMENT_OUTCOMES if row[key] is not None}
        if shown and customers < MIN_CELL:
            _fail(f"{what}: an outcome for a segment of fewer than {MIN_CELL} customers")
        for key, figure in shown.items():
            if key == "unscanned":
                if not isinstance(figure, bool):
                    _fail(what)
            elif key == "churners":
                if _count(figure, what) > customers:
                    _fail(what)
            else:
                _share(figure, what)
        segments.append(
            row
            | {
                "dimension": _text(row["dimension"], what),
                "segment": _text(row["segment"], what),
                "flagged": _count(row["flagged"], what),
            }
        )
    return insights.ValidationEvaluation(
        metrics=metrics,
        churn_rate=_share(record["churn_rate"], what),
        no_skill_accuracy=_share(record["no_skill_accuracy"], what),
        ece=_share(record["ece"], what),
        reliability=reliability,
        segments=segments,
    )


def _importance(value: Any) -> Ranking:
    what = "importance"
    record = _record(value, ("ranking", "partition", "customers"), what)
    known = (
        set(schema.FEATURE_LABELS) | set(schema.NUMERIC_COLUMNS) | set(schema.ALLOWED_CATEGORIES)
    )
    ranking = []
    for item in _list(record["ranking"], what):
        row = _record(item, ("feature", "label", "mean_abs_shap", "share"), what)
        if row["feature"] not in known:
            _fail(what)
        ranking.append(
            {
                "feature": row["feature"],
                "label": _text(row["label"], what),
                "mean_abs_shap": _share(row["mean_abs_shap"], what),
                "share": _share(row["share"], what),
            }
        )
    customers = _count(record["customers"], what)
    if not ranking or customers < 1:
        _fail(what)
    return Ranking(ranking, _text(record["partition"], what), customers)


_TOP_LEVEL = (
    "format",
    "identity",
    "dataset_customers",
    "observed",
    "band_observed",
    "histogram_observed",
    "breakdowns",
    "lifecycle",
    "sweep_basis",
    "validation",
    "collinearity",
    "importance",
)


def parse(body: Any, model: Predictor) -> PublishedEvidence:
    """``body`` as evidence for ``model``, or ``DemoDataError``: nothing is taken on trust."""
    record = _record(body, _TOP_LEVEL, "its top-level keys")
    if record["format"] != FORMAT or isinstance(record["format"], bool):
        _fail("another format")
    expected = identity_of(model)
    if not all(is_sha256(expected[key]) for key in ("model_sha256", "data_sha256", "split_digest")):
        raise DemoDataError("the served model records no provenance to match the evidence to")
    if record["identity"] != expected:
        raise DemoDataError(
            "the published evidence is not of the served model, its dataset, its split, "
            "its threshold or the configured risk bands"
        )
    names = [band.name for band in model.config.risk_bands]
    bands = _record(record["band_observed"], tuple(names), "the risk bands")
    return PublishedEvidence(
        dataset_customers=_count(record["dataset_customers"], "dataset size"),
        _observed=_observed(record["observed"], "observed"),
        _bands={name: _observed(bands[name], f"band {name}") for name in names},
        _histogram=_histogram(record["histogram_observed"]),
        _breakdowns=_breakdowns(record["breakdowns"]),
        _lifecycle=_lifecycle(record["lifecycle"]),
        _basis=_basis(record["sweep_basis"]),
        _validation=_maybe(record["validation"], lambda value, _what: _validation(value), ""),
        _collinearity=_maybe(record["collinearity"], _number, "collinearity"),
        _importance=_maybe(record["importance"], lambda value, _what: _importance(value), ""),
    )


def load_published(path: Path, model: Predictor) -> PublishedEvidence:
    """The published evidence at ``path`` for ``model``; ``DemoDataError`` if it cannot be used."""
    try:
        if path.stat().st_size > _MAX_BYTES:
            raise DemoDataError("the published evidence is larger than any real one")
        body = json.loads(path.read_text(encoding="utf-8"))
    # ValueError covers JSONDecodeError and an integer past Python's digit limit;
    # RecursionError is what nesting deeper than the interpreter allows raises.
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise DemoDataError(
            f"the published evidence could not be read: {exc.__class__.__name__}"
        ) from exc
    return parse(body, model)
