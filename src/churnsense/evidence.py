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

Figures that split validation customers by score are published only where no
interval between two cuts holds fewer than ``MIN_CELL`` churners or stayers
(``churnsense.score_disclosure``): the sweep at safe cuts, the calibration bins
on those same cuts, segments and risk bands with complementary suppression, and
no validation histogram. The reader checks that rule again on its own.
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

from churnsense import (
    disclosure_audit,
    insights,
    joint_disclosure,
    legacy_disclosures,
    score_disclosure,
)
from churnsense.book import is_sha256, labelled
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
#: Bump when the published file's layout or rules change: an older one is then refused.
#: 3: cuts kept against the release reports' thresholds too; training groups shown only
#: with ``MIN_CELL`` churners and ``MIN_CELL`` stayers; Two year's training churn hidden.
FORMAT = 4
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
        self._gates: dict[Any, threading.Lock] = {}

    def _once(self, key: Any, compute: Callable[[], Any]) -> Any:
        """``compute()``'s value, computed once per key: a request that comes while another
        computes it waits for that one rather than computing it again (the training tables
        cost seconds of solving)."""
        with self._lock:
            if key in self._kept:
                return self._kept[key]
            gate = self._gates.setdefault(key, threading.Lock())
        with gate:
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

    #: The dataset's own book shows the whole sweep: it is not published.
    sweep_withheld: None = None

    def validation_scores(self) -> tuple[Any, Any, Any]:
        """The validation customers with outcomes, their outcomes and their scores."""
        rows = labelled(self._book.frame, "validation", shown=self._book.outcomes)
        return rows, rows[schema.TARGET].to_numpy(), rows["churn_probability"].to_numpy()

    def observed(self) -> insights.Observed | None:
        return self._once("observed", lambda: insights.observed_overall(self._book))

    def band_observed(self) -> dict[str, insights.Observed | None]:
        return self._once("bands", lambda: insights.band_observed(self._book, self._model.config))

    def histogram_observed(self) -> list[tuple[insights.Observed | None, float | None]]:
        return self._once("histogram", lambda: insights.histogram_observed(self._book))

    def _tables(self) -> dict[tuple[str, str | None], insights.Breakdown] | None:
        """Every breakdown, once: the tables are protected together, and that is the costly
        part. ``None`` (withheld) when no hiding keeps the rule - never a table guarded on
        its own instead."""

        def build() -> dict[tuple[str, str | None], insights.Breakdown] | None:
            try:
                return insights.breakdowns(self._book)
            except joint_disclosure.Unprotectable as exc:
                logger.warning("training tables withheld: %s", exc)
                self._kept.setdefault("unprotectable", str(exc))
                return None

        return self._once("breakdowns", build)

    @property
    def unprotectable(self) -> str | None:
        """Why the training tables are withheld, when no hiding keeps the rule."""
        self._tables()
        return self._kept.get("unprotectable")

    def breakdown(self, rows: str, columns: str | None = None) -> insights.Breakdown | None:
        insights.DIMENSIONS[rows]
        if columns is not None:
            insights.DIMENSIONS[columns]
        tables = self._tables()
        return None if tables is None else tables[(rows, columns)]

    def lifecycle(self) -> insights.Lifecycle | None:
        tables = self._tables()
        if tables is None:
            return None
        return self._once("lifecycle", lambda: insights.lifecycle(self._book, tables))

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
    _breakdowns: dict[tuple[str, str | None], insights.Breakdown]
    _lifecycle: insights.Lifecycle
    _basis: SweepBasis | None
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
        """Not published: validation customers per 0.05 of score would cut the sweep finer."""
        return [(None, None)] * insights.HISTOGRAM_BINS

    def breakdown(self, rows: str, columns: str | None = None) -> insights.Breakdown:
        return self._breakdowns[rows, columns]

    def lifecycle(self) -> insights.Lifecycle:
        return self._lifecycle

    def sweep_basis(self) -> SweepBasis | None:
        return self._basis

    @property
    def sweep_withheld(self) -> str | None:
        """Why no sweep is published, or ``None`` when one is."""
        return score_disclosure.SWEEP_WITHHELD if self._basis is None else None

    def economics(self, business: Business) -> insights.Economics | None:
        return None if self._basis is None else insights.economics_from(self._basis, business)

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
    if live.unprotectable is not None:
        raise DemoDataError(
            f"no publication of the training tables keeps the rule: {live.unprotectable}"
        )
    basis, validation, cells = _validation_on_safe_cuts(live, model)
    ranking = live.importance()
    body = {
        "format": FORMAT,
        "identity": identity_of(model),
        "dataset_customers": live.dataset_customers,
        "observed": _plain(live.observed()),
        "band_observed": {
            name: _plain(seen)
            for name, seen in score_disclosure.guard_bands(
                live.band_observed(), live.observed()
            ).items()
        },
        "breakdowns": [_plain(live.breakdown(rows, columns)) for rows, columns in all_breakdowns()],
        "lifecycle": _plain(live.lifecycle()),
        "sweep_basis": _plain(basis),
        "validation": validation,
        "collinearity": live.collinearity(),
        "importance": None if ranking is None else asdict(ranking),
    }
    # The training tables guarded again from what they publish, as an outsider reads them.
    insights.guard_published(body["breakdowns"], body["lifecycle"])
    released = legacy_disclosures.describes(body["identity"])
    stated = _stated_by_reports(live.sweep_basis()) if released and basis is not None else None
    return audited(body, model, cells, stated, released)


def _stated_by_reports(fine: SweepBasis | None) -> dict[float, tuple[int, int]] | None:
    """The release reports' thresholds read exactly off the full sweep: the outsider's best.

    The reports' sweep is transcribed by hand (``legacy_disclosures.LEGACY_SWEEP``), and the
    cut rule judges pieces by it: it must be this sweep as the reports print it - the same
    customers flagged, a churner count the printed precision and recall allow - or the rule
    and this audit would judge different pieces. Refused otherwise.
    """
    if fine is None:
        return None
    out = {}
    for point in legacy_disclosures.LEGACY_SWEEP:
        i = score_disclosure._index(fine.thresholds, point.threshold)
        if i is None:
            raise DemoDataError(
                f"the full sweep has no cut at {point.threshold}, where the release reports state it"
            )
        flagged, churners = fine.true_positives[i] + fine.false_positives[i], fine.true_positives[i]
        try:
            low, high = legacy_disclosures.churner_range(point, fine.churners)
        except ValueError:
            low, high = 1, 0
        if flagged != point.flagged or not low <= churners <= high:
            raise DemoDataError(
                f"the release reports' sweep at {point.threshold} is not the released model's: "
                f"they state {point.flagged} flagged at precision {point.precision} and recall "
                f"{point.recall}, the sweep has {flagged} flagged and {churners} churners"
            )
        out[point.threshold] = (flagged, churners)
    return out


def audited(
    body: dict[str, Any],
    model: Predictor,
    cells: dict[Any, Any] | None,
    stated: dict[float, tuple[int, int]] | None,
    released: bool,
) -> dict[str, Any]:
    """``body``, once it loads and an outsider can solve no small group from it - read with
    the release reports' figures (``stated``) and, for the released model, with the training
    tables, the test report and the dataset's public totals; else ``DemoDataError``."""
    # Parsed back before it is handed over: what is written is what will load.
    written = json.loads(dumps(body))
    parse(written, model)
    again = json.loads(dumps(body))
    if insights.guard_published(again["breakdowns"], again["lifecycle"]):
        raise DemoDataError("a training table would hide more than it does; nothing was written")
    # And read as an outsider would read it, every published figure against every other.
    bands = [(band.name, band.min, band.max) for band in model.config.risk_bands]
    found = disclosure_audit.published_violations(
        written, bands, cells, legacy=stated, external=released
    )
    if released and stated is None and written["sweep_basis"] is not None:
        raise DemoDataError(
            "the release reports' thresholds could not be read; nothing was written"
        )
    if found:
        raise DemoDataError(
            f"{len(found)} group(s) of fewer than {MIN_CELL} could be solved for from the "
            f"published figures; nothing was written. First: {found[0]}"
        )
    return body


def legacy_for(identity: dict[str, Any], churners: int) -> score_disclosure.Legacy | None:
    """The thresholds a cut must be read together with, or ``None``.

    The release reports state the sweep of the *released* model's scores: for evidence of
    that model, on its dataset and split, they are read as an outsider reads them. Of any
    other model the reports say nothing that relates to its scores - a threshold of one
    model's scores is no threshold of another's - so nothing is added.
    """
    if legacy_disclosures.describes(identity):
        return score_disclosure.published_legacy(churners)
    return None


def _validation_on_safe_cuts(
    live: LiveEvidence, model: Predictor
) -> tuple[SweepBasis | None, dict[str, Any] | None, dict[Any, Any] | None]:
    """The sweep at safe cuts, and the validation figures read on those cuts.

    Nothing at all when no cuts keep the rule: every figure at the threshold
    is a cut of its own, and so is published only beside a safe sweep.
    """
    fine = live.sweep_basis()
    if fine is None:
        return None, None, None
    # The documents (model card, README) state the full sweep's peak and figures at 0.5: a
    # cut they use must be a cut here too, or the two together cut finer than the rule.
    peak = insights.economics_from(fine, model.config.business).peak.threshold
    cuts = score_disclosure.safe_cuts(
        fine,
        required=(model.threshold,),
        preferred=(peak, score_disclosure.AT_HALF),
        legacy=legacy_for(identity_of(model), fine.churners),
    )
    if cuts is None:
        return None, None, None
    basis = score_disclosure.restrict(fine, cuts)
    validation = live.validation_evaluation()
    if validation is None:
        return basis, None, None
    rows, y, p = live.validation_scores()
    present = [column for column in schema.VALIDATION_SEGMENTS if column in rows.columns]
    cells = score_disclosure.segment_cells(y, p, rows, present, model.threshold)
    segments = score_disclosure.guard_segments(validation.segments, cells)
    identical = score_disclosure.identical_segments(rows, present)
    if mixed := score_disclosure.betrayed(segments, identical):
        raise DemoDataError(f"a segment hidden under one name is shown under another: {mixed[0]}")
    return (
        basis,
        {
            "metrics": validation.metrics.as_dict(),
            "churn_rate": validation.churn_rate,
            "no_skill_accuracy": validation.no_skill_accuracy,
            "ece": validation.ece,
            "reliability": score_disclosure.reliability_on(y, p, cuts),
            "segments": segments,
        },
        cells,
    )


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
_REASONS = ("too_few", "cross_partition", "withheld")
_HIDDEN = ("churned", "rate", "lift", "low", "high")


def _cell(value: Any, what: str) -> insights.Cell:
    record = _record(value, _CELL, what)
    row = _text(record["row"], what)
    column = _maybe(record["column"], _text, what)
    customers, reason = _count(record["customers"], what), record["reason"]
    if reason is not None:
        if reason not in _REASONS or (reason == "too_few") != (customers < MIN_CELL):
            _fail(what)
        if any(record[key] is not None for key in _HIDDEN):
            _fail(f"{what}: a hidden group states its churn")
        return insights.Cell(row, column, customers, None, None, None, reason)
    churned, rate = _rate_of(record["churned"], record["rate"], customers, what)
    if not score_disclosure.two_sided(churned, customers - churned):
        _fail(f"{what}: a shown group has fewer than {MIN_CELL} churners or stayers")
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
    _crossed_hidden(rows, columns, [*row_totals, *column_totals, *cells], what)
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


def _crossed_hidden(rows: str, columns: str | None, cells: list[insights.Cell], what: str) -> None:
    """Every group of a ``CROSS_PARTITION`` level keeps its churn hidden, in every table."""
    for cell in cells:
        for key, level in ((rows, cell.row), (columns, cell.column)):
            if (
                key is not None
                and level in insights.CROSS_PARTITION.get(key, ())
                and cell.customers
                and cell.rate is not None
            ):
                _fail(f"{what}: {level}'s churn is shown")
        # A margin cell names its level in ``row`` whichever dimension it belongs to.
        if cell.column is None and cell.rate is not None and cell.customers:
            for key in (rows, columns):
                if key is not None and cell.row in insights.CROSS_PARTITION.get(key, ()):
                    _fail(f"{what}: {cell.row}'s churn is shown")


def _breakdowns(value: Any) -> dict[tuple[str, str | None], insights.Breakdown]:
    found = {(b.rows, b.columns): b for b in map(_breakdown, _list(value, "breakdowns"))}
    if list(found) != all_breakdowns():
        _fail("not exactly the breakdowns a page can ask for, once each")
    return found


def _consistent(
    tables: dict[tuple[str, str | None], insights.Breakdown], lifecycle: insights.Lifecycle
) -> insights.Lifecycle:
    """``lifecycle``, once every table tells the same story: one partition, a two-way
    table's margins its dimensions' own one-way tables, its cells adding up to them, both
    orientations of a pair alike, the same customers shown or hidden alike wherever they
    appear, and the lifecycle the tenure table. The tables are guarded as one system
    (``churnsense.joint_disclosure``), which holds only if they are one."""
    first = next(iter(tables.values()))
    partition = (first.partition_customers, first.partition_churned, first.base_rate)
    for (rows, columns), table in tables.items():
        what = f"breakdown {rows!r} x {columns!r}"
        if (table.partition_customers, table.partition_churned, table.base_rate) != partition:
            _fail(f"{what}: another partition than the other tables'")
        if columns is None:
            continue
        if table.row_totals != tables[(rows, None)].row_totals or (
            table.column_totals != tables[(columns, None)].row_totals
        ):
            _fail(f"{what}: its margins are not its dimensions' own tables")
        at = {(c.row, c.column): c for c in table.cells}
        other = {(c.column, c.row): c for c in tables[(columns, rows)].cells}
        for key, cell in at.items():
            twin = other[key]
            if (cell.customers, cell.churned, cell.rate, cell.reason) != (
                twin.customers,
                twin.churned,
                twin.rate,
                twin.reason,
            ):
                _fail(f"{what}: the two orientations of the pair disagree")
        for margins, line in ((table.row_totals, 0), (table.column_totals, 1)):
            for margin in margins:
                members = [c for key, c in at.items() if key[line] == margin.row]
                if sum(c.customers for c in members) != margin.customers:
                    _fail(f"{what}: its cells do not add up to its margins")
                every = margin.rate is not None and all(c.rate is not None for c in members)
                if every and sum(c.churned or 0 for c in members) != margin.churned:
                    _fail(f"{what}: its cells' churn does not add up to its margins'")
    if lifecycle.buckets != tables[("tenure", None)].row_totals or (
        lifecycle.partition_customers != partition[0]
    ):
        _fail("lifecycle: not the tenure table")
    try:
        system = insights.joint_system(tables, lifecycle)
    except ValueError as exc:
        _fail(f"the tables count the same customers differently: {exc}")
    if system.mixed:
        _fail(
            f"the same customers' churn is shown in one table and hidden in another: {system.mixed[0]}"
        )
    return lifecycle


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


def _bands(
    value: Any, names: list[str], overall: insights.Observed
) -> dict[str, insights.Observed]:
    """The risk bands' observed churn: each shown band, and the hidden ones together, keep the
    two-sided rule; they add up to ``overall``, so what is hidden is ``overall`` less the rest."""
    record = _record(value, tuple(names), "the risk bands")
    bands: dict[str, insights.Observed] = {}
    for name in names:
        what = f"band {name}"
        band = _record(record[name], ("customers", "churned", "rate"), what)
        customers = _count(band["customers"], what)
        if band["churned"] is None and band["rate"] is None:
            bands[name] = insights.Observed(customers, None, None)
            continue
        churned, rate = _rate_of(band["churned"], band["rate"], customers, what)
        if not score_disclosure.two_sided(churned, customers - churned):
            _fail(f"{what}: fewer than {MIN_CELL} churners or stayers")
        bands[name] = insights.Observed(customers, churned, rate)
    shown = [band for band in bands.values() if band.churned is not None]
    if sum(band.customers for band in bands.values()) != overall.customers:
        _fail("the risk bands do not add up to the customers observed")
    if len(shown) < len(bands):
        if overall.churned is None:
            if shown:
                _fail("a band's churn is shown where the total's is not")
        else:
            customers = overall.customers - sum(band.customers for band in shown)
            churned = overall.churned - sum(band.churned or 0 for band in shown)
            if churned < 0 or not score_disclosure.two_sided(churned, customers - churned):
                _fail("the hidden risk bands can be solved for")
    elif overall.churned is not None and sum(b.churned or 0 for b in shown) != overall.churned:
        _fail("the risk bands do not add up to the churn observed")
    return bands


def _basis(value: Any, identity: Any) -> SweepBasis:
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
        if tp[i] + fn[i] != churners or charges[i] < 0:
            _fail(what)
        # Raising the cut adds no charges. (Nor customers: a step that gained some would
        # hold a negative count, which ``holds`` below refuses.)
        if i and charges[i] > charges[i - 1] + 1e-6:
            _fail(what)
    basis = SweepBasis(
        customers=customers,
        thresholds=tuple(thresholds),
        true_positives=tuple(tp),
        false_positives=tuple(fp),
        false_negatives=tuple(fn),
        flagged_churner_charges=tuple(charges),
    )
    # The first cut flags everyone: every later one flags fewer (``holds``), none more.
    if fn[0] != 0 or tp[0] + fp[0] != customers:
        _fail(f"{what}: its first cut does not flag every customer")
    if not score_disclosure.holds(basis):
        _fail(
            f"{what}: it does not start with everyone, or a step holds fewer than {MIN_CELL} churners or stayers"
        )
    # Of the released model on its dataset and split, the release reports state the sweep at
    # thresholds of their own: read together with them, no piece may break the rule either.
    if legacy_disclosures.describes(identity) and not score_disclosure.legacy_holds(
        basis, score_disclosure.published_legacy(churners)
    ):
        _fail(f"{what}: read with the release reports' thresholds, a piece breaks the rule")
    return basis


_COUNTS = ("n", "positives", "tn", "fp", "fn", "tp")
_RELIABILITY = ("low", "high", "customers", "mean_predicted", "churned", "rate")
_SEGMENT = ("dimension", "segment", "customers", "flagged", *insights.SEGMENT_OUTCOMES)


def _validation(
    value: Any, basis: SweepBasis | None, threshold: float
) -> insights.ValidationEvaluation:
    """The validation figures, each read off the published sweep's own cuts."""
    what = "validation"
    if basis is None:
        _fail("validation figures without a sweep to read them on")
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
    _at_the_threshold(metrics, basis, threshold)
    reliability = _reliability(record["reliability"], basis)
    segments = _segments(record["segments"], metrics)
    return insights.ValidationEvaluation(
        metrics=metrics,
        churn_rate=_share(record["churn_rate"], what),
        no_skill_accuracy=_share(record["no_skill_accuracy"], what),
        ece=_share(record["ece"], what),
        reliability=reliability,
        segments=segments,
    )


def _at_the_threshold(metrics: ClassificationMetrics, basis: SweepBasis, threshold: float) -> None:
    """The operating point is the sweep at the served threshold (``parse`` has made sure it
    is a cut), and its rates are its own counts'."""
    if not math.isclose(metrics.threshold, threshold, abs_tol=1e-9):
        _fail("validation: the operating point is not at the served threshold")
    i = _cut_index(basis, threshold)
    tp, fp, fn = basis.true_positives[i], basis.false_positives[i], basis.false_negatives[i]
    n, tn = basis.customers, basis.customers - tp - fp - fn
    expected = (n, basis.churners, tp, fp, fn, tn)
    found = (metrics.n, metrics.positives, metrics.tp, metrics.fp, metrics.fn, metrics.tn)
    if found != expected:
        _fail("validation: the operating point is not the sweep at the threshold")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    rates = {
        "accuracy": (tp + tn) / n,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }
    if any(not math.isclose(getattr(metrics, k), v, abs_tol=1e-12) for k, v in rates.items()):
        _fail("validation: the operating point's rates are not its counts'")


def _cut_index(basis: SweepBasis, threshold: float) -> int:
    for i, t in enumerate(basis.thresholds):
        if math.isclose(t, threshold, abs_tol=1e-9):
            return i
    _fail("the served threshold is not a cut of the published sweep")


def _reliability(value: Any, basis: SweepBasis) -> list[dict[str, Any]]:
    """Calibration bins: exactly the intervals between the sweep's cuts, nothing finer."""
    what = "validation reliability"
    steps = score_disclosure.intervals(basis)
    items = _list(value, what)
    if len(items) != len(steps):
        _fail(f"{what}: not the sweep's intervals")
    out = []
    for item, (low, high, churners, stayers) in zip(items, steps, strict=True):
        row = _record(item, _RELIABILITY, what)
        customers = _count(row["customers"], what)
        bin_ = {
            "low": _share(row["low"], what),
            "high": _share(row["high"], what),
            "customers": customers,
            "mean_predicted": _share(row["mean_predicted"], what),
        }
        churned, rate = _rate_of(row["churned"], row["rate"], customers, what)
        if (
            not math.isclose(bin_["low"], low, abs_tol=1e-9)
            or not math.isclose(bin_["high"], high, abs_tol=1e-9)
            or (customers, churned) != (churners + stayers, churners)
        ):
            _fail(f"{what}: not the sweep's intervals")
        if not bin_["low"] <= bin_["mean_predicted"] <= bin_["high"]:
            _fail(what)
        out.append(bin_ | {"churned": churned, "rate": rate})
    return out


def _segment_cells(row: dict[str, Any], what: str) -> score_disclosure.Cells:
    """A shown segment's four counts at the threshold, read back from its own figures."""
    customers, flagged = row["customers"], row["flagged"]
    churners = _count(row["churners"], what)
    recall, precision = row["recall"], row["precision"]
    tp = 0 if churners == 0 else round(_share(recall, what) * churners)
    if flagged and (precision is None or not math.isclose(_share(precision, what), tp / flagged)):
        _fail(what)
    fp, fn = flagged - tp, churners - tp
    tn = customers - flagged - fn
    # A negative count breaks the two-sided rule its caller checks next.
    if churners and not math.isclose(recall, tp / churners):
        _fail(what)
    return tp, fp, fn, tn


def _segments(value: Any, metrics: ClassificationMetrics) -> list[dict[str, Any]]:
    """Segments at the threshold: every shown one, and the hidden ones of each dimension
    together, keep the rule on both sides of the cut."""
    what = "validation segments"
    segments = []
    shown_cells: dict[str, list[score_disclosure.Cells]] = {}
    totals: dict[str, tuple[int, int]] = {}
    for item in _list(value, what):
        row = _record(item, _SEGMENT, what)
        customers = _count(row["customers"], what)
        flagged = _count(row["flagged"], what)
        dimension, segment = _text(row["dimension"], what), _text(row["segment"], what)
        shown = {key: row[key] for key in insights.SEGMENT_OUTCOMES if row[key] is not None}
        for key, figure in shown.items():
            if key == "unscanned":
                if not isinstance(figure, bool):
                    _fail(what)
            elif key == "churners":
                if _count(figure, what) > customers:
                    _fail(what)
            else:
                _share(figure, what)
        if shown and not (
            math.isclose(
                _share(row["churn_rate"], what), row["churners"] / customers, abs_tol=1e-12
            )
            and math.isclose(_share(row["flagged_rate"], what), flagged / customers, abs_tol=1e-12)
        ):
            _fail(f"{what}: a rate that is not its counts'")
        seen = totals.get(dimension, (0, 0))
        totals[dimension] = (seen[0] + customers, seen[1] + flagged)
        cells = shown_cells.setdefault(dimension, [])
        if shown:
            cells.append(c := _segment_cells(row, what))
            if not (score_disclosure.two_sided(*c[:2]) and score_disclosure.two_sided(*c[2:])):
                _fail(f"{what}: a shown segment has fewer than {MIN_CELL} churners or stayers")
        segments.append(row | {"dimension": dimension, "segment": segment, "flagged": flagged})
    everyone = (metrics.tp, metrics.fp, metrics.fn, metrics.tn)
    for dimension, (customers, flagged) in totals.items():
        if (customers, flagged) != (metrics.n, metrics.tp + metrics.fp):
            _fail(f"{what}: {dimension} does not add up to the partition")
        rest = [a - sum(c[k] for c in shown_cells[dimension]) for k, a in enumerate(everyone)]
        if min(rest) < 0 or not (
            score_disclosure.two_sided(rest[0], rest[1])
            and score_disclosure.two_sided(rest[2], rest[3])
        ):
            _fail(f"{what}: the hidden segments of {dimension} can be solved for")
    return segments


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
    if type(record["format"]) is not int or record["format"] != FORMAT:
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
    observed = _observed(record["observed"], "observed")
    basis = _maybe(record["sweep_basis"], lambda value, _what: _basis(value, expected), "")
    if basis is not None:
        _cut_index(basis, model.threshold)  # the overview's campaign is read at it
    return PublishedEvidence(
        dataset_customers=_count(record["dataset_customers"], "dataset size"),
        _observed=observed,
        _bands=_bands(record["band_observed"], names, observed),
        _breakdowns=(tables := _breakdowns(record["breakdowns"])),
        _lifecycle=_consistent(tables, _lifecycle(record["lifecycle"])),
        _basis=basis,
        _validation=_maybe(
            record["validation"],
            lambda value, _what: _validation(value, basis, model.threshold),
            "",
        ),
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
