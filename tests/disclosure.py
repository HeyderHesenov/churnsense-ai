"""Independent checks of what published figures give away, shared by the tests.

Training-partition breakdowns: a table is stated as equations over its counts - every row and column adds
up to its total, the totals to the partition - with ``0 <= churned <=
customers`` for each hidden count, and a linear program asks for the least and
the greatest value a group of hidden counts can sum to. It shares no code
with ``churnsense.suppression``, which reasons on a graph.

The held-out partition: ``group_ranges`` reads the released totals over any groups
of listed customers, and ``collision`` reads the release rule itself.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Hashable, Iterable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

MINIMUM = 20
#: The cut the held-out scenarios flag customers at.
CUT = 0.36

Equation = dict[Hashable, int]


def fixed_groups(
    sizes: dict,
    values: dict,
    hidden: Iterable[Hashable],
    equations: list[Equation],
    *,
    minimum: int = MINIMUM,
    largest: int | None = None,
) -> set[tuple]:
    """Groups of hidden counts, under ``minimum`` customers together, whose sum is fixed.

    ``equations`` sum to zero over every count; ``values`` holds the published
    ones. ``largest`` caps how many counts a group holds (all, by default).
    """
    keys = [k for k in sorted(hidden, key=repr) if sizes[k] > 0]
    small = [k for k in keys if sizes[k] < minimum]
    index = {k: i for i, k in enumerate(keys)}
    a_eq, b_eq = [], []
    for equation in equations:
        row, rhs = np.zeros(len(keys)), 0.0
        for key, coefficient in equation.items():
            if key in index:
                row[index[key]] += coefficient
            else:
                rhs -= coefficient * values[key]
        if row.any():
            a_eq.append(row)
            b_eq.append(rhs)
    if not a_eq:  # nothing ties the hidden counts: each ranges over 0..customers
        return set()
    bounds = [(0, sizes[k]) for k in keys]
    fixed = set()
    for size in range(1, min(len(small), largest or len(small)) + 1):
        for group in itertools.combinations(small, size):
            if sum(sizes[k] for k in group) >= minimum:
                continue
            objective = np.zeros(len(keys))
            objective[[index[k] for k in group]] = 1
            low = linprog(objective, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
            high = linprog(-objective, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
            assert low.status == 0 and high.status == 0
            if -high.fun - low.fun < 0.5:
                fixed.add(group)
    return fixed


def grid_equations(rows: list, columns: list) -> list[Equation]:
    """A two-way table's balances, over keys ``(r, k)``, ``("row", r)``, ``("col", k)``, ``"all"``."""
    equations: list[Equation] = [{**{(r, k): 1 for k in columns}, ("row", r): -1} for r in rows] + [
        {**{(r, k): 1 for r in rows}, ("col", k): -1} for k in columns
    ]
    equations += [
        {**{("row", r): 1 for r in rows}, "all": -1},
        {**{("col", k): 1 for k in columns}, "all": -1},
    ]
    return equations


def line_equation(levels: list) -> list[Equation]:
    """A one-way table's single balance: its levels add up to ``"all"``."""
    return [{**dict.fromkeys(levels, 1), "all": -1}]


def group_ranges(
    flagged: list[int], unflagged: list[int], caught: int, missed: int
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """What a reader of the released totals can work out of each group's churners.

    The groups are any cross of what the listing shows, so they may be as fine as the
    reader likes. Each has flagged churners ``t`` and unflagged churners ``u``; the
    totals say only what they sum to. An integer program asks each one's least and
    greatest value, as ``((t_low, t_high), (u_low, u_high))``.
    """
    k = len(flagged)
    a_eq = np.zeros((2, 2 * k))
    a_eq[0, :k] = 1
    a_eq[1, k:] = 1
    totals = np.array([caught, missed], dtype=float)
    upper = np.array([*flagged, *unflagged], dtype=float)

    def spread(position: int) -> tuple[float, float]:
        ends = []
        for sign in (1, -1):
            objective = np.zeros(2 * k)
            objective[position] = sign
            result = milp(
                objective,
                constraints=LinearConstraint(a_eq, totals, totals),
                bounds=Bounds(np.zeros(2 * k), upper),
                integrality=np.ones(2 * k),
            )
            assert result.status == 0, "the totals contradict the groups"
            ends.append(sign * result.fun)
        return ends[0], ends[1]

    return [(spread(i), spread(k + i)) for i in range(k)]


def collision(
    published: Callable[[np.ndarray], object], y: np.ndarray, p: np.ndarray, group: np.ndarray
) -> np.ndarray | None:
    """Other labels the same output cannot be told from, with another count in ``group``.

    Moves one churner into or out of ``group`` within a flag side, between the closest
    scores first, and calls the release itself: so this reader knows the rule, and an
    output that gives ``group``'s count away has no such labels. ``None`` if none found.
    """
    flagged = p >= CUT
    wanted = published(y)
    for side in (flagged, ~flagged):
        inside = np.flatnonzero(side & group)
        outside = np.flatnonzero(side & ~group)
        pairs = [(i, j) for i in inside for j in outside if y[i] != y[j]]
        pairs.sort(key=lambda pair: abs(p[pair[0]] - p[pair[1]]))
        for i, j in pairs[:400]:
            other = y.copy()
            other[i], other[j] = y[j], y[i]
            if published(other) == wanted:
                return other
    return None
