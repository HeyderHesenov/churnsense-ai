"""An outsider's reading of published one- and two-way tables, built independently of
``churnsense.joint_disclosure``: the referee its tests are checked against.

One unknown per published count (no merging), with what an outsider may use and nothing
else: each two-way table's cells add up to its margins, each one-way table to the
partition, the same customers counted twice are one count, a group inside another holds
no more churners than it and no fewer stayers, and ``0 <= churned <= customers``. A sum of
hidden counts is *determined* when its least and greatest value over every whole-number
completion agree (``scipy.optimize.milp``). Which counts are the same customers, or nested,
comes from the population itself when there is one (``Population``), never from the sizes.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

Key = tuple


@dataclass
class Published:
    """Published counts: ``customers``/``churned`` per key, ``hidden`` keys, the relations."""

    levels: dict[str, tuple[str, ...]]
    customers: dict[Key, int]
    churned: dict[Key, int]
    hidden: set[Key]
    partition: tuple[int, int]
    same: list[tuple[Key, Key]]
    inside: list[tuple[Key, Key]]  # (smaller, larger)
    pairs: list[tuple[str, str]]


def cell(order: Sequence[str], a: str, i: int, b: str, j: int) -> Key:
    if order.index(a) > order.index(b):
        a, i, b, j = b, j, a, i
    return ("c", a, i, b, j)


class Population:
    """Customers with a level of every dimension and an outcome: the ground truth."""

    def __init__(
        self,
        levels: Mapping[str, Sequence[str]],
        rows: Sequence[Mapping[str, int]],
        churned: Sequence[int],
    ):
        self.levels = {d: tuple(v) for d, v in levels.items()}
        self.order = list(self.levels)
        self.rows = list(rows)
        self.churned = list(churned)

    def members(self, key: Key) -> frozenset[int]:
        if key[0] == "m":
            return frozenset(r for r, row in enumerate(self.rows) if row[key[1]] == key[2])
        return frozenset(
            r for r, row in enumerate(self.rows) if row[key[1]] == key[2] and row[key[3]] == key[4]
        )

    def all_keys(self) -> list[Key]:
        out = [("m", d, i) for d in self.order for i in range(len(self.levels[d]))]
        for a, b in itertools.combinations(self.order, 2):
            out += [
                ("c", a, i, b, j)
                for i in range(len(self.levels[a]))
                for j in range(len(self.levels[b]))
            ]
        return out

    def counts(self) -> tuple[dict[Key, int], dict[Key, int]]:
        customers, churned = {}, {}
        for key in self.all_keys():
            m = self.members(key)
            customers[key] = len(m)
            churned[key] = sum(self.churned[r] for r in m)
        return customers, churned

    def published(self, hidden: Iterable[Key]) -> Published:
        customers, churned = self.counts()
        sets = {key: self.members(key) for key in self.all_keys()}
        same, inside = [], []
        for x, y in itertools.combinations(self.all_keys(), 2):
            if not sets[x] or not sets[y]:
                continue
            if sets[x] == sets[y]:
                same.append((x, y))
            elif sets[x] < sets[y]:
                inside.append((x, y))
            elif sets[y] < sets[x]:
                inside.append((y, x))
        return Published(
            self.levels,
            customers,
            churned,
            set(hidden),
            (len(self.rows), sum(self.churned)),
            same,
            inside,
            list(itertools.combinations(self.order, 2)),
        )

    def joint_input(self, hidden: Iterable[Key]):
        """The arguments ``joint_disclosure.Joint`` takes, true counts and ``hidden`` flags."""
        hidden = set(hidden)
        customers, churned = self.counts()
        one = {
            d: [
                (customers[("m", d, i)], churned[("m", d, i)], ("m", d, i) in hidden)
                for i in range(len(v))
            ]
            for d, v in self.levels.items()
        }
        two = {}
        for a, b in itertools.combinations(self.order, 2):
            two[(a, b)] = [
                [
                    (
                        customers[("c", a, i, b, j)],
                        churned[("c", a, i, b, j)],
                        ("c", a, i, b, j) in hidden,
                    )
                    for j in range(len(self.levels[b]))
                ]
                for i in range(len(self.levels[a]))
            ]
        return self.levels, one, two, (len(self.rows), sum(self.churned))


class Outsider:
    """The whole-number completions of what ``published`` shows."""

    def __init__(self, published: Published) -> None:
        self.p = published
        self.keys = sorted(published.customers, key=str)
        self.index = {k: i for i, k in enumerate(self.keys)}
        n = len(self.keys)
        rows, lo, hi = [], [], []

        def eq(coefficients: dict[Key, int], value: float) -> None:
            line = np.zeros(n)
            for k, c in coefficients.items():
                line[self.index[k]] += c
            rows.append(line)
            lo.append(value)
            hi.append(value)

        levels = published.levels
        for a, b in published.pairs:
            for i in range(len(levels[a])):
                eq({**{("c", a, i, b, j): 1 for j in range(len(levels[b]))}, ("m", a, i): -1}, 0)
            for j in range(len(levels[b])):
                eq({**{("c", a, i, b, j): 1 for i in range(len(levels[a]))}, ("m", b, j): -1}, 0)
        for d, v in levels.items():
            eq({("m", d, i): 1 for i in range(len(v))}, published.partition[1])
        for x, y in published.same:
            eq({x: 1, y: -1}, 0)
        for small, large in published.inside:
            gap = published.customers[large] - published.customers[small]
            line = np.zeros(n)
            line[self.index[large]] += 1
            line[self.index[small]] -= 1
            rows.append(line)
            lo.append(0)
            hi.append(gap)
        self.constraint = LinearConstraint(np.array(rows), np.array(lo), np.array(hi))
        lower, upper = np.zeros(n), np.zeros(n)
        for k, i in self.index.items():
            if k in published.hidden:
                upper[i] = published.customers[k]
            else:
                lower[i] = upper[i] = published.churned[k]
        self.bounds = Bounds(lower, upper)

    def range(self, coefficients: Mapping[Key, int]) -> tuple[int, int]:
        c = np.zeros(len(self.keys))
        for k, v in coefficients.items():
            c[self.index[k]] += v
        out = []
        for sign in (1, -1):
            result = milp(
                sign * c,
                integrality=np.ones(len(c)),
                bounds=self.bounds,
                constraints=[self.constraint],
            )
            assert result.x is not None, "the published counts admit no completion"
            out.append(round(sign * result.fun))
        return out[0], out[1]

    def determined(self, coefficients: Mapping[Key, int]) -> int | None:
        low, high = self.range(coefficients)
        return low if low == high else None


def violations(
    published: Published,
    *,
    minimum: int,
    alone: Iterable[Key] = (),
    size: int = 2,
) -> list[tuple[str, tuple[Key, ...], int]]:
    """Every determined sum of up to ``size`` distinct hidden groups, and every determined
    difference of two nested published groups, that the rule forbids: fewer than ``minimum``
    churners or stayers (an empty group aside), or made of ``alone`` groups only."""
    outsider = Outsider(published)
    alone = set(alone)
    twins = {}
    for x, y in published.same:
        twins.setdefault(y, x)
    hidden = sorted(
        (k for k in published.hidden if published.customers[k] > 0 and k not in twins),
        key=str,
    )
    nested = {frozenset(pair) for pair in published.inside}
    found = []
    for n in range(1, size + 1):
        for group in itertools.combinations(hidden, n):
            if any(frozenset(pair) in nested for pair in itertools.combinations(group, 2)):
                continue
            value = outsider.determined(dict.fromkeys(group, 1))
            if value is None:
                continue
            customers = sum(published.customers[k] for k in group)
            if set(group) <= alone or not _keeps(value, customers, minimum):
                found.append(("sum", group, value))
    for small, large in published.inside:
        if not _difference_group(small, large):
            continue
        value = outsider.determined({large: 1, small: -1})
        if value is None:
            continue
        customers = published.customers[large] - published.customers[small]
        if not _keeps(value, customers, minimum):
            found.append(("difference", (large, small), value))
    return found


def _difference_group(small: Key, large: Key) -> bool:
    """A difference the rule protects: one level inside another's, alone or within the same
    level of a third dimension. (A margin less one of its own cells is the rest of its line,
    already a sum of published cells.)"""
    if small[0] == large[0] == "m":
        return small[1] != large[1]
    if small[0] == large[0] == "c":
        parts = {(small[1], small[2]), (small[3], small[4])}
        return bool(parts & {(large[1], large[2]), (large[3], large[4])})
    return False


def _keeps(churned: int, customers: int, minimum: int) -> bool:
    stayed = customers - churned
    return (churned == 0 and stayed == 0) or (churned >= minimum and stayed >= minimum)
