"""Which churn counts of a breakdown stay hidden, so that no small group's can be worked out.

A breakdown publishes every group's size and, from ``minimum`` customers up,
how many of it churned. Hiding a small group's count is not enough on its own:
its row and column totals are published too, so the count can come back by
subtraction - or from the plain fact that a count is never negative, when the
rest of its line already holds every churner (or none). This module hides
further counts until no group under ``minimum`` customers, alone or summed
with other hidden groups, has a churn count that the published counts and
``0 <= churned <= customers`` fix.

**The table as a graph.** Every count is an edge and every total a node where
what flows in equals what flows out:

* one-way: each level runs from the levels node to the total node, and the
  partition's churners run back;
* two-way: each cell runs from its row to its column, each row total from the
  rows' top node to its row, each column total from its column to the
  columns' top node, and the partition from the columns' top to the rows'.

**What the published counts fix.** The completions of a table - every way to
fill in its hidden counts - form a polytope. Its affine hull is cut out by the
flow balance and by "this count is known", for each published count and for
each hidden one that every completion agrees on (found with a pair of linear
programs). What moves within it are circulations on the remaining hidden
edges, spanned by their fundamental cycles. A sum of hidden counts is fixed
exactly when no such cycle changes it, so the question "is there a small group
whose sum is fixed?" is a little integer program over the small edges, not an
enumeration of groups.

**How a count is protected.** A hidden edge comes back by subtraction when
no cycle of hidden edges runs through it, so the first option closes such a
cycle at the least cost: the fewest further groups hidden, then the fewest
customers. When the bounds fix it, a line through it has no room left, so a
count on that line is hidden instead, with a cycle of its own.

Totals - a breakdown's margins and the partition - are never hidden here: a
margin is decided by its own one-way breakdown, so a two-way table hides only
cells, and two responses that share a margin agree on it.

**Scope: one response at a time.** Each table is protected on its own, and this
is a rule for what a report states, not privacy protection. Responses can add
to each other: two two-way tables that share a margin the one-way breakdown hid
can each narrow that margin's churn and together pin it, and two dimensions can
name the same people (no internet service under ``InternetService`` and under an
add-on), so a count hidden in one table may be published in the other. Protecting
every table of every dimension at once is not attempted. Nor does this reach
beyond breakdowns: the API lists training customers with their own outcomes.
"""

from __future__ import annotations

import heapq
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

logger = logging.getLogger(__name__)

#: The cost of hiding a set of edges: how many groups, then how many customers.
Cost = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Edge:
    tail: int
    head: int
    customers: int
    churned: int
    #: A total decided elsewhere (a margin, the partition): never newly hidden here.
    fixed: bool = False


class _Table:
    """A table's graph, the edges hidden so far, and what they give away."""

    def __init__(
        self, nodes: int, edges: Sequence[_Edge], hidden: Iterable[int], minimum: int
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.minimum = minimum
        # An empty group's count is a known zero: nothing to hide, nothing to hide behind.
        self.hidden = {i for i in hidden if edges[i].customers > 0}
        self.incidence = np.zeros((nodes, len(edges)))
        for i, edge in enumerate(edges):
            self.incidence[edge.tail, i] += 1
            self.incidence[edge.head, i] -= 1
        # Only hideable edges are ever added, and none of them is small: this never changes.
        self.small = sorted(i for i in self.hidden if edges[i].customers < minimum)
        #: Hidden edges found free. Hiding more only widens the completions: they stay free.
        self.free: set[int] = set()

    def hideable(self, i: int) -> bool:
        edge = self.edges[i]
        return i not in self.hidden and not edge.fixed and edge.customers > 0

    def cost(self, added: Iterable[int]) -> Cost:
        added = list(added)
        return len(added), sum(self.edges[i].customers for i in added)

    # --- what the published counts fix ------------------------------------------------------

    def _optimum(self, objective: np.ndarray, bounds: list[tuple[int, int]]) -> float:
        result = linprog(
            objective,
            A_eq=self.incidence,
            b_eq=np.zeros(self.nodes),
            bounds=bounds,
            method="highs",
        )
        if result.status != 0:
            raise RuntimeError(f"a published table has no consistent completion: {result.message}")
        return float(result.fun)

    def _moving(self, hidden: set[int], trial: bool) -> list[int]:
        """The hidden edges whose count differs between completions of the published table."""
        bounds = [
            (0, e.customers) if j in hidden else (e.churned, e.churned)
            for j, e in enumerate(self.edges)
        ]
        moving = []
        for i in sorted(hidden):
            if i in self.free:
                moving.append(i)
                continue
            objective = np.zeros(len(self.edges))
            objective[i] = 1
            low = self._optimum(objective, bounds)
            high = -self._optimum(-objective, bounds)
            # A network matrix with whole-number bounds: both optima are whole numbers.
            if high - low >= 0.5:
                moving.append(i)
                if not trial:
                    self.free.add(i)
        return moving

    def _cycles(self, moving: list[int]) -> np.ndarray:
        """The fundamental cycles of the ``moving`` edges: one row each, entries -1, 0 or 1.

        A spanning forest is grown over the moving edges; every other moving edge
        closes one cycle with the forest path between its ends, each edge signed
        by whether the cycle runs along it or against it.
        """
        reach: dict[int, list[tuple[int, int]]] = {}
        for i in moving:
            edge = self.edges[i]
            reach.setdefault(edge.tail, []).append((i, edge.head))
            reach.setdefault(edge.head, []).append((i, edge.tail))
        #: Each forest node's edge to its parent and the parent; a root has none.
        up: dict[int, tuple[int, int]] = {}
        depth: dict[int, int] = {}
        in_forest: set[int] = set()
        for root in sorted(reach):
            if root in depth:
                continue
            depth[root] = 0
            queue = [root]
            for node in queue:
                for i, other in reach[node]:
                    if other not in depth:
                        up[other], depth[other] = (i, node), depth[node] + 1
                        in_forest.add(i)
                        queue.append(other)

        def climb(node: int, row: np.ndarray, toward_root: bool) -> int:
            """One step up the forest, the edge signed by the cycle's direction over it."""
            i, parent = up[node]
            along = self.edges[i].tail == node if toward_root else self.edges[i].head == node
            row[i] += 1 if along else -1
            return parent

        cycles = []
        for i in moving:
            if i in in_forest:
                continue
            row = np.zeros(len(self.edges))
            row[i] = 1
            # Along the edge from tail to head, then through the forest from head back to tail.
            back, start = self.edges[i].head, self.edges[i].tail
            while depth[back] > depth[start]:
                back = climb(back, row, toward_root=True)
            while depth[start] > depth[back]:
                start = climb(start, row, toward_root=False)
            while back != start:
                back = climb(back, row, toward_root=True)
                start = climb(start, row, toward_root=False)
            cycles.append(row)
        return np.array(cycles).reshape(len(cycles), len(self.edges))

    def _state(self, extra: frozenset[int] = frozenset()) -> np.ndarray:
        """The fundamental cycles of what still moves once ``extra`` is hidden as well."""
        return self._cycles(self._moving(self.hidden | extra, trial=bool(extra)))

    @staticmethod
    def fixes(cycles: np.ndarray, group: frozenset[int]) -> bool:
        """Whether no cycle changes ``group``'s summed count."""
        return not cycles[:, sorted(group)].sum(axis=1).any()

    def settled(self) -> tuple[set[int], np.ndarray]:
        """What the published totals fix by themselves, whatever else is hidden.

        The small edges still pinned with every hideable edge hidden (a line whose
        total holds no churner, or only churners), and the cycles that remain
        then: a group no cycle changes even so cannot be protected by hiding.
        """
        everything = self.hidden | {j for j in range(len(self.edges)) if self.hideable(j)}
        moving = self._moving(everything, trial=True)
        return set(self.small) - set(moving), self._cycles(moving)

    def pinned_group(self, given_up: set[frozenset[int]]) -> frozenset[int] | None:
        """A set of small hidden edges, under ``minimum`` together, whose sum is fixed.

        The one with the fewest customers (ties to the earliest edges), found by
        an integer program: pick small edges over which every fundamental cycle
        sums to zero. ``None`` when there is none outside ``given_up``.
        """
        if not self.small:
            return None
        cycles = self._state()[:, self.small]
        size = np.array([self.edges[i].customers for i in self.small])
        width = len(self.small)
        constraints = [
            LinearConstraint(np.ones((1, width)), lb=1),
            LinearConstraint(size[None, :], ub=self.minimum - 1),
        ]
        if len(cycles):
            constraints.append(LinearConstraint(cycles, lb=0, ub=0))
        for group in given_up:
            inside = np.array([1 if i in group else -1 for i in self.small])
            constraints.append(LinearConstraint(inside[None, :], ub=len(group) - 1))
        # Fewest customers first: the index terms add up to less than one customer's weight.
        objective = size * width**2 + np.arange(width)
        result = milp(
            objective, constraints=constraints, integrality=np.ones(width), bounds=Bounds(0, 1)
        )
        if result.status == 2:  # infeasible: no small group is fixed
            return None
        if result.status != 0:
            raise RuntimeError(f"the disclosure check did not finish: {result.message}")
        return frozenset(i for i, z in zip(self.small, result.x, strict=True) if z > 0.5)

    # --- the repairs ------------------------------------------------------------------------

    def cycle(self, i: int, extra: frozenset[int] = frozenset()) -> list[int] | None:
        """The edges to hide so that a cycle of hidden edges runs through edge ``i``.

        The cheapest path between ``i``'s ends that avoids ``i``: a hidden edge
        (or one in ``extra``) is free, a hideable one costs itself, totals and
        empty groups are out of reach. ``None`` when no such path exists.
        """
        hidden = self.hidden | extra
        start, goal = self.edges[i].tail, self.edges[i].head
        best: dict[int, tuple[Cost, list[int]]] = {start: ((0, 0), [])}
        queue: list[tuple[Cost, int]] = [((0, 0), start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if node == goal:
                return best[node][1]
            if cost > best[node][0]:
                continue
            for j, edge in enumerate(self.edges):
                if j == i or node not in (edge.tail, edge.head) or edge.customers == 0:
                    continue
                if j not in hidden and edge.fixed:
                    continue
                step = (0, 0) if j in hidden else (1, edge.customers)
                reached = (cost[0] + step[0], cost[1] + step[1])
                other = edge.head if node == edge.tail else edge.tail
                if other not in best or reached < best[other][0]:
                    best[other] = (reached, best[node][1] + ([] if j in hidden else [j]))
                    heapq.heappush(queue, (reached, other))
        return None

    def protect(self, group: frozenset[int]) -> set[int]:
        """The cheapest edges to hide that free pinned ``group``, or the cheapest that help.

        Subtraction pins an edge no cycle runs through, so closing one is the
        first kind of option. Otherwise the bounds pin it: a line through one of
        its ends has no room left, so a hideable edge there is hidden, with a
        cycle of its own so that it cannot simply be worked out instead. When no
        option frees the group, the cheapest one still hides more; when nothing
        near it can be hidden, the smallest hideable edge anywhere is. Empty only
        when nothing at all is left to hide.
        """
        found: set[frozenset[int]] = set()
        for i in sorted(group):
            if own := self.cycle(i):
                found.add(frozenset(own))
            ends = (self.edges[i].tail, self.edges[i].head)
            for j, edge in enumerate(self.edges):
                if self.hideable(j) and (edge.tail in ends or edge.head in ends):
                    found.add(frozenset({j, *(self.cycle(j, frozenset({j})) or [])}))
        options = sorted(found, key=lambda option: (self.cost(option), sorted(option)))
        for option in options:
            if not self.fixes(self._state(option), group):
                return set(option)
        if options:
            return set(options[0])
        rest = [j for j in range(len(self.edges)) if self.hideable(j)]
        return {min(rest, key=lambda j: (self.edges[j].customers, j))} if rest else set()


def _protect(nodes: int, edges: Sequence[_Edge], hidden: Iterable[int], minimum: int) -> set[int]:
    """``hidden``, plus the edges that keep every small group's count open; deterministic.

    What the published totals fix by themselves (a total with no churner in
    it gives each of its groups away) no hiding can protect: it is set aside
    first and logged once, so it costs no further group. Every other round
    hides at least one more edge or sets one group aside, so it ends.
    """
    table = _Table(nodes, edges, hidden, minimum)
    lost, everything = table.settled()
    table.small = [i for i in table.small if i not in lost]
    given_up: set[frozenset[int]] = set()
    while (group := table.pinned_group(given_up)) is not None:
        added = set() if table.fixes(everything, group) else table.protect(group)
        if not added:
            given_up.add(group)
        table.hidden |= added
    if lost or given_up:
        logger.warning(
            "%d small group(s) have a churn count the published totals fix by themselves",
            len(lost) + len(given_up),
        )
    return table.hidden


def _safely(nodes: int, edges: Sequence[_Edge], hidden: Iterable[int], minimum: int) -> set[int]:
    """``_protect``, or - should a solver fail - every count the table may hide."""
    hidden = set(hidden)
    try:
        return _protect(nodes, edges, hidden, minimum)
    except RuntimeError:
        logger.exception("the disclosure check failed: hiding every count the table may hide")
        return {i for i, e in enumerate(edges) if e.customers > 0 and (not e.fixed or i in hidden)}


def one_way(customers: Sequence[int], churned: Sequence[int], minimum: int) -> frozenset[int]:
    """The levels of a one-way breakdown whose churn count is hidden.

    Every non-empty level under ``minimum`` customers, and the further levels
    that keep each of them from following out of the partition's total.
    """
    levels = len(customers)
    edges = [_Edge(0, 1, int(n), int(c)) for n, c in zip(customers, churned, strict=True)]
    edges.append(_Edge(1, 0, int(sum(customers)), int(sum(churned)), fixed=True))
    small = (i for i, n in enumerate(customers) if n < minimum)
    return frozenset(i for i in _safely(2, edges, small, minimum) if i < levels)


def two_way(
    customers: np.ndarray | Sequence[Sequence[int]],
    churned: np.ndarray | Sequence[Sequence[int]],
    *,
    hidden_rows: Iterable[int],
    hidden_columns: Iterable[int],
    minimum: int,
) -> frozenset[tuple[int, int]]:
    """The cells of a two-way breakdown whose churn count is hidden, as (row, column).

    ``hidden_rows`` and ``hidden_columns`` are the margins each dimension's own
    one-way breakdown hides (``one_way``); they stay as they are, so the cells
    are hidden around them.
    """
    n, c = np.asarray(customers, dtype=int), np.asarray(churned, dtype=int)
    rows, columns = n.shape
    rows_top, columns_top = rows + columns, rows + columns + 1
    edges = [
        _Edge(r, rows + k, int(n[r, k]), int(c[r, k])) for r in range(rows) for k in range(columns)
    ]
    edges += [_Edge(rows_top, r, int(n[r].sum()), int(c[r].sum()), fixed=True) for r in range(rows)]
    edges += [
        _Edge(rows + k, columns_top, int(n[:, k].sum()), int(c[:, k].sum()), fixed=True)
        for k in range(columns)
    ]
    edges.append(_Edge(columns_top, rows_top, int(n.sum()), int(c.sum()), fixed=True))
    cells = rows * columns
    hidden = {int(r) * columns + int(k) for r, k in np.argwhere(n < minimum)}
    hidden |= {cells + r for r in hidden_rows} | {cells + rows + k for k in hidden_columns}
    protected = _safely(rows + columns + 2, edges, hidden, minimum)
    return frozenset(divmod(i, columns) for i in sorted(protected) if i < cells)
