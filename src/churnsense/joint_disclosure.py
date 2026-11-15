"""Every published training table, guarded as one disclosure system.

The public demo publishes, for the training partition, each dimension's one-way table and
every two-way table of two dimensions, with each group's size always and its churn unless
hidden, plus the partition's totals. ``churnsense.suppression`` protects each table on its
own; an outsider reads them all at once. A column margin less a row margin whose row
holds a single group gives a cell this table hides; a margin one table hides follows from a
row another table shows in full; the same customers appear in several tables (the add-ons'
"No internet service" is InternetService "No"); and one group inside another
(InternetService "No" inside the cheapest price band) makes their difference a group of
its own in every third dimension.

So everything is read as one system of whole-number counts:

* a **variable** per distinct set of customers - a level, a pair of levels, or the
  difference of two nested ones. Which sets are the same, and which lie inside which, is
  read off the published sizes alone: ``|a and b| = |a| = |b|`` is one set,
  ``|a and b| = |a| < |b|`` is ``a`` inside ``b``;
* **equalities**: each two-way table's cells add up to its margins, each one-way table to
  the partition, a difference to its two sets; **bounds** ``0 <= churned <= customers``;
  and the median tenures of churners and stayers, which bound the tenure buckets;
* a **protected** count is any hidden one, and every difference (never published); a sum
  of protected counts that every whole-number completion gives the same value is
  *determined*, and must hold at least ``minimum`` churners and ``minimum`` stayers. A
  sum of ``alone`` counts only (a group inside a ``CROSS_PARTITION`` level) may never be
  determined at all.

``Joint.guard`` hides published counts until that holds: one search finds the smallest
determined group that breaks it, and the cheapest published count that frees it is hidden
- cells before margins - until there is none. Where nothing published is left to hide and
a group is still determined, it raises ``Unprotectable``: the caller publishes nothing.
A count is never estimated or invented; a completion is used only to read what a
determined sum must equal.

Determined is exact up to floating point in the linear algebra: the counts each bound
pins are found by linear programs, the whole-number completions' own equalities by mixed
integer programs (where the equations are not a network's), and a determined group is
confirmed by elimination modulo two primes. Out of scope, as no exact count follows from
them: the association strengths (rounded), intervals and lifts (functions of shown cells),
and what three dimensions together would bound.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

logger = logging.getLogger(__name__)

#: A published count: its group's size, its churn (``None`` when hidden and unknown, as in
#: a published file) and whether it is hidden.
Count = tuple[int, int | None, bool]

#: A published count's place: ``("m", dimension, level)`` for a one-way level and
#: ``("c", first, level, second, level)`` for a two-way cell, in the canonical order.
Key = tuple

_TOL = 1e-7
_PRIMES = (2_147_483_629, 2_147_483_587)


class Unprotectable(Exception):
    """No published count is left to hide, and a protected group is still determined."""


@dataclass(frozen=True)
class Medians:
    """The published median tenure of churners and of stayers, and the tenure buckets'
    bounds: bucket ``k`` holds tenures in ``(edges[k], edges[k + 1]]``."""

    churned: float | None
    stayed: float | None
    dimension: str
    edges: tuple[int, ...]


@dataclass
class _Var:
    customers: int
    value: float | None
    published: bool
    hidden: bool
    margin: bool
    alone: bool
    order: int
    members: list[Key] = field(default_factory=list)


@dataclass
class _Row:
    coefficients: dict[int, int]
    rhs: float
    equality: bool = True


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Key, Key] = {}

    def find(self, key: Key) -> Key:
        root = self.parent.setdefault(key, key)
        if root != key:
            root = self.parent[key] = self.find(root)
        return root

    def union(self, a: Key, b: Key) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class Joint:
    """The published training tables as one system (see the module docstring).

    ``levels`` gives each dimension's levels in order (the canonical dimension order is the
    mapping's); ``one_way[d]`` a ``Count`` per level; ``two_way[(a, b)]`` (``a`` before ``b``)
    a grid of ``Count``; ``partition`` (customers, churned); ``alone`` the levels whose
    groups may never be determined.
    """

    def __init__(
        self,
        levels: Mapping[str, Sequence[str]],
        one_way: Mapping[str, Sequence[Count]],
        two_way: Mapping[tuple[str, str], Sequence[Sequence[Count]]],
        partition: tuple[int, int],
        *,
        alone: Mapping[str, Iterable[str]] = (),
        medians: Medians | None = None,
        minimum: int = 20,
    ) -> None:
        self.levels = {d: tuple(v) for d, v in levels.items()}
        self.dims = tuple(self.levels)
        self._rank = {d: i for i, d in enumerate(self.dims)}
        self.minimum = minimum
        self.partition = partition
        counts: dict[Key, Count] = {}
        for d, cells in one_way.items():
            for i, count in enumerate(cells):
                counts[("m", d, i)] = count
        for (a, b), grid in two_way.items():
            if self._rank[a] >= self._rank[b]:
                raise ValueError(f"a two-way table must be keyed in canonical order: {a}, {b}")
            for i, line in enumerate(grid):
                for j, count in enumerate(line):
                    counts[("c", a, i, b, j)] = count
        self.counts = counts
        self.pairs = tuple(sorted(two_way, key=lambda p: (self._rank[p[0]], self._rank[p[1]])))
        alone_levels = {
            (d, self.levels[d].index(level)) for d, ls in dict(alone).items() for level in ls
        }
        self._build(alone_levels, medians)

    # --- the system -------------------------------------------------------------------------

    def cell(self, a: str, i: int, b: str, j: int) -> Key:
        if self._rank[a] > self._rank[b]:
            a, i, b, j = b, j, a, i
        return ("c", a, i, b, j)

    def _customers(self, key: Key) -> int:
        return int(self.counts[key][0])

    def _build(self, alone_levels: set[tuple[str, int]], medians: Medians | None) -> None:
        uf = _UnionFind()
        for key in self.counts:
            uf.find(key)
        inside: list[tuple[tuple[str, int], tuple[str, int]]] = []
        for a, b in self.pairs:
            for i in range(len(self.levels[a])):
                for j in range(len(self.levels[b])):
                    both = self._customers(("c", a, i, b, j))
                    na, nb = self._customers(("m", a, i)), self._customers(("m", b, j))
                    if both == 0:
                        continue
                    if both == na:
                        uf.union(("c", a, i, b, j), ("m", a, i))
                    if both == nb:
                        uf.union(("c", a, i, b, j), ("m", b, j))
                    if both == na == nb:
                        for x in self.dims:
                            if x in (a, b):
                                continue
                            for k in range(len(self.levels[x])):
                                uf.union(self.cell(a, i, x, k), self.cell(b, j, x, k))
                    elif both == na:
                        inside.append(((a, i), (b, j)))
                    elif both == nb:
                        inside.append(((b, j), (a, i)))
        derived: list[tuple[Key, Key]] = []
        for (a, i), (b, j) in inside:
            derived.append((("m", b, j), ("m", a, i)))
            for x in self.dims:
                if x in (a, b):
                    continue
                for k in range(len(self.levels[x])):
                    small, large = self.cell(a, i, x, k), self.cell(b, j, x, k)
                    gap = self._customers(large) - self._customers(small)
                    if gap < 0:
                        raise ValueError(f"{large} holds fewer customers than {small} inside it")
                    if gap == 0:
                        uf.union(small, large)
                    else:
                        derived.append((large, small))
        # Levels inside an alone level are alone too.
        alone = set(alone_levels)
        for (a, i), (b, j) in inside:
            if (b, j) in alone:
                alone.add((a, i))
        classes: dict[Key, list[Key]] = {}
        for key in sorted(self.counts, key=self._sort_key):
            classes.setdefault(uf.find(key), []).append(key)
        self.vars: list[_Var] = []
        self.var_of: dict[Key, int] = {}
        #: Sets of customers shown in one table and hidden in another.
        self.mixed: list[list[Key]] = []
        for members in sorted(classes.values(), key=lambda m: self._sort_key(m[0])):
            sizes = {self._customers(k) for k in members}
            if len(sizes) != 1:
                raise ValueError(f"the same customers are counted differently: {members}")
            customers = sizes.pop()
            hidden = any(self.counts[k][2] for k in members)
            if hidden and not all(self.counts[k][2] for k in members) and customers > 0:
                self.mixed.append(list(members))
            known = [self.counts[k][1] for k in members if self.counts[k][1] is not None]
            if len(set(known)) > 1:
                raise ValueError(f"the same customers' churn is given differently: {members}")
            value = known[0] if known else None
            if customers > 0 and not hidden and value is None:
                raise ValueError(f"a shown count has no value: {members}")
            var = _Var(
                customers=customers,
                value=None if value is None else float(value),
                published=True,
                hidden=hidden and customers > 0,
                margin=any(k[0] == "m" for k in members),
                alone=any(self._inside_alone(k, alone) for k in members),
                order=len(self.vars),
                members=list(members),
            )
            for k in members:
                self.var_of[k] = len(self.vars)
            self.vars.append(var)
        rows: list[_Row] = []
        for a, b in self.pairs:
            for i in range(len(self.levels[a])):
                rows.append(
                    self._line(
                        [self.cell(a, i, b, j) for j in range(len(self.levels[b]))], ("m", a, i)
                    )
                )
            for j in range(len(self.levels[b])):
                rows.append(
                    self._line(
                        [self.cell(a, i, b, j) for i in range(len(self.levels[a]))], ("m", b, j)
                    )
                )
        for d in self.dims:
            if any(("m", d, i) in self.counts for i in range(len(self.levels[d]))):
                coefficients: dict[int, int] = {}
                for i in range(len(self.levels[d])):
                    v = self.var_of[("m", d, i)]
                    if self.vars[v].customers:
                        coefficients[v] = coefficients.get(v, 0) + 1
                rows.append(_Row(coefficients, float(self.partition[1])))
        self.derived: dict[int, tuple[int, int]] = {}
        seen: set[tuple[int, int]] = set()
        for large, small in derived:
            vl, vs = self.var_of[large], self.var_of[small]
            if vl == vs or (vl, vs) in seen:
                continue
            seen.add((vl, vs))
            gap = self.vars[vl].customers - self.vars[vs].customers
            values = (self.vars[vl].value, self.vars[vs].value)
            value = None if None in values else values[0] - values[1]
            d = len(self.vars)
            self.vars.append(
                _Var(
                    customers=gap,
                    value=value,
                    published=False,
                    hidden=True,
                    margin=False,
                    alone=self.vars[vl].alone,
                    order=d,
                    members=[("d", large, small)],
                )
            )
            self.derived[d] = (vl, vs)
            rows.append(_Row({d: 1, vl: -1, vs: 1}, 0.0))
        if medians is not None and medians.dimension in self.levels:
            rows.extend(self._median_rows(medians))
        unique: dict[tuple, _Row] = {}
        for row in rows:
            row.coefficients = {v: c for v, c in row.coefficients.items() if c}
            if not row.coefficients:
                continue
            unique.setdefault((tuple(sorted(row.coefficients.items())), row.rhs, row.equality), row)
        self.rows = list(unique.values())
        self._incident: dict[int, list[int]] = {}
        for r, row in enumerate(self.rows):
            for v in row.coefficients:
                self._incident.setdefault(v, []).append(r)

    def _sort_key(self, key: Key) -> tuple:
        if key[0] == "m":
            return (0, self._rank[key[1]], key[2])
        return (1, self._rank[key[1]], key[2], self._rank[key[3]], key[4])

    def _inside_alone(self, key: Key, alone: set[tuple[str, int]]) -> bool:
        if key[0] == "m":
            return (key[1], key[2]) in alone
        return (key[1], key[2]) in alone or (key[3], key[4]) in alone

    def _line(self, cells: list[Key], margin: Key) -> _Row:
        coefficients: dict[int, int] = {}
        for key in cells:
            v = self.var_of[key]
            if self.vars[v].customers:
                coefficients[v] = coefficients.get(v, 0) + 1
        m = self.var_of[margin]
        if self.vars[m].customers:
            coefficients[m] = coefficients.get(m, 0) - 1
        return _Row(coefficients, 0.0)

    def _median_rows(self, medians: Medians) -> list[_Row]:
        """No more than half of an outcome's customers lie strictly below its median tenure,
        and no more than half strictly above."""
        d, edges = medians.dimension, medians.edges
        buckets = [self.var_of[("m", d, k)] for k in range(len(self.levels[d]))]
        churners = self.partition[1]
        stayers = self.partition[0] - churners
        out = []
        for median, total, churn in (
            (medians.churned, churners, True),
            (medians.stayed, stayers, False),
        ):
            if median is None:
                continue
            below = [k for k in range(len(buckets)) if edges[k + 1] < median]
            # Tenures are whole months: the smallest in (edges[k], edges[k + 1]] is edges[k] + 1.
            above = [k for k in range(len(buckets)) if edges[k] + 1 > median]
            for chosen in (below, above):
                if not chosen:
                    continue
                limit = total // 2
                if churn:
                    out.append(_Row({buckets[k]: 1 for k in chosen}, float(limit), equality=False))
                else:
                    customers = sum(self.vars[buckets[k]].customers for k in chosen)
                    out.append(
                        _Row(
                            {buckets[k]: -1 for k in chosen},
                            float(limit - customers),
                            equality=False,
                        )
                    )
        return out

    # --- reading the system ---------------------------------------------------------------

    def _known(self, v: int, hidden: set[int]) -> bool:
        var = self.vars[v]
        return var.customers == 0 or (v not in hidden and var.published)

    def _value(self, v: int) -> float:
        var = self.vars[v]
        return 0.0 if var.customers == 0 else float(var.value)  # type: ignore[arg-type]

    def _reduced(self, hidden: set[int]) -> list[tuple[int, dict[int, int], float, bool]]:
        """Each row over the unknown counts, the known ones moved to its right-hand side."""
        out = []
        for r, row in enumerate(self.rows):
            free, rhs = {}, row.rhs
            for v, c in row.coefficients.items():
                if self._known(v, hidden):
                    rhs -= c * self._value(v)
                else:
                    free[v] = c
            out.append((r, free, rhs, row.equality))
        return out

    def _components(self, hidden: set[int]) -> list[list[int]]:
        unknown = [v for v in range(len(self.vars)) if not self._known(v, hidden)]
        uf = _UnionFind()
        for v in unknown:
            uf.find(v)
        for _r, free, _rhs, _eq in self._reduced(hidden):
            members = list(free)
            for v in members[1:]:
                uf.union(members[0], v)
        groups: dict = {}
        for v in unknown:
            groups.setdefault(uf.find(v), []).append(v)
        return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])

    # --- one component ----------------------------------------------------------------------

    def _analyse(self, component: list[int], hidden: set[int], integer: bool) -> _Analysis:
        members = set(component)
        rows = [
            (r, free, rhs, eq)
            for r, free, rhs, eq in self._reduced(hidden)
            if free and set(free) <= members
        ]
        return _Analysis(self, component, rows, integer=integer)

    def violations(self, hidden: set[int]) -> list[_Violation]:
        """Protected groups the published counts determine and the rule forbids.

        Over the real completions first, which is cheap and finds nearly everything; only
        when they hold none is each component read over the whole numbers too."""
        components = self._components(hidden)
        for integer in (False, True):
            found = [
                v
                for component in components
                for v in self._cached(component, hidden, integer).violations()
            ]
            if found:
                return found
        return []

    def violation(self, hidden: set[int]) -> _Violation | None:
        """The first of ``violations``, or ``None``."""
        found = self.violations(hidden)
        return found[0] if found else None

    def _cached(self, component: list[int], hidden: set[int], integer: bool) -> _Analysis:
        key = (tuple(component), integer)
        cache = self.__dict__.setdefault("_cache", {})
        if key not in cache:
            cache[key] = self._analyse(component, hidden, integer)
        return cache[key]

    def determined(
        self, group: Iterable[int], hidden: set[int], *, integer: bool = True, exact: bool = True
    ) -> bool:
        """Whether every completion gives ``group`` the same sum: over the whole numbers, or
        (``integer=False``, a quicker screen) over the reals, which can only say less."""
        group = set(group)
        if all(self._known(v, hidden) for v in group):
            return True
        for component in self._components(hidden):
            if group & set(component):
                analysis = self._cached(component, hidden, integer)
                part = group & set(component)
                if not analysis.determines(part, exact=exact):
                    return False
        return True

    def _candidates(self, found: _Violation, hidden: set[int]) -> list[int]:
        touching: set[int] = set()
        component = set(found.component)
        for v in component:
            for r in self._incident.get(v, ()):
                touching.update(self.rows[r].coefficients)
        options = [
            v
            for v in touching
            if v not in hidden and self.vars[v].published and self.vars[v].customers > 0
        ]
        return sorted(
            options, key=lambda v: (self.vars[v].margin, self.vars[v].customers, self.vars[v].order)
        )

    def guard(self, *, attempts: int = 12) -> set[Key]:
        """The published counts to hide: every hidden one, and what keeps each determined
        protected group within the rule. ``Unprotectable`` if no hiding can."""
        hidden = {v for v, var in enumerate(self.vars) if var.hidden}
        while True:
            found = self.violations(hidden)
            if not found:
                break
            # Each group gets a count of its own hidden this round: hiding only ever leaves
            # less determined, and whatever a new hide exposes, the next round finds.
            added: set[int] = set()
            for violation in found:
                now = hidden | added
                if not self.determined(violation.group, now, integer=False):
                    continue
                options = self._candidates(violation, now)
                if not options:
                    raise Unprotectable(
                        f"a protected group of {violation.customers} customers stays "
                        "determined with nothing left to hide around it"
                    )
                analysis = self._cached(violation.component, hidden, False)
                screened = set(analysis.freeing(violation.group, options))
                choice = options[0]
                for option in [o for o in options if o in screened][:attempts]:
                    if not self.determined(
                        violation.group, now | {option}, integer=False, exact=False
                    ):
                        choice = option
                        break
                added.add(choice)
            if not added:
                # Determined only over the whole numbers: the screen cannot tell the options
                # apart, so the cheapest one around each group is hidden.
                for violation in found:
                    options = self._candidates(violation, hidden | added)
                    if not options:
                        raise Unprotectable(
                            f"a protected group of {violation.customers} customers stays "
                            "determined with nothing left to hide around it"
                        )
                    added.add(options[0])
            hidden |= added
        return {key for v in hidden if self.vars[v].published for key in self.vars[v].members}

    def determined_sums(self, dimension: str) -> list[tuple[frozenset[int], float]]:
        """Every set of ``dimension``'s levels whose churn, summed, the published counts give,
        and that sum: what an outsider can read of the dimension from every table at once."""
        hidden = {v for v, var in enumerate(self.vars) if var.hidden}
        k = len(self.levels[dimension])
        out = []
        for size in range(1, k + 1):
            for subset in itertools.combinations(range(k), size):
                variables = {self.var_of[("m", dimension, i)] for i in subset}
                if self.determined(variables, hidden):
                    out.append((frozenset(subset), self._sum(variables, hidden)))
        return out

    def _sum(self, variables: set[int], hidden: set[int]) -> float:
        total = 0.0
        for component in self._components(hidden):
            part = variables & set(component)
            if part:
                point = self._cached(component, hidden, True).point
                total += sum(point[component.index(v)] for v in part)
        return total + sum(self._value(v) for v in variables if self._known(v, hidden))


@dataclass
class _Violation:
    component: list[int]
    group: set[int]
    customers: int


class _Analysis:
    """One connected set of unknown counts: what its equations, bounds and whole numbers pin."""

    def __init__(self, system: Joint, component: list[int], rows: list, *, integer: bool) -> None:
        self.system = system
        self.component = component
        self.index = {v: i for i, v in enumerate(component)}
        n = len(component)
        vars_ = [system.vars[v] for v in component]
        self.lower = np.zeros(n)
        self.upper = np.array([float(v.customers) for v in vars_])
        eq, ineq = [], []
        self.eq_ids: list[int] = []
        self.ineq_ids: list[int] = []
        for r, free, rhs, equality in rows:
            line = np.zeros(n)
            for v, c in free.items():
                line[self.index[v]] = c
            (eq if equality else ineq).append((line, rhs))
            (self.eq_ids if equality else self.ineq_ids).append(r)
        self.a_eq = np.array([line for line, _ in eq]) if eq else np.zeros((0, n))
        self.b_eq = np.array([rhs for _, rhs in eq]) if eq else np.zeros(0)
        self.a_ub = np.array([line for line, _ in ineq]) if ineq else np.zeros((0, n))
        self.b_ub = np.array([rhs for _, rhs in ineq]) if ineq else np.zeros(0)
        self.network = self._network()
        self.point = self._feasible()
        self.pinned = self._pinned()
        self.extra: list[np.ndarray] = []
        if integer and not self.network:
            self.extra = self._integer_equalities()
        self._rows = self._equality_rows()

    # A component is a network's when its unknowns are one table's cells (margins fixed) or
    # one dimension's margins: its linear programs then have whole-number vertices.
    def _network(self) -> bool:
        if len(self.b_ub) or any(v in self.system.derived for v in self.component):
            return False
        members = [m for v in self.component for m in self.system.vars[v].members]
        tables = {(m[1], m[3]) if m[0] == "c" else ("m", m[1]) for m in members}
        if len(tables) != 1:
            return False
        return len(members) == len(self.component)

    def _feasible(self) -> np.ndarray:
        system = self.system
        values = [system.vars[v].value for v in self.component]
        if all(v is not None for v in values):
            return np.array(values, dtype=float)
        result = milp(
            np.zeros(len(self.component)),
            integrality=np.ones(len(self.component)),
            bounds=Bounds(self.lower, self.upper),
            constraints=self._constraints(),
        )
        if result.x is None:
            raise ValueError("the published counts admit no completion: they disagree")
        return np.rint(result.x)

    def _constraints(
        self, extra: Sequence[tuple[np.ndarray, float]] = ()
    ) -> list[LinearConstraint]:
        out = []
        if len(self.b_eq):
            out.append(LinearConstraint(self.a_eq, self.b_eq, self.b_eq))
        if len(self.b_ub):
            out.append(LinearConstraint(self.a_ub, -np.inf, self.b_ub))
        for line, value in extra:
            out.append(LinearConstraint(line[None, :], value, value))
        return out

    def _pinned(self) -> dict[int, float]:
        """The unknowns at a bound in every completion, and inequalities tight in every one,
        by the standard iteration: maximise how far the candidates can move off their
        bounds; one that moves is free, and when none of those left can, all are pinned."""
        n = len(self.component)
        m_ub = len(self.b_ub)
        candidates = set(range(n))
        tight = set(range(m_ub))
        while candidates or tight:
            cand, tig = sorted(candidates), sorted(tight)
            k = len(cand) + len(tig)
            # variables: x (n), t (k)
            c = np.concatenate([np.zeros(n), -np.ones(k)])
            a_ub, b_ub = [], []
            for slot, j in enumerate(cand):
                for sign, bound in ((-1.0, self.lower[j]), (1.0, self.upper[j])):
                    line = np.zeros(n + k)
                    line[j] = sign
                    line[n + slot] = 1.0
                    a_ub.append(line)
                    b_ub.append(-bound if sign < 0 else bound)
            for slot, r in enumerate(tig, start=len(cand)):
                line = np.zeros(n + k)
                line[:n] = self.a_ub[r]
                line[n + slot] = 1.0
                a_ub.append(line)
                b_ub.append(self.b_ub[r])
            for r in range(m_ub):
                line = np.zeros(n + k)
                line[:n] = self.a_ub[r]
                a_ub.append(line)
                b_ub.append(self.b_ub[r])
            a_eq = np.hstack([self.a_eq, np.zeros((len(self.b_eq), k))]) if len(self.b_eq) else None
            bounds = [(lo, hi) for lo, hi in zip(self.lower, self.upper, strict=True)] + [
                (0, 1)
            ] * k
            result = linprog(
                c,
                A_ub=np.array(a_ub) if a_ub else None,
                b_ub=np.array(b_ub) if b_ub else None,
                A_eq=a_eq,
                b_eq=self.b_eq if len(self.b_eq) else None,
                bounds=bounds,
                method="highs",
            )
            if result.status != 0:
                raise ValueError("the published counts admit no completion: they disagree")
            t = result.x[n:]
            free_vars = {j for slot, j in enumerate(cand) if t[slot] > _TOL}
            free_rows = {r for slot, r in enumerate(tig, start=len(cand)) if t[slot] > _TOL}
            if not free_vars and not free_rows:
                break
            candidates -= free_vars
            tight -= free_rows
        self.tight = sorted(tight)
        return {j: float(np.rint(self.point[j])) for j in sorted(candidates)}

    def _moving(self) -> list[int]:
        return [j for j in range(len(self.component)) if j not in self.pinned]

    def _equality_rows(self) -> np.ndarray:
        """The equations over the moving unknowns: the published ones, the tight
        inequalities, and the whole-number completions' own."""
        moving = self._moving()
        rows = list(self.a_eq)
        rows += [self.a_ub[r] for r in self.tight]
        rows += self.extra
        if not rows:
            return np.zeros((0, len(moving)))
        return np.array(rows)[:, moving] if moving else np.zeros((len(rows), 0))

    def _integer_equalities(self) -> list[np.ndarray]:
        """Equations every whole-number completion keeps that the real ones need not.

        The real completions move in a space of directions (the equations' null space, the
        pinned counts held). A direction of it that is neither spanned by differences of
        whole-number completions found so far nor already known to be constant is
        optimised both ways over the whole numbers: the same value both ways makes it a new
        equation; else the two points' difference widens the span. Each step settles one
        dimension, so it takes at most twice that many mixed integer programs.
        """
        n = len(self.component)
        if not self._moving():
            return []
        fixed_rows = list(self.a_eq) + [self.a_ub[r] for r in self.tight]
        for j in self.pinned:
            line = np.zeros(n)
            line[j] = 1.0
            fixed_rows.append(line)
        matrix = np.array(fixed_rows) if fixed_rows else np.zeros((0, n))
        free = _null_space(matrix, n)
        d = free.shape[1]
        if d == 0:
            return []
        settled: list[np.ndarray] = []  # orthonormal, in the free space's coordinates
        found: list[np.ndarray] = []
        bounds = Bounds(self.lower, self.upper)
        base = self._constraints()
        for _ in range(d):
            direction = None
            for i in range(d):
                c = np.zeros(d)
                c[i] = 1.0
                for q in settled:
                    c -= (q @ c) * q
                if np.linalg.norm(c) > 1e-6:
                    direction = c / np.linalg.norm(c)
                    break
            if direction is None:
                break
            w = free @ direction
            extremes = []
            for sign in (1.0, -1.0):
                result = milp(sign * w, integrality=np.ones(n), bounds=bounds, constraints=base)
                if result.x is None:
                    raise ValueError("the published counts admit no whole-number completion")
                extremes.append(np.rint(result.x))
            spread = extremes[0] - extremes[1]
            if abs(float(w @ spread)) < 1e-6:
                found.append(w)
                settled.append(direction)
                continue
            c = free.T @ spread
            for q in settled:
                c -= (q @ c) * q
            settled.append(c / np.linalg.norm(c))
        return found

    # --- groups -----------------------------------------------------------------------------

    def determines(self, part: set[int], *, exact: bool = True) -> bool:
        local = [self.index[v] for v in part]
        moving = self._moving()
        at = {j: i for i, j in enumerate(moving)}
        target = np.zeros(len(moving))
        for j in local:
            if j in at:
                target[at[j]] = 1.0
        if not target.any():
            return True
        return _in_row_space(self._rows, target, exact=exact and not self.extra)

    def freeing(self, group: set[int], options: Sequence[int]) -> list[int]:
        """Which of ``options`` (published counts not hidden), hidden alone, would leave
        ``group``'s sum undetermined - a screen, read off the equations that give the sum.

        The sum is a combination ``y`` of the equations; hiding a count adds it to each
        equation it is in. The sum stays determined exactly when some combination giving it
        also cancels the new count: so a count frees the group when every such combination
        meets it the same, non-zero, way. Bound-pinned counts and the equations a hidden
        count would bring along are not read: the next round decides for certain."""
        moving = self._moving()
        rows = self._rows
        if not moving or rows.shape[0] == 0:
            return []
        at = {j: i for i, j in enumerate(moving)}
        target = np.zeros(len(moving))
        for v in group:
            j = self.index.get(v)
            if j is not None and j in at:
                target[at[j]] = 1.0
        if not target.any():
            return []
        try:
            y0, *_ = np.linalg.lstsq(rows.T, target, rcond=None)
            u, s, _ = np.linalg.svd(rows, full_matrices=True)
        except np.linalg.LinAlgError:
            return []
        rank = int((s > 1e-9 * max(float(s.max()) if s.size else 0.0, 1.0)).sum())
        left = u[:, rank:]
        ids = self.eq_ids + [self.ineq_ids[r] for r in self.tight]
        out = []
        for option in options:
            column = np.zeros(rows.shape[0])
            for i, r in enumerate(ids):
                column[i] = self.system.rows[r].coefficients.get(option, 0)
            if not column.any():
                continue
            if np.abs(left.T @ column).max(initial=0.0) < 1e-9 and abs(float(column @ y0)) > 1e-9:
                out.append(option)
        return out

    def violations(self) -> list[_Violation]:
        """Once per component: what it holds does not change while it is the same."""
        if not hasattr(self, "_violations"):
            self._violations = self._find_violations()
        return self._violations

    def _find_violations(self) -> list[_Violation]:
        """Every count the component pins on its own that the rule forbids; failing those,
        the smallest forbidden group of each kind (few churners, few stayers, alone only)."""
        system = self.system
        out = []
        singles = dict(self.pinned)
        moving = self._moving()
        if moving and self._rows.shape[0]:
            free = _null_space(self._rows, len(moving))
            for i, j in enumerate(moving):
                if np.abs(free[i]).max(initial=0.0) < 1e-9:
                    singles[j] = float(self.point[j])
        for j, value in sorted(singles.items()):
            v = self.component[j]
            var = system.vars[v]
            if var.alone or not _keeps(value, var.customers, system.minimum):
                out.append(_Violation(self.component, {v}, var.customers))
        if out or not moving:
            return out
        values = self.point[moving]
        customers = np.array(
            [system.vars[self.component[j]].customers for j in moving], dtype=float
        )
        alone = np.array([system.vars[self.component[j]].alone for j in moving])
        for search in ("churned", "stayed", "alone"):
            group = self._search(moving, values, customers, alone, search)
            if group is not None:
                vs = {self.component[moving[i]] for i in group}
                out.append(_Violation(self.component, vs, int(customers[list(group)].sum())))
        return out

    def _search(self, moving, values, customers, alone, search) -> list[int] | None:
        """The smallest group of moving unknowns whose sum the equations determine and the
        rule forbids, by a mixed integer program over z (in the group) and y (the
        combination of equations that gives its sum); each answer confirmed exactly."""
        rows = self._rows
        m, k = rows.shape
        if m == 0:
            return None
        minimum = self.system.minimum
        order = np.array([self.system.vars[self.component[j]].order for j in moving], dtype=float)
        cost = np.concatenate([np.zeros(m), customers + order / (order.max() + 1.0)])
        a = np.hstack([rows.T, -np.eye(k)])
        constraints = [LinearConstraint(a, 0, 0)]
        constraints.append(
            LinearConstraint(np.concatenate([np.zeros(m), np.ones(k)])[None, :], 1, np.inf)
        )
        if search == "churned":
            constraints.append(
                LinearConstraint(
                    np.concatenate([np.zeros(m), values])[None, :], -np.inf, minimum - 0.5
                )
            )
        elif search == "stayed":
            constraints.append(
                LinearConstraint(
                    np.concatenate([np.zeros(m), customers - values])[None, :],
                    -np.inf,
                    minimum - 0.5,
                )
            )
        upper = np.concatenate(
            [np.full(m, np.inf), np.where(alone, 1.0, 0.0) if search == "alone" else np.ones(k)]
        )
        lower = np.concatenate([np.full(m, -np.inf), np.zeros(k)])
        integrality = np.concatenate([np.zeros(m), np.ones(k)])
        for _ in range(25):
            result = milp(
                cost, integrality=integrality, bounds=Bounds(lower, upper), constraints=constraints
            )
            if result.status == 2:  # infeasible: no group of this kind is determined
                return None
            if result.x is None:
                # The solver stopped without an answer: read the component as determined
                # rather than as safe.
                logger.warning("the group search failed: treating the component as determined")
                return list(range(k))
            z = np.rint(result.x[m:])
            group = [i for i in range(k) if z[i] > 0.5]
            if not group:
                return None
            target = np.zeros(k)
            target[group] = 1.0
            if _in_row_space(rows, target, exact=not self.extra):
                return group
            cut = np.zeros(m + k)
            cut[[m + i for i in group]] = 1.0
            constraints.append(LinearConstraint(cut[None, :], -np.inf, len(group) - 1))
        logger.warning("the group search did not settle: treating the component as determined")
        return list(range(k))


def _keeps(churned: float, customers: int, minimum: int) -> bool:
    stayed = customers - churned
    return (abs(churned) < 0.5 and abs(stayed) < 0.5) or (
        churned >= minimum - 1e-6 and stayed >= minimum - 1e-6
    )


def _null_space(matrix: np.ndarray, n: int) -> np.ndarray:
    if matrix.shape[0] == 0:
        return np.eye(n)
    _, s, vt = np.linalg.svd(matrix, full_matrices=True)
    rank = int((s > 1e-9 * max(float(s.max()) if s.size else 0.0, 1.0)).sum())
    return vt[rank:].T


def _orth(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix.reshape(matrix.shape[0], 0)
    u, s, _ = np.linalg.svd(matrix, full_matrices=False)
    rank = int((s > 1e-9 * max(float(s.max()) if s.size else 0.0, 1.0)).sum())
    return u[:, :rank]


def _in_row_space(rows: np.ndarray, target: np.ndarray, *, exact: bool) -> bool:
    """Whether ``target`` is a combination of ``rows``: modulo two primes when every row is
    whole-numbered, else by least squares."""
    if rows.shape[0] == 0:
        return not target.any()
    whole_numbered = np.allclose(rows, np.rint(rows))
    if exact and whole_numbered:
        return _in_row_space_mod(rows, target)
    try:
        solution, *_ = np.linalg.lstsq(rows.T, target, rcond=None)
    except np.linalg.LinAlgError:
        # The floating-point solver gave up: decide exactly where the rows allow it, and
        # otherwise read the sum as determined - the side that hides more.
        return _in_row_space_mod(rows, target) if whole_numbered else True
    return bool(np.abs(rows.T @ solution - target).max() < 1e-6)


def _in_row_space_mod(rows: np.ndarray, target: np.ndarray) -> bool:
    whole = np.rint(rows).astype(np.int64)
    goal = np.rint(target).astype(np.int64)
    return all(_rank_mod(whole, p) == _rank_mod(np.vstack([whole, goal]), p) for p in _PRIMES)


def _rank_mod(matrix: np.ndarray, p: int) -> int:
    m = np.mod(matrix, p).astype(np.int64)
    rank = 0
    rows, cols = m.shape
    for col in range(cols):
        if rank == rows:
            break
        pivots = np.nonzero(m[rank:, col])[0]
        if pivots.size == 0:
            continue
        pivot = rank + int(pivots[0])
        if pivot != rank:
            m[[rank, pivot]] = m[[pivot, rank]]
        inverse = pow(int(m[rank, col]), p - 2, p)
        m[rank] = (m[rank] * inverse) % p
        others = np.nonzero(m[:, col])[0]
        others = others[others != rank]
        if others.size:
            factors = m[others, col][:, None]
            m[others] = (m[others] - (factors * m[rank]) % p) % p
        rank += 1
    return rank
