"""An independent check of what a published table gives away, shared by the tests.

A table is stated as equations over its counts - every row and column adds
up to its total, the totals to the partition - with ``0 <= churned <=
customers`` for each hidden count, and a linear program asks for the least and
the greatest value a group of hidden counts can sum to. It shares no code
with ``churnsense.suppression``, which reasons on a graph.
"""

from __future__ import annotations

import itertools
from collections.abc import Hashable, Iterable

import numpy as np
from scipy.optimize import linprog

MINIMUM = 20

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
