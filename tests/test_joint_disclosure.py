"""The training tables guarded as one system (``churnsense.joint_disclosure``), checked by an
independent outsider (``tests.joint_oracle``): after the guard, no published count - read
with every other, the same customers counted twice, one group inside another, and the
bounds - determines a hidden group with fewer than ``minimum`` churners or stayers, nor any
group of ``alone`` counts only. Before it, the tables guarded one by one do leak.
"""

from __future__ import annotations

import itertools
import random

import numpy as np
import pytest

from churnsense import joint_disclosure as jd
from churnsense import suppression
from tests.joint_oracle import Outsider, Population, violations

MIN = 20


def _population(levels: dict, groups: dict) -> Population:
    """Customers built from ``groups``: a combination of level indices -> (customers, churned)."""
    rows, churned = [], []
    order = list(levels)
    for combo, (n, c) in groups.items():
        for k in range(n):
            rows.append(dict(zip(order, combo, strict=True)))
            churned.append(1 if k < c else 0)
    return Population(levels, rows, churned)


def _guarded(pop: Population, hidden, *, minimum: int = MIN, alone=()) -> set:
    system = jd.Joint(*pop.joint_input(hidden), alone=alone, minimum=minimum)
    return system.guard()


def _alone_keys(pop: Population, alone: dict) -> set:
    keys = set()
    for key in pop.all_keys():
        parts = [(key[1], key[2])] if key[0] == "m" else [(key[1], key[2]), (key[3], key[4])]
        if any(pop.levels[d][i] in alone.get(d, ()) for d, i in parts):
            keys.add(key)
    return keys


# --- the review's example: 403/92 less 313/75 gives 90/17 -------------------------------------


REVIEW_LEVELS = {"A": ("a0", "a1", "a2"), "B": ("b0", "b1", "b2")}
REVIEW_GROUPS = {
    (0, 2): (313, 75),
    (1, 0): (200, 60),
    (1, 1): (150, 40),
    (1, 2): (90, 17),
    (2, 0): (300, 90),
    (2, 1): (250, 70),
}
#: What each table protected on its own hides: the small cell, and its complement in the
#: column - which is the whole of row a0, the very count margin a0 shows.
PER_TABLE = {("c", "A", 1, "B", 2), ("c", "A", 0, "B", 2)}


def test_the_reviews_subtraction_is_open_before_and_closed_after():
    pop = _population(REVIEW_LEVELS, REVIEW_GROUPS)
    customers, _ = pop.counts()
    assert customers[("m", "B", 2)] == 403 and customers[("m", "A", 0)] == 313
    before = Outsider(pop.published(PER_TABLE))
    assert before.determined({("c", "A", 1, "B", 2): 1}) == 17  # 92 - 75: the leak
    hidden = _guarded(pop, PER_TABLE)
    after = pop.published(hidden)
    assert Outsider(after).determined({("c", "A", 1, "B", 2): 1}) is None
    assert violations(after, minimum=MIN, size=3) == []
    # The same customers are hidden wherever they appear: cell (a0, b2) is margin a0.
    assert ("m", "A", 0) in hidden and ("c", "A", 0, "B", 2) in hidden


def test_the_reverse_direction_is_closed_too():
    """The same tables with the dimensions the other way round: a row margin less a column
    margin that holds a single group."""
    levels = {"B": REVIEW_LEVELS["B"], "A": REVIEW_LEVELS["A"]}
    groups = {(j, i): v for (i, j), v in REVIEW_GROUPS.items()}
    pop = _population(levels, groups)
    per_table = {("c", "B", 2, "A", 1), ("c", "B", 2, "A", 0)}
    assert Outsider(pop.published(per_table)).determined({("c", "B", 2, "A", 1): 1}) == 17
    hidden = _guarded(pop, per_table)
    assert violations(pop.published(hidden), minimum=MIN, size=3) == []


def test_a_hidden_margin_rebuilt_from_a_row_of_shown_cells_is_closed():
    """a2 is small and hidden, a1 hidden beside it in the one-way table; but a1's row in
    the two-way table is shown in full, so a1 - and with it a2 - follows."""
    levels = {"A": ("a0", "a1", "a2"), "B": ("b0", "b1")}
    groups = {
        (0, 0): (300, 80),
        (0, 1): (300, 90),
        (1, 0): (200, 50),
        (1, 1): (200, 60),
        (2, 0): (15, 3),
        (2, 1): (15, 2),
    }
    pop = _population(levels, groups)
    per_table = {("m", "A", 2), ("m", "A", 1), ("c", "A", 2, "B", 0), ("c", "A", 2, "B", 1)}
    outsider = Outsider(pop.published(per_table))
    assert outsider.determined({("m", "A", 2): 1}) == 5
    hidden = _guarded(pop, per_table)
    assert violations(pop.published(hidden), minimum=MIN, size=3) == []
    assert any(k[0] == "c" and k[2] == 1 for k in hidden)  # a cell of row a1 is hidden


def test_a_stayer_count_is_protected_as_a_churner_count_is():
    """Thirty customers, twenty-five churners: the five stayers are the small group."""
    groups = dict(REVIEW_GROUPS)
    groups[(1, 2)] = (30, 25)
    groups[(0, 2)] = (313, 75)
    pop = _population(REVIEW_LEVELS, groups)
    before = Outsider(pop.published(PER_TABLE))
    assert before.determined({("c", "A", 1, "B", 2): 1}) == 25
    hidden = _guarded(pop, PER_TABLE)
    after = Outsider(pop.published(hidden))
    assert after.determined({("c", "A", 1, "B", 2): 1}) is None
    assert violations(pop.published(hidden), minimum=MIN, size=3) == []


# --- several tables -----------------------------------------------------------------------------


def _per_table(pop: Population, *, minimum: int = MIN, alone=()) -> set:
    """Each table guarded on its own (``churnsense.suppression``), as before the joint guard."""
    customers, churned = pop.counts()
    hidden = set()
    margins = {}
    for d, levels in pop.levels.items():
        n = [customers[("m", d, i)] for i in range(len(levels))]
        c = [churned[("m", d, i)] for i in range(len(levels))]
        breaks = {i for i in range(len(levels)) if n[i] and not _keeps(c[i], n[i], minimum)}
        lone = {i for i in range(len(levels)) if levels[i] in dict(alone).get(d, ())}
        out = suppression.guard_one_way(
            n, c, sum(c), hidden=breaks | lone, alone=lone, minimum=minimum
        )
        hidden |= {("m", d, i) for i in out}
        margins[d] = [None if i in out else c[i] for i in range(len(levels))]
    for a, b in itertools.combinations(pop.levels, 2):
        la, lb = pop.levels[a], pop.levels[b]
        n = np.array(
            [[customers[("c", a, i, b, j)] for j in range(len(lb))] for i in range(len(la))]
        )
        c = [[churned[("c", a, i, b, j)] for j in range(len(lb))] for i in range(len(la))]
        breaks = {
            (i, j)
            for i in range(len(la))
            for j in range(len(lb))
            if n[i, j] and not _keeps(c[i][j], int(n[i, j]), minimum)
        }
        out = suppression.guard_two_way(
            n, c, margins[a], margins[b], sum(map(sum, c)), hidden=breaks, minimum=minimum
        )
        hidden |= {("c", a, i, b, j) for i, j in out}
    return hidden


def _keeps(churned: int, customers: int, minimum: int) -> bool:
    stayed = customers - churned
    return (churned == 0 and stayed == 0) or (churned >= minimum and stayed >= minimum)


def test_overlapping_margins_across_tables_are_closed_together():
    """E's first level lies inside A's first, so cell (a0, e0) is margin e0: A x E guarded on
    its own leaves cell (a0, e1) as margin a0 less margin e0 - two one-way tables and a
    two-way one together. The joint guard closes it."""
    pop, alone = _aliased(101, size=180)
    per_table = _per_table(pop, minimum=8, alone=alone)
    keys = _alone_keys(pop, alone)
    before = violations(pop.published(per_table), minimum=8, alone=keys, size=1)
    assert ("sum", (("c", "A", 0, "E", 1),), 6) in before
    hidden = _guarded(pop, per_table, minimum=8, alone=alone)
    assert per_table <= hidden
    assert violations(pop.published(hidden), minimum=8, alone=keys, size=2) == []


def test_hiding_a_margin_reaches_every_table_it_is_a_margin_of():
    """Once a margin is hidden, no table may show the rest of what would give it: every
    hidden margin is open or keeps the rule, read with all the tables."""
    pop, alone = _aliased(101, size=180)
    hidden = _guarded(pop, _per_table(pop, minimum=8, alone=alone), minimum=8, alone=alone)
    published = pop.published(hidden)
    outsider = Outsider(published)
    margins = [key for key in hidden if key[0] == "m"]
    assert margins
    for key in margins:
        value = outsider.determined({key: 1})
        assert value is None or _keeps(value, published.customers[key], 8), key


# --- the same customers, one group inside another -------------------------------------------


def _aliased(seed: int, size: int = 220) -> tuple[Population, dict]:
    """A, B, a D whose last level is exactly A's last ("no internet service"), and an E whose
    first level lies inside A's first (nested), with one alone level of B."""
    rng = random.Random(seed)
    levels = {
        "A": ("a0", "a1", "a2"),
        "B": ("b0", "b1", "b2"),
        "D": ("d0", "d1", "none"),
        "E": ("e0", "e1"),
    }
    rows, churned = [], []
    for _ in range(size):
        a, b = rng.choice((0, 0, 1, 1, 2)), rng.choice((0, 0, 1, 2))
        d = 2 if a == 2 else rng.choice((0, 1))
        e = 0 if a == 0 and rng.random() < 0.6 else 1
        rows.append({"A": a, "B": b, "D": d, "E": e})
        churned.append(1 if rng.random() < (0.15 + 0.25 * (b == 0) + 0.2 * (a == 1)) else 0)
    return Population(levels, rows, churned), {"B": ("b2",)}


def test_the_identities_and_the_nesting_are_read_off_the_published_sizes():
    pop, alone = _aliased(1)
    system = jd.Joint(*pop.joint_input(set()), alone=alone, minimum=8)
    assert system.var_of[("m", "A", 2)] == system.var_of[("m", "D", 2)]
    for b in range(3):
        assert system.var_of[("c", "A", 2, "B", b)] == system.var_of[("c", "B", b, "D", 2)]
    nested = {
        (system.vars[large].members[0], system.vars[small].members[0])
        for large, small in system.derived.values()
    }
    assert (("m", "A", 0), ("m", "E", 0)) in nested


def test_the_same_customers_are_hidden_in_every_table_or_none():
    pop, alone = _aliased(2)
    hidden = _guarded(pop, _per_table(pop, minimum=8, alone=alone), minimum=8, alone=alone)
    published = pop.published(hidden)
    for x, y in published.same:
        assert (x in hidden) == (y in hidden), (x, y)


def test_a_difference_of_nested_groups_is_protected():
    """(a0, x) less (e0, x) is the group of a0 outside e0 in x: never published, protected."""
    pop, alone = _aliased(4)
    hidden = _guarded(pop, _per_table(pop, minimum=8, alone=alone), minimum=8, alone=alone)
    found = violations(pop.published(hidden), minimum=8, alone=_alone_keys(pop, alone), size=1)
    assert [f for f in found if f[0] == "difference"] == []


def test_alone_groups_are_never_determined_in_any_table():
    pop, alone = _aliased(5)
    keys = _alone_keys(pop, alone)
    hidden = _guarded(pop, _per_table(pop, minimum=8, alone=alone) | keys, minimum=8, alone=alone)
    assert keys <= hidden
    outsider = Outsider(pop.published(hidden))
    for key in sorted(keys, key=str):
        if pop.published(hidden).customers[key]:
            assert outsider.determined({key: 1}) is None, key


# --- no safe publication --------------------------------------------------------------------


def test_a_partition_too_small_to_protect_is_refused():
    """Fifteen churners in all: every split of them is a small group, and the partition's
    total is published whatever is hidden. Nothing is published."""
    levels = {"A": ("a0", "a1"), "B": ("b0", "b1")}
    groups = {(0, 0): (60, 5), (0, 1): (60, 4), (1, 0): (60, 3), (1, 1): (60, 3)}
    pop = _population(levels, groups)
    with pytest.raises(jd.Unprotectable):
        _guarded(pop, set(pop.all_keys()) - {k for k in pop.all_keys() if k[0] == "m"})


def test_nothing_left_to_hide_is_refused_not_ignored(monkeypatch):
    pop = _population(REVIEW_LEVELS, REVIEW_GROUPS)
    monkeypatch.setattr(jd.Joint, "_candidates", lambda *_args: [])
    with pytest.raises(jd.Unprotectable):
        _guarded(pop, PER_TABLE)


# --- one answer, whoever asks -----------------------------------------------------------------


def test_the_published_file_needs_no_more_hiding_than_the_builder_did():
    """The builder reads true counts; a reader of the file, only what it shows. Guarded again
    from the file, nothing more is hidden: one fixpoint for both."""
    pop, alone = _aliased(6)
    hidden = _guarded(pop, _per_table(pop, minimum=8, alone=alone), minimum=8, alone=alone)
    levels, one, two, partition = pop.joint_input(hidden)
    unknown = {d: [(n, None if h else c, h) for n, c, h in cells] for d, cells in one.items()}
    unknown_two = {
        p: [[(n, None if h else c, h) for n, c, h in line] for line in grid]
        for p, grid in two.items()
    }
    again = jd.Joint(levels, unknown, unknown_two, partition, alone=alone, minimum=8).guard()
    assert again == hidden


@pytest.mark.parametrize("seed", range(20))
def test_no_completion_determines_a_small_or_alone_group(seed):
    pop, alone = _aliased(100 + seed, size=180)
    per_table = _per_table(pop, minimum=8, alone=alone)
    hidden = _guarded(pop, per_table, minimum=8, alone=alone)
    published = pop.published(hidden)
    assert violations(published, minimum=8, alone=_alone_keys(pop, alone), size=2) == []


def test_the_tables_guarded_one_by_one_do_leak_somewhere():
    """The property above is not vacuous: guarded one table at a time, some seeds leak."""
    leaking = 0
    for seed in range(20):
        pop, alone = _aliased(100 + seed, size=180)
        published = pop.published(_per_table(pop, minimum=8, alone=alone))
        if violations(published, minimum=8, alone=_alone_keys(pop, alone), size=1):
            leaking += 1
    assert leaking >= 3


# --- the lifecycle's median tenures --------------------------------------------------------


def _tenure_system(median: float, *, hidden: bool) -> jd.Joint:
    """One tenure-like dimension, buckets (-1, 3], (3, 12], (12, 24], thirty churners: a
    median of 3.5 says exactly fifteen of them are in the first bucket."""
    levels = {"T": ("0-3", "4-12", "13-24"), "X": ("x0", "x1")}
    one = {
        "T": [(100, 15, hidden), (200, 10, hidden), (300, 5, False)],
        "X": [(300, 15, False), (300, 15, False)],
    }
    two = {
        ("T", "X"): [
            [(50, 8, True), (50, 7, True)],
            [(100, 5, True), (100, 5, True)],
            [(150, 2, True), (150, 3, True)],
        ]
    }
    medians = jd.Medians(median, None, "T", (-1, 3, 12, 24))
    return jd.Joint(levels, one, two, (600, 30), medians=medians, minimum=MIN)


def test_a_median_between_two_buckets_pins_the_churners_below_it():
    """With an even count, the median 3.5 says no more than half are below 3.5 and no more
    than half above: both, so exactly half are in "0-3" - read off the bucket edges as an
    outsider does, the smallest tenure of (3, 12] being 4."""
    system = _tenure_system(3.5, hidden=True)
    first = system.var_of[("m", "T", 0)]
    hidden = {v for v, var in enumerate(system.vars) if var.hidden}
    assert system.determined({first}, hidden)
    assert round(system._sum({first}, hidden)) == 15


def test_a_median_that_gives_a_small_bucket_away_refuses_publication():
    """Fifteen churners pinned by a published median: nothing hidden can reopen it."""
    with pytest.raises(jd.Unprotectable):
        _tenure_system(3.5, hidden=True).guard()


def test_a_median_inside_a_bucket_pins_nothing():
    system = _tenure_system(8.0, hidden=True)
    first = system.var_of[("m", "T", 0)]
    hidden = {v for v, var in enumerate(system.vars) if var.hidden}
    assert not system.determined({first}, hidden)


# --- whole numbers say more than the real numbers -------------------------------------------


def _odd_cycle() -> jd.Joint:
    """Three counts of at most one, any two of which sum to at most one, and a fourth, t,
    with x1 + x2 + x3 = 1 + t. Over the reals t reaches 0.5 (each x a half); over whole
    numbers exactly one x is one, so t is 0: determined only by integrality - the kind of
    system shared margins and nested groups can make, which no network matrix does."""
    system = object.__new__(jd.Joint)
    names = ("x1", "x2", "x3", "s12", "s23", "s13", "t")
    system.vars = [
        jd._Var(
            customers=1,
            value=None,
            published=False,
            hidden=True,
            margin=False,
            alone=False,
            order=i,
            members=[("x", name)],
        )
        for i, name in enumerate(names)
    ]
    x1, x2, x3, s12, s23, s13, t = range(7)
    system.rows = [
        jd._Row({x1: 1, x2: 1, s12: 1}, 1.0),
        jd._Row({x2: 1, x3: 1, s23: 1}, 1.0),
        jd._Row({x1: 1, x3: 1, s13: 1}, 1.0),
        jd._Row({x1: 1, x2: 1, x3: 1, t: -1}, 1.0),
    ]
    system._incident = {}
    for r, row in enumerate(system.rows):
        for v in row.coefficients:
            system._incident.setdefault(v, []).append(r)
    system.derived = {}
    # One churner and one stayer suffice here, so each equation's own sum keeps the rule and
    # only t - no churner in one customer - breaks it.
    system.minimum = 1
    return system


def test_a_count_only_whole_numbers_determine_is_found():
    system = _odd_cycle()
    hidden = set(range(7))
    assert not system.determined({6}, hidden, integer=False)
    assert system.determined({6}, hidden, integer=True)


def test_a_group_only_whole_numbers_determine_is_protected_too():
    """t, one customer, is determined over the whole numbers and breaks the rule; the reals
    alone find nothing, and nothing published is left to hide, so the guard refuses."""
    system = _odd_cycle()
    hidden = set(range(7))
    real = [
        v for c in system._components(hidden) for v in system._cached(c, hidden, False).violations()
    ]
    assert real == []
    with pytest.raises(jd.Unprotectable):
        system.guard()


def test_a_group_with_enough_churners_but_few_stayers_is_found():
    """p + q = 50 of 60 customers: fifty churners, ten stayers - neither count alone is
    determined (each is anything from 20 to 30), only their sum, and only by its stayers
    does it break the rule."""
    system = object.__new__(jd.Joint)
    system.vars = [
        jd._Var(
            customers=30,
            value=None,
            published=False,
            hidden=True,
            margin=False,
            alone=False,
            order=i,
            members=[("x", name)],
        )
        for i, name in enumerate(("p", "q"))
    ]
    system.rows = [jd._Row({0: 1, 1: 1}, 50.0)]
    system._incident = {0: [0], 1: [0]}
    system.derived = {}
    system.minimum = MIN
    hidden = {0, 1}
    assert not system.determined({0}, hidden) and system.determined({0, 1}, hidden)
    with pytest.raises(jd.Unprotectable):
        system.guard()


def test_a_sum_of_alone_counts_is_never_determined_however_large():
    """Two Two-year-like counts whose sum (80 churners of 200) keeps the 20/20 rule: alone,
    their sum may still never be determined."""
    system = object.__new__(jd.Joint)
    system.vars = [
        jd._Var(
            customers=100,
            value=None,
            published=False,
            hidden=True,
            margin=False,
            alone=True,
            order=i,
            members=[("x", name)],
        )
        for i, name in enumerate(("two_year_a", "two_year_b"))
    ]
    system.rows = [jd._Row({0: 1, 1: 1}, 80.0)]
    system._incident = {0: [0], 1: [0]}
    system.derived = {}
    system.minimum = MIN
    with pytest.raises(jd.Unprotectable):
        system.guard()


def test_a_level_inside_an_alone_level_is_alone_too():
    """E's first level lies inside A's alone level: its margin and its cells are groups of
    Two-year-like customers, and are read as such wherever they appear."""
    rng = random.Random(7)
    levels = {"A": ("a0", "a1", "a2"), "B": ("b0", "b1"), "E": ("e0", "e1")}
    rows, churned = [], []
    for _ in range(300):
        a, b = rng.choice((0, 1, 2)), rng.choice((0, 1))
        rows.append({"A": a, "B": b, "E": 0 if a == 2 and rng.random() < 0.5 else 1})
        churned.append(1 if rng.random() < 0.3 else 0)
    pop = Population(levels, rows, churned)
    system = jd.Joint(*pop.joint_input(set()), alone={"A": ("a2",)}, minimum=8)
    assert system.vars[system.var_of[("m", "E", 0)]].alone
    assert system.vars[system.var_of[("c", "B", 1, "E", 0)]].alone
    assert not system.vars[system.var_of[("m", "E", 1)]].alone
