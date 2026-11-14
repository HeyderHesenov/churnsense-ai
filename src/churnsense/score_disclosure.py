"""What a public copy may say about the validation partition's scores.

Every figure the validation sweep gives is a count, or a sum, over the customers
scored at or above a cut. Two such figures at neighbouring cuts difference into
the customers between them: how many there were, how many churned, and, through
the simulated money, the sum of those churners' monthly charges. On a 0.01 grid
some steps hold one churner, and the difference is that customer's charge and
outcome.

So a public copy publishes the sweep only at cuts between which every interval,
and the tail above the last, holds at least ``MIN_CELL`` churners **and**
``MIN_CELL`` stayers (``safe_cuts``). Every other figure that splits validation
customers by score - the calibration bins, the operating point, the precision
at 0.5 - is published on those same cuts and nowhere else, so no second set of
edges can cut an interval finer. Where no such cuts exist, nothing of the sweep
is published.

Two tables split customers by score and by something else, and are held to the
same rule with complementary suppression: the validation segments at the
operating threshold (``guard_segments``) and the observed churn per risk band
(``guard_bands``). A hidden group must not be recoverable as the total minus the
shown ones, so hiding one hides another until the hidden ones *together* keep
the rule.

The rule is read on the counts themselves: a group is either empty or has at
least ``MIN_CELL`` of each outcome (``two_sided``). An empty group discloses no
one; a group of only churners, or only stayers, discloses everyone in it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from churnsense import insights
from churnsense.evaluation.disclosure import MIN_CELL
from churnsense.evaluation.threshold import SweepBasis
from churnsense.insights import Observed

#: The cut the operating point's figures need besides the served threshold: precision and
#: recall at 0.5, shown to say what calibration changed. Kept only where the rule allows it.
AT_HALF = 0.5

#: What the sweep's pages say when no cuts keep the rule.
SWEEP_WITHHELD = (
    "The validation sweep is not published here: no set of thresholds keeps at least "
    f"{MIN_CELL} churners and {MIN_CELL} stayers between every two of them, so neither the "
    "sweep nor any figure at a threshold is shown."
)


def two_sided(churners: int, stayers: int, minimum: int = MIN_CELL) -> bool:
    """A group nobody is in, or one with at least ``minimum`` of each outcome."""
    return (churners == 0 and stayers == 0) or (churners >= minimum and stayers >= minimum)


# --- the sweep's cuts ------------------------------------------------------------------------


def intervals(basis: SweepBasis) -> list[tuple[float, float, int, int]]:
    """``(low, high, churners, stayers)`` between each two cuts, and above the last (to 1)."""
    t, tp, fp = basis.thresholds, basis.true_positives, basis.false_positives
    out = []
    for i, low in enumerate(t):
        last = i + 1 == len(t)
        high = 1.0 if last else t[i + 1]
        churners = tp[i] - (0 if last else tp[i + 1])
        stayers = fp[i] - (0 if last else fp[i + 1])
        out.append((low, high, churners, stayers))
    return out


def holds(basis: SweepBasis, minimum: int = MIN_CELL) -> bool:
    """Whether ``basis`` starts at 0 (everyone) and every interval of it keeps the rule.

    No interval may be empty either: an empty step between two cuts would be a cut
    that says nothing, and the rule is simplest stated without exceptions.
    """
    return (
        bool(basis.thresholds)
        and basis.thresholds[0] == 0.0
        and all(
            churners >= minimum and stayers >= minimum
            for _low, _high, churners, stayers in intervals(basis)
        )
    )


def _index(thresholds: Sequence[float], cut: float) -> int | None:
    for i, t in enumerate(thresholds):
        if math.isclose(t, cut, abs_tol=1e-9):
            return i
    return None


# --- together with what is already published elsewhere ----------------------------------------
#
# Immutable documents state the validation sweep at thresholds of their own
# (``churnsense.legacy_disclosures``). An outsider reads those and the demo's cuts as one
# set of edges, so a cut must not split a legacy interval into a piece the rule forbids -
# and must not sit on the edge of a legacy interval that already breaks it, where its exact
# counts would sharpen a small group the documents give only roughly.


@dataclass(frozen=True, slots=True)
class Known:
    """What an outsider knows at a threshold: the customers at or above it, exactly, and how
    many of them churned, somewhere in ``low`` to ``high``."""

    flagged: int
    low: int
    high: int


@dataclass(frozen=True, slots=True)
class Legacy:
    """Thresholds other documents state the sweep at, and what they say there.

    ``known`` maps each threshold to what its document gives; where it is ``None`` the
    counts are read, exactly, from the sweep being cut (a builder that has the full grid).
    """

    known: Mapping[float, Known | None]

    @property
    def thresholds(self) -> tuple[float, ...]:
        return tuple(sorted(self.known))


def published_legacy(positives: int) -> Legacy:
    """The release reports' sweep, as an outsider reads it for a partition of ``positives``."""
    from churnsense import legacy_disclosures as docs

    return Legacy(
        {
            point.threshold: Known(point.flagged, *docs.churner_range(point, positives))
            for point in docs.LEGACY_SWEEP
        }
    )


def _known_at(basis: SweepBasis, legacy: Legacy, t: float | None, cut: bool) -> Known:
    """Counts at ``t``: exact at a cut of ``basis`` (or the top, ``None``), else the legacy view."""
    if t is None:
        return Known(0, 0, 0)
    stated = next((k for s, k in legacy.known.items() if math.isclose(s, t, abs_tol=1e-9)), None)
    i = _index(basis.thresholds, t)
    if cut or stated is None:
        if i is None:
            raise ValueError(f"no counts at {t}: neither a cut of the sweep nor stated elsewhere")
        tp, fp = basis.true_positives[i], basis.false_positives[i]
        return Known(tp + fp, tp, tp)
    return stated


def _pieces_keep(
    basis: SweepBasis, legacy: Legacy, low: float, high: float | None, minimum: int
) -> bool:
    """Whether the pieces between two neighbouring cuts ``low`` and ``high`` (``None``: the
    top), split by the legacy thresholds between them, keep the rule as an outsider sees them.

    A piece bounded by two legacy edges is the documents' own interval: it is not the
    demo's to fix, but neither of the demo's cuts may be its edge when it breaks the rule.
    """
    inner = [t for t in legacy.thresholds if low + 1e-9 < t and (high is None or t < high - 1e-9)]
    edges: list[float | None] = [low, *inner, high]
    for a, b in itertools.pairwise(edges):
        upper, lower = _known_at(basis, legacy, a, a == low), _known_at(basis, legacy, b, b == high)
        churners = upper.low - lower.high
        stayers = (upper.flagged - upper.high) - (lower.flagged - lower.low)
        if churners >= minimum and stayers >= minimum:
            continue
        # A piece that breaks the rule may only be the documents' own interval: neither of
        # its edges a cut of the demo's. (0 is everyone, which every publication states;
        # the top is no cut.) Every other edge is a legacy threshold strictly inside.
        if (a == low and a != 0.0) or (b is not None and b == high):
            return False
    return True


def legacy_holds(basis: SweepBasis, legacy: Legacy, minimum: int = MIN_CELL) -> bool:
    """Whether ``basis``'s cuts, read together with ``legacy``'s, cut no group the rule forbids."""
    cuts: list[float | None] = [*basis.thresholds, None]
    try:
        return all(
            _pieces_keep(basis, legacy, a, b, minimum)
            for a, b in itertools.pairwise(cuts)
            if a is not None
        )
    except ValueError:
        return False


def _greedy(
    basis: SweepBasis, required: Sequence[float], minimum: int, legacy: Legacy | None = None
) -> tuple[float, ...] | None:
    """The most cuts of ``basis``'s grid, ``required`` among them, that keep the rule.

    Left to right, each cut is the first that leaves enough behind it *and* before
    the next required cut (or the top). Every interval has a lower bound only, so
    the earliest feasible cut is never worse than a later one: no grid cut can be
    added to the result without breaking the rule. With ``legacy``, a step must also
    keep the rule against the legacy thresholds inside it (``_pieces_keep``); the result
    is then safe, if not always the most cuts.
    """
    t, tp, fp = basis.thresholds, basis.true_positives, basis.false_positives
    if not t or t[0] != 0.0:
        return None
    stops: list[int] = []
    for cut in required:
        at = _index(t, cut)
        if at is None:
            return None
        if at:
            stops.append(at)

    def enough(i: int, j: int | None) -> bool:
        above = (0, 0) if j is None else (tp[j], fp[j])
        if not (tp[i] - above[0] >= minimum and fp[i] - above[1] >= minimum):
            return False
        if legacy is None:
            return True
        try:
            return _pieces_keep(basis, legacy, t[i], None if j is None else t[j], minimum)
        except ValueError:
            return False

    kept = [0]
    for stop in [*sorted(set(stops)), None]:
        end = len(t) if stop is None else stop
        for j in range(kept[-1] + 1, end):
            if enough(kept[-1], j) and enough(j, stop):
                kept.append(j)
        if not enough(kept[-1], stop):
            return None
        if stop is not None:
            kept.append(stop)
    return tuple(t[i] for i in kept)


def safe_cuts(
    basis: SweepBasis,
    required: Iterable[float] = (),
    preferred: Iterable[float] = (),
    minimum: int = MIN_CELL,
    legacy: Legacy | None = None,
) -> tuple[float, ...] | None:
    """Cuts of ``basis``'s grid that keep the rule: ``required`` always, and as many of
    ``preferred`` as fit together - earlier ones first; ``None`` when no such cuts exist.

    Preferred cuts are where other published figures sit (the documents' peak, 0.5): one
    that cannot be kept is dropped alone, not with the others. With ``legacy``, the cuts
    also keep the rule read together with the thresholds other documents state.
    """
    required, preferred = tuple(required), tuple(preferred)
    for size in range(len(preferred), 0, -1):
        for chosen in combinations(preferred, size):  # in the order given
            if (cuts := _greedy(basis, (*required, *chosen), minimum, legacy)) is not None:
                return cuts
    return _greedy(basis, required, minimum, legacy)


def restrict(basis: SweepBasis, cuts: Sequence[float]) -> SweepBasis:
    """``basis`` read at ``cuts`` only, which must be thresholds of it."""
    found = [_index(basis.thresholds, cut) for cut in cuts]
    picked = [i for i in found if i is not None]
    if len(picked) != len(found) or picked != sorted(set(picked)):
        raise ValueError("the cuts must be rising thresholds of the basis")
    return SweepBasis(
        customers=basis.customers,
        thresholds=tuple(basis.thresholds[i] for i in picked),
        true_positives=tuple(basis.true_positives[i] for i in picked),
        false_positives=tuple(basis.false_positives[i] for i in picked),
        false_negatives=tuple(basis.false_negatives[i] for i in picked),
        flagged_churner_charges=tuple(basis.flagged_churner_charges[i] for i in picked),
    )


def reliability_on(y: np.ndarray, p: np.ndarray, cuts: Sequence[float]) -> list[dict]:
    """Observed churn against the mean score, per interval between ``cuts``.

    The calibration bins of a public copy: the sweep's own intervals, so they add
    no edge of their own. Each holds at least ``MIN_CELL`` of each outcome when
    ``cuts`` keep the rule, so every rate is shown.
    """
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    out = []
    for i, low in enumerate(cuts):
        last = i + 1 == len(cuts)
        high = 1.0 if last else float(cuts[i + 1])
        rows = (p >= low) if last else (p >= low) & (p < high)
        customers, churned = int(rows.sum()), int(y[rows].sum())
        out.append(
            {
                "low": float(low),
                "high": high,
                "customers": customers,
                "mean_predicted": float(p[rows].mean()) if customers else float(low),
                "churned": churned,
                "rate": churned / customers if customers else None,
            }
        )
    return out


# --- segments at the operating threshold --------------------------------------------------------

Cells = tuple[
    int, int, int, int
]  # flagged churners, flagged stayers, missed churners, left stayers


def segment_cells(
    y: np.ndarray, p: np.ndarray, frame: pd.DataFrame, columns: Sequence[str], threshold: float
) -> dict[tuple[str, str], Cells]:
    """Each segment's four counts at ``threshold``, keyed as ``segment_table`` labels them."""
    y, flagged = np.asarray(y, dtype=int) == 1, np.asarray(p, dtype=float) >= threshold
    out: dict[tuple[str, str], Cells] = {}
    for column in columns:
        labels = frame[column].astype(str).to_numpy()
        for segment in pd.unique(labels):
            rows = labels == segment
            out[column, str(segment)] = (
                int((rows & flagged & y).sum()),
                int((rows & flagged & ~y).sum()),
                int((rows & ~flagged & y).sum()),
                int((rows & ~flagged & ~y).sum()),
            )
    return out


def _cells_keep(cells: Cells, minimum: int) -> bool:
    tp, fp, fn, tn = cells
    return two_sided(tp, fp, minimum) and two_sided(fn, tn, minimum)


def _sum(cells: Iterable[Cells]) -> Cells:
    total = [0, 0, 0, 0]
    for c in cells:
        total = [a + b for a, b in zip(total, c, strict=True)]
    return (total[0], total[1], total[2], total[3])


def guard_segments(
    segments: Sequence[dict], cells: Mapping[tuple[str, str], Cells], minimum: int = MIN_CELL
) -> list[dict]:
    """``segments`` with every outcome hidden where it, or the hidden ones together, break the rule.

    Within a segment the operating threshold makes two intervals, the flagged and
    the left; each must keep the rule. The segments of one dimension add up to
    the validation partition, whose counts at the threshold are published, so
    the hidden segments of a dimension are hidden *together*: while their sum
    breaks the rule, the smallest shown segment is hidden as well.
    """
    hidden: set[tuple[str, str]] = set()
    for dimension in dict.fromkeys(row["dimension"] for row in segments):
        keys = [
            (row["dimension"], row["segment"]) for row in segments if row["dimension"] == dimension
        ]
        mine = {key for key in keys if key not in cells or not _cells_keep(cells[key], minimum)}
        shown = sorted(
            (key for key in keys if key not in mine),
            key=lambda key: (sum(cells[key]), key[1]),
        )
        while mine and not _cells_keep(_sum(cells[key] for key in mine if key in cells), minimum):
            if not shown:
                break
            mine.add(shown.pop(0))
        hidden |= mine
    return [
        row | dict.fromkeys(insights.SEGMENT_OUTCOMES)
        if (row["dimension"], row["segment"]) in hidden
        else row
        for row in segments
    ]


def identical_segments(
    frame: pd.DataFrame, columns: Sequence[str]
) -> list[tuple[tuple[str, str], ...]]:
    """Segments of different dimensions that hold exactly the same customers.

    Suppression is decided per dimension, so one group under two names - without
    internet, and "No internet service" of every add-on - could be hidden under one
    and shown under the other.
    """
    groups: dict[frozenset, list[tuple[str, str]]] = {}
    for column in columns:
        labels = frame[column].astype(str)
        for segment in pd.unique(labels):
            members = frozenset(frame.index[labels == segment])
            groups.setdefault(members, []).append((column, str(segment)))
    return [tuple(names) for names in groups.values() if len(names) > 1]


def betrayed(
    segments: Sequence[Mapping[str, Any]], identical: Sequence[tuple[tuple[str, str], ...]]
) -> list[tuple[tuple[str, str], ...]]:
    """The groups of ``identical`` that are hidden under one name and shown under another."""
    shown = {(row["dimension"], row["segment"]): row["churners"] is not None for row in segments}
    return [
        names for names in identical if len({shown[name] for name in names if name in shown}) > 1
    ]


# --- risk bands -------------------------------------------------------------------------------------


def guard_bands(
    bands: Mapping[str, Observed | None], overall: Observed, minimum: int = MIN_CELL
) -> dict[str, Observed | None]:
    """``bands`` with observed churn hidden where a band, or the hidden ones together, break the rule.

    The bands split the training and validation customers by score, and add up to
    ``overall``: what is hidden is read off as ``overall`` minus what is shown, so
    that difference must keep the rule too.
    """

    counted = {
        name: (seen.customers, seen.churned)
        for name, seen in bands.items()
        if seen is not None and seen.churned is not None
    }
    shown = {
        n
        for n, (customers, churned) in counted.items()
        if two_sided(churned, customers - churned, minimum)
    }

    def hidden_keep() -> bool:
        if len(shown) == len(bands):
            return True
        customers = overall.customers - sum(counted[n][0] for n in shown)
        churned = (overall.churned or 0) - sum(counted[n][1] for n in shown)
        return two_sided(churned, customers - churned, minimum)

    while shown and not hidden_keep():
        shown.remove(min(shown, key=lambda n: (counted[n][0], n)))
    return {
        name: seen
        if name in shown or seen is None
        else Observed(customers=seen.customers, churned=None, rate=None)
        for name, seen in bands.items()
    }
