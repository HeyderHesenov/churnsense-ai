"""An outsider's arithmetic on what is published: what can be solved for?

Every published count over customers split by score is a linear function of the
counts in the *atoms*: the intervals between all edges anything publishes, per
partition (training, validation) and outcome. An outsider can compute exactly
the linear combinations that lie in the span of what is published - differences
of cumulative counts, a total less the shown groups, a band less a validation
interval. ``score_violations`` lists every set of atoms (each partition, and both
together) whose churners or stayers can be computed that way and break the rule:
neither none nor at least ``MIN_CELL``. With few atoms every set is tried, so a
union of intervals that are not neighbours is tried too; with many (a 0.01 grid)
every run of neighbouring atoms. ``segment_violations`` does the same for every
union of a dimension's segments on each side of the threshold.

This finds what can be solved for *exactly*. Counts still bound one another - a
segment's flagged customers bound its flagged churners - and that is not a
disclosure this checks; nor is any of it differential privacy or anonymity.

``published_violations`` runs both on a file as ``churnsense.evidence`` writes it,
so the builder refuses to write one an outsider could solve a small group from.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from churnsense.evaluation.disclosure import MIN_CELL

VALIDATION, TRAINING = "validation", "train"
CHURNED, STAYED = 1, 0
TOP = 1.0
#: Up to this many atoms per partition, every set of them is tried.
EVERY_SET_UP_TO = 16

Cells = tuple[int, int, int, int]


@dataclass(frozen=True)
class Fact:
    """A published count: customers of ``partitions`` with outcome in ``outcomes`` and a
    score in ``[low, high)`` (``high`` at or above 1 reaches the top, 1 included)."""

    partitions: frozenset[str]
    outcomes: frozenset[int]
    low: float
    high: float
    value: int


def at_or_above(threshold: float, outcome: int, value: int) -> Fact:
    return Fact(frozenset({VALIDATION}), frozenset({outcome}), threshold, 2.0, value)


def between(
    partitions: Iterable[str], outcomes: Iterable[int], low: float, high: float, value: int
) -> Fact:
    return Fact(frozenset(partitions), frozenset(outcomes), low, high, value)


def _edges(facts: Iterable[Fact]) -> list[float]:
    edges = {0.0}
    for fact in facts:
        edges.add(round(fact.low, 9))
        if fact.high < TOP:
            edges.add(round(fact.high, 9))
    return sorted(edges)


def _index(edges: list[float]) -> dict[tuple[int, str, int], int]:
    atoms = [(k, p, o) for k in range(len(edges)) for p in (VALIDATION, TRAINING) for o in (1, 0)]
    return {atom: i for i, atom in enumerate(atoms)}


def _row(fact: Fact, edges: list[float], index: dict) -> np.ndarray:
    row = np.zeros(len(index))
    for k, low in enumerate(edges):
        if low >= round(fact.low, 9) - 1e-12 and (fact.high >= TOP or low < round(fact.high, 9)):
            for p in fact.partitions:
                for o in fact.outcomes:
                    row[index[k, p, o]] = 1.0
    return row


def true_atoms(
    edges: list[float], scores: Mapping[str, tuple[np.ndarray, np.ndarray]], index: dict
) -> np.ndarray:
    """The book's own count in every atom: ``scores`` maps a partition to (outcomes, scores)."""
    x = np.zeros(len(index))
    for partition, (y, p) in scores.items():
        k = np.searchsorted(np.asarray(edges), np.round(p, 9), side="right") - 1
        for kk, yy in zip(k, y, strict=True):
            x[index[int(kk), partition, int(yy)]] += 1
    return x


def _row_space(a: np.ndarray) -> np.ndarray:
    """An orthonormal basis of the published rows' span."""
    _, s, vt = np.linalg.svd(a, full_matrices=False)
    return vt[s > 1e-9 * max(float(s.max()) if s.size else 0.0, 1.0)]


def _solvable(vectors: np.ndarray, basis: np.ndarray) -> np.ndarray:
    """For each row of ``vectors``, whether it lies in the span ``basis`` spans."""
    residual = vectors - (vectors @ basis.T) @ basis
    return np.linalg.norm(residual, axis=1) < 1e-7


def _fine(value: float, other: float | None) -> bool:
    """None of the outcome, or at least ``MIN_CELL``; a solvable none beside an unknown
    other outcome is taken to be shared with somebody."""
    if value >= MIN_CELL:
        return True
    return value == 0 and other == 0


def _sets(n: int) -> list[tuple[int, ...]]:
    """The sets of atoms to try: all of them for a few atoms, else every run of neighbours."""
    if n <= EVERY_SET_UP_TO:
        return [s for size in range(1, n + 1) for s in itertools.combinations(range(n), size)]
    return [tuple(range(i, j)) for i, j in itertools.combinations(range(n + 1), 2)]


def score_violations(
    facts: Sequence[Fact], scores: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None
) -> tuple[list[str], int]:
    """Every solvable count over a set of score atoms that breaks the rule, and how many
    counts were solvable (``_score_violations``)."""
    found, solved, _keys = _score_violations(facts, scores)
    return found, solved


def _score_violations(
    facts: Sequence[Fact], scores: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None
) -> tuple[list[str], int, list[tuple]]:
    """Every solvable count over a set of score atoms that breaks the rule, how many counts
    were solvable, and each violation as a key comparable across sets of edges.

    With ``scores`` (each partition's outcomes and scores) the counts are the book's own,
    and every fact is first checked against them. Without, each solvable count is solved
    for from the published figures alone, as an outsider would.
    """
    edges = _edges(facts)
    index = _index(edges)
    a = np.array([_row(f, edges, index) for f in facts])
    published = np.array([f.value for f in facts], dtype=float)
    if scores is not None:
        truth = true_atoms(edges, scores, index)
        wrong = [f for f, got in zip(facts, a @ truth, strict=True) if abs(got - f.value) > 1e-6]
        if wrong:
            raise ValueError(f"the model of what is published disagrees with the book: {wrong[:3]}")
        x = truth
    else:
        x = np.linalg.pinv(a) @ published
        if not np.allclose(a @ x, published):
            raise ValueError("the published figures disagree with one another")
    basis = _row_space(a)
    sets = _sets(len(edges))
    found, solved = [], 0
    keys: list[tuple] = []
    for parts in ({VALIDATION}, {TRAINING}, {VALIDATION, TRAINING}):
        vectors = {}
        for outcome in (CHURNED, STAYED):
            m = np.zeros((len(sets), len(index)))
            for row, chosen in enumerate(sets):
                for k in chosen:
                    for p in parts:
                        m[row, index[k, p, outcome]] = 1.0
            vectors[outcome] = (m, _solvable(m, basis), np.round(m @ x, 6))
        for outcome, other in ((CHURNED, STAYED), (STAYED, CHURNED)):
            _, ok, values = vectors[outcome]
            _, other_ok, other_values = vectors[other]
            for row in np.flatnonzero(ok):
                solved += 1
                partner = other_values[row] if (other_ok[row] or scores is not None) else None
                if not _fine(float(values[row]), None if partner is None else float(partner)):
                    span = [edges[k] for k in sets[row]]
                    found.append(
                        f"{sorted(parts)} atoms from {span} outcome={outcome}: {values[row]:g}"
                    )
                    keys.append((frozenset(parts), _ranges(edges, sets[row]), outcome))
    return found, solved, keys


def _ranges(edges: list[float], chosen: Sequence[int]) -> tuple[tuple[float, float], ...]:
    """The score ranges a set of atoms covers, neighbours merged: comparable across edge sets."""
    out: list[list[float]] = []
    for k in sorted(chosen):
        low, high = edges[k], edges[k + 1] if k + 1 < len(edges) else TOP
        if out and math.isclose(out[-1][1], low, abs_tol=1e-9):
            out[-1][1] = high
        else:
            out.append([low, high])
    return tuple((round(a, 9), round(b, 9)) for a, b in out)


def new_score_violations(
    published: Sequence[Fact], already: Sequence[Fact]
) -> tuple[list[str], int]:
    """What ``published`` lets an outsider solve that ``already`` alone does not.

    ``already`` are figures that are out there whatever is published now (immutable
    reports); ``published`` must include them too. A group they give away is not this
    publication's to withhold - but nothing published may give away another.
    """
    before = set(_score_violations(already)[2])
    found, solved, keys = _score_violations(published)
    return [line for line, key in zip(found, keys, strict=True) if key not in before], solved


#: Per dimension, sets of its segments (by position) and the churners they hold together,
#: known from outside the publication (``external_segment_churners``).
External = Mapping[str, Sequence[tuple[frozenset[int], int]]]


def segment_violations(
    shown: Mapping[str, list[tuple[Cells | None, int, int]]],
    totals: Cells,
    truth: Mapping[str, list[Cells]] | None = None,
    external: External | None = None,
) -> tuple[list[str], int]:
    """Per dimension: ``shown`` lists each segment as (its four cells or ``None`` if hidden,
    customers, flagged); ``truth`` the four cells; ``external`` the churners of sets of
    segments that other sources give (a training count, a test report and the dataset's
    public totals). Every union of segments whose flagged side, left side or churners and
    stayers in all can be solved for and breaks the rule.

    Without ``truth`` each solvable count is solved for from the published figures and
    ``external`` alone, as an outsider would.
    """
    found, solved = [], 0
    for dimension, segments in shown.items():
        k = len(segments)

        def cell(s: int, c: int, k: int = k) -> np.ndarray:
            v = np.zeros(4 * k)
            v[4 * s + c] = 1.0
            return v

        rows, values = [], []
        for s, (cells, customers, flagged) in enumerate(segments):
            rows.append(sum(cell(s, c) for c in range(4)))
            values.append(customers)
            rows.append(cell(s, 0) + cell(s, 1))
            values.append(flagged)
            if cells is not None:
                for c in range(4):
                    rows.append(cell(s, c))
                    values.append(cells[c])
        for c in range(4):
            rows.append(sum(cell(s, c) for s in range(k)))
            values.append(totals[c])
        for members, churners in (external or {}).get(dimension, ()):
            rows.append(sum(cell(s, 0) + cell(s, 2) for s in members))
            values.append(churners)
        a = np.array(rows)
        if truth is not None:
            x = np.array([v for cells in truth[dimension] for v in cells], dtype=float)
        else:
            x = np.linalg.pinv(a) @ np.array(values, dtype=float)
        if not np.allclose(a @ x, values):
            raise ValueError(f"{dimension}: the published segments disagree with their counts")
        basis = _row_space(a)
        for size in range(1, k + 1):
            for subset in itertools.combinations(range(k), size):
                # (flagged churners, flagged stayers), (missed churners, left stayers), and
                # (churners, stayers) of the segments whole.
                for first, second in ((0, 1), (2, 3), (None, None)):
                    if first is None:
                        a_vec = sum(cell(s, 0) + cell(s, 2) for s in subset)
                        b_vec = sum(cell(s, 1) + cell(s, 3) for s in subset)
                    else:
                        a_vec = sum(cell(s, first) for s in subset)
                        b_vec = sum(cell(s, second) for s in subset)
                    for vec, other in ((a_vec, b_vec), (b_vec, a_vec)):
                        if _solvable(np.atleast_2d(vec), basis)[0]:
                            solved += 1
                            partner = (
                                other @ x
                                if _solvable(np.atleast_2d(other), basis)[0] or truth is not None
                                else None
                            )
                            value = float(np.round(vec @ x, 6))
                            if not _fine(
                                value, None if partner is None else float(np.round(partner, 6))
                            ):
                                what = "whole" if first is None else f"cells {first},{second}"
                                found.append(f"{dimension} {subset} {what}: {value:g}")
    return found, solved


def external_segment_churners(
    one_way: Mapping[str, Sequence[tuple[str, int | None]]],
    partition_churned: int,
    segments: Mapping[str, Sequence[str]],
    determined: Mapping[str, Sequence[tuple[Sequence[str], int]]] | None = None,
) -> dict[str, list[tuple[frozenset[int], int]]]:
    """The validation churners of sets of segments that published training counts give away.

    For a dimension an immutable report states the test partition's churners for
    (``legacy_disclosures.TEST_SEGMENTS``), and whose dataset totals are public
    (``POPULATION_MARGINALS``): a set of levels whose training churn can be read off the
    published one-way table - a shown level, or the hidden ones together as the partition's
    churn less the shown - has validation churners = total - test - training.

    ``one_way`` gives each dimension's training levels as (level, churned or ``None``);
    ``segments`` the validation segments' order, as ``segment_violations`` numbers them.
    ``determined``, when given, replaces what the one-way table alone gives: every set of
    levels whose training churn all the published tables together determine
    (``training_determined``) - a two-way table can give a level the one-way table hides.
    """
    from churnsense import legacy_disclosures as docs

    test = {(row.dimension, row.segment): row.churners for row in docs.TEST_SEGMENTS}
    out: dict[str, list[tuple[frozenset[int], int]]] = {}
    for dimension in docs.TEST_DIMENSIONS:
        if dimension not in one_way or dimension not in segments:
            continue
        order = list(segments[dimension])
        levels = one_way[dimension]
        shown = [(level, c) for level, c in levels if c is not None]
        known: list[tuple[list[str], int]] = [([level], c) for level, c in shown]
        hidden = [level for level, c in levels if c is None]
        if hidden:
            known.append((hidden, partition_churned - sum(c for _, c in shown)))
        if determined is not None:
            known = [(list(names), int(value)) for names, value in determined.get(dimension, ())]
        for names, training in known:
            if not all((dimension, n) in docs.POPULATION_MARGINALS and n in order for n in names):
                continue
            total = sum(
                docs.POPULATION_MARGINALS[dimension, n][1] - test[dimension, n] for n in names
            )
            out.setdefault(dimension, []).append(
                (frozenset(order.index(n) for n in names), total - training)
            )
    return out


# --- a published file ----------------------------------------------------------------------


def facts_of(body: Mapping[str, Any], bands: Sequence[tuple[str, float, float]]) -> list[Fact]:
    """Every count of customers split by score in a published evidence body.

    ``bands`` are the configured risk bands as (name, min, max): the observed churn per
    band is of the training and validation customers together.
    """
    both, everyone = (VALIDATION, TRAINING), (CHURNED, STAYED)
    facts: list[Fact] = []
    basis = body.get("sweep_basis")
    if basis is not None:
        for t, tp, fp in zip(
            basis["thresholds"], basis["true_positives"], basis["false_positives"], strict=True
        ):
            facts += [at_or_above(t, CHURNED, tp), at_or_above(t, STAYED, fp)]
    validation = body.get("validation")
    for b in validation["reliability"] if validation else []:
        facts.append(between({VALIDATION}, everyone, b["low"], b["high"], b["customers"]))
        facts.append(between({VALIDATION}, {CHURNED}, b["low"], b["high"], b["churned"]))
    for name, low, high in bands:
        seen = body["band_observed"][name]
        facts.append(between(both, everyone, low, high, seen["customers"]))
        if seen["churned"] is not None:
            facts.append(between(both, {CHURNED}, low, high, seen["churned"]))
    seen = body["observed"]
    facts.append(between(both, everyone, 0.0, 2.0, seen["customers"]))
    if seen["churned"] is not None:
        facts.append(between(both, {CHURNED}, 0.0, 2.0, seen["churned"]))
    return facts


def segment_view(segments: Sequence[Mapping[str, Any]]) -> dict[str, list]:
    """Each dimension's segments as an outsider reads them: cells where shown."""
    view: dict[str, list] = {}
    for row in segments:
        cells = None
        if row["churners"] is not None:
            churners, flagged = row["churners"], row["flagged"]
            tp = round(row["recall"] * churners) if churners else 0
            fn = churners - tp
            cells = (tp, flagged - tp, fn, row["customers"] - flagged - fn)
        view.setdefault(row["dimension"], []).append((cells, row["customers"], row["flagged"]))
    return view


def legacy_facts(known: Mapping[float, tuple[int, int]]) -> list[Fact]:
    """Figures stated elsewhere, as facts: (customers, churners) at or above each threshold,
    read exactly - the outsider's best case."""
    facts: list[Fact] = []
    for threshold, (flagged, churners) in sorted(known.items()):
        facts += [
            at_or_above(threshold, CHURNED, churners),
            at_or_above(threshold, STAYED, flagged - churners),
        ]
    return facts


def published_violations(
    body: Mapping[str, Any],
    bands: Sequence[tuple[str, float, float]],
    cells: Mapping[tuple[str, str], Cells] | None = None,
    legacy: Mapping[float, tuple[int, int]] | None = None,
    external: bool = False,
) -> list[str]:
    """What an outsider could solve a small group from in ``body``; empty when nothing.

    ``cells`` are the segments' true counts, which the builder knows; without them each
    segment count is solved for from the published figures. ``legacy`` are the validation
    figures immutable reports already state at their own thresholds: only what ``body``
    adds to them counts. ``external`` reads the segments with what the training tables,
    the test report and the dataset's public totals give (``external_segment_churners``) -
    for evidence of the released model, which those reports describe.
    """
    facts = facts_of(body, bands)
    if legacy:
        stated = legacy_facts(legacy)
        found, _ = new_score_violations([*facts, *stated], stated)
    else:
        found, _ = score_violations(facts)
    validation = body.get("validation")
    if validation:
        m = validation["metrics"]
        segments = validation["segments"]
        truth: dict[str, list[Cells]] | None = None
        if cells is not None:
            truth = {}
            for row in segments:
                truth.setdefault(row["dimension"], []).append(
                    cells[row["dimension"], row["segment"]]
                )
        extra = None
        if external:
            extra = external_segment_churners(
                _one_way_churn(body),
                _partition_churned(body),
                _segment_order(segments),
                training_determined(body),
            )
        more, _ = segment_violations(
            segment_view(segments), (m["tp"], m["fp"], m["fn"], m["tn"]), truth, extra
        )
        found += more
    return found


def training_determined(body: Mapping[str, Any]) -> dict[str, list[tuple[list[str], int]]]:
    """For each dimension a release report states test churners for, every set of its
    levels whose training churn the published tables, read together, determine - and that
    churn (``churnsense.joint_disclosure``)."""
    from churnsense import insights
    from churnsense import legacy_disclosures as docs

    tables = {(b["rows"], b["columns"]): b for b in body["breakdowns"]}
    system = insights.joint_system(tables, body.get("lifecycle"))
    out: dict[str, list[tuple[list[str], int]]] = {}
    for dimension in docs.TEST_DIMENSIONS:
        if (dimension, None) not in tables:
            continue
        levels = insights.DIMENSIONS[dimension].levels
        out[dimension] = [
            ([levels[i] for i in sorted(subset)], round(value))
            for subset, value in system.determined_sums(dimension)
        ]
    return out


def _one_way_churn(body: Mapping[str, Any]) -> dict[str, list[tuple[str, int | None]]]:
    return {
        b["rows"]: [(c["row"], c["churned"]) for c in b["row_totals"]]
        for b in body["breakdowns"]
        if b["columns"] is None
    }


def _partition_churned(body: Mapping[str, Any]) -> int:
    return next(b["partition_churned"] for b in body["breakdowns"] if b["columns"] is None)


def _segment_order(segments: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    order: dict[str, list[str]] = {}
    for row in segments:
        order.setdefault(row["dimension"], []).append(row["segment"])
    return order
