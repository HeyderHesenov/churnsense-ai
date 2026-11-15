"""The guard on what a table publishes: the class rule and counts that must stay open alone.

``churnsense.suppression.guard_one_way`` and ``guard_two_way`` hide what a table may not
show and protect it: no published figure, nor ``0 <= churned <= customers``, may fix a sum of
protected counts unless that sum has ``MIN`` churners *and* ``MIN`` stayers - and a sum of
``alone`` counts only is never fixed. Counts already hidden may be unknown (``None``): the
guard reads them as an outsider would. Checked here by brute force, on small tables, with a
linear program over every way to fill in what is hidden.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy.optimize import linprog

from churnsense import suppression

MIN = 20


def _keeps(churned: int, customers: int) -> bool:
    stayed = customers - churned
    return (churned == 0 and stayed == 0) or (churned >= MIN and stayed >= MIN)


def _range(n, churned, hidden, partition, margins, subset):
    """The least and most ``subset``'s summed churn can be, over every completion.

    One-way when ``margins`` is None; else (row margins, column margins) of known values,
    ``None`` where hidden."""
    flat_n = np.asarray(n).ravel()
    size = flat_n.size
    a_eq, b_eq = [], []
    a_eq.append(np.ones(size))
    b_eq.append(partition)
    if margins is not None:
        _rows, columns = np.shape(n)
        for r, m in enumerate(margins[0]):
            if m is not None:
                row = np.zeros(size)
                row[r * columns : (r + 1) * columns] = 1
                a_eq.append(row)
                b_eq.append(m)
        for k, m in enumerate(margins[1]):
            if m is not None:
                col = np.zeros(size)
                col[k::columns] = 1
                a_eq.append(col)
                b_eq.append(m)
    flat = list(np.asarray(churned, dtype=object).ravel())
    bounds = [
        (0, int(flat_n[i])) if i in hidden else (int(flat[i]), int(flat[i])) for i in range(size)
    ]
    objective = np.zeros(size)
    objective[list(subset)] = 1
    low = linprog(objective, A_eq=np.array(a_eq), b_eq=b_eq, bounds=bounds, method="highs").fun
    high = -linprog(-objective, A_eq=np.array(a_eq), b_eq=b_eq, bounds=bounds, method="highs").fun
    return round(low), round(high)


def _violations(n, truth, hidden, protected, alone, partition, margins=None):
    """Every set of protected hidden counts an outsider can fix that the rule forbids."""
    flat_n = np.asarray(n).ravel()
    flat_truth = np.asarray(truth).ravel()
    found = []
    candidates = sorted(i for i in protected if i in hidden and flat_n[i] > 0)
    for size in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            low, high = _range(n, truth, hidden, partition, margins, subset)
            if low != high:
                continue
            churned = int(sum(flat_truth[i] for i in subset))
            customers = int(sum(flat_n[i] for i in subset))
            if not _keeps(churned, customers) or set(subset) <= set(alone):
                found.append(subset)
    return found


# --- one-way ---------------------------------------------------------------------------------


def test_a_level_that_must_stay_open_alone_is_hidden_with_its_cheapest_complement():
    customers, churned = [900, 400, 300], [400, 90, 25]
    hidden = suppression.guard_one_way(
        customers, churned, sum(churned), hidden={2}, alone={2}, minimum=MIN
    )
    # The smaller of the others goes with it; the sum of the two (115 of 700) keeps the rule.
    assert hidden == {1, 2}


def test_a_group_the_guard_cannot_protect_hides_the_whole_table(monkeypatch, caplog):
    """Should no hiding keep a protected group open, the guard fails closed - every count the
    table may hide - as it does when a solver fails, never with only a warning."""
    monkeypatch.setattr(suppression._Table, "protect", lambda _self, _group: set())
    customers, churned = [900, 400, 300], [400, 90, 25]
    hidden = suppression.guard_one_way(
        customers, churned, sum(churned), hidden={2}, alone={2}, minimum=MIN
    )
    assert hidden == {0, 1, 2}
    assert "hiding every count" in caplog.text


def test_a_complement_whose_sum_breaks_the_rule_takes_the_third_level_too():
    customers, churned = [900, 400, 300], [400, 6, 6]
    hidden = suppression.guard_one_way(
        customers, churned, sum(churned), hidden={1, 2}, alone={2}, minimum=MIN
    )
    assert hidden == {0, 1, 2}


def test_a_fixed_sum_that_keeps_the_rule_is_not_protected_further():
    """Two hidden levels whose total keeps the rule may be known together: no third is hidden."""
    customers, churned = [900, 400, 300], [400, 30, 25]
    hidden = suppression.guard_one_way(customers, churned, sum(churned), hidden={1, 2}, minimum=MIN)
    assert hidden == {1, 2}


def test_an_unknown_count_is_judged_by_what_the_published_figures_fix():
    """An already-hidden level of unknown churn: the partition's total less the shown levels
    fixes it, so an outsider knows it. Thirty churners and 370 stayers keep the rule and it
    may stay alone; ten do not, and the cheapest other level is hidden with it."""
    customers = [900, 400, 300]
    assert suppression.guard_one_way(customers, [400, None, 25], 455, hidden=(), minimum=MIN) == {1}
    assert suppression.guard_one_way(customers, [400, None, 25], 435, hidden=(), minimum=MIN) == {
        1,
        2,
    }


@pytest.mark.parametrize("seed", range(40))
def test_no_one_way_completion_fixes_what_must_stay_open(seed):
    rng = np.random.default_rng(seed)
    levels = int(rng.integers(3, 6))
    customers = rng.integers(15, 400, levels).tolist()
    churned = [
        int(rng.integers(0, n + 1)) if rng.random() < 0.7 else int(rng.integers(0, 15))
        for n in customers
    ]
    churned = [min(c, n) for c, n in zip(churned, customers, strict=True)]
    breaks = {
        i for i, (c, n) in enumerate(zip(churned, customers, strict=True)) if not _keeps(c, n)
    }
    alone = {int(rng.integers(0, levels))} if rng.random() < 0.5 else set()
    hidden = suppression.guard_one_way(
        customers,
        churned,
        sum(churned),
        hidden=breaks | alone,
        alone=alone,
        minimum=MIN,
    )
    assert breaks | alone <= hidden
    assert _violations(customers, churned, hidden, breaks | alone, alone, sum(churned)) == []


# --- two-way ---------------------------------------------------------------------------------


def _table(seed: int):
    rng = np.random.default_rng(seed)
    rows, columns = int(rng.integers(2, 4)), int(rng.integers(2, 4))
    n = rng.integers(10, 160, (rows, columns))
    rate = rng.uniform(0.02, 0.6, (rows, columns))
    c = np.minimum(n, np.round(n * rate)).astype(int)
    return n, c


def _margins(n_line, c_line):
    """A dimension's own one-way table, guarded as ``insights`` guards it: ``None`` where hidden."""
    breaks = {
        i for i, (c, n) in enumerate(zip(c_line, n_line, strict=True)) if n and not _keeps(c, n)
    }
    hidden = suppression.guard_one_way(n_line, c_line, sum(c_line), hidden=breaks, minimum=MIN)
    return [None if i in hidden else c for i, c in enumerate(c_line)]


def _two_way(n, c, alone=frozenset()):
    rows, columns = n.shape
    row_m = _margins([int(x) for x in n.sum(axis=1)], [int(x) for x in c.sum(axis=1)])
    col_m = _margins([int(x) for x in n.sum(axis=0)], [int(x) for x in c.sum(axis=0)])
    breaks = {
        (r, k)
        for r in range(rows)
        for k in range(columns)
        if n[r, k] and not _keeps(c[r, k], n[r, k])
    }
    cells = suppression.guard_two_way(
        n,
        c.tolist(),
        row_m,
        col_m,
        int(c.sum()),
        hidden=breaks | set(alone),
        alone=alone,
        minimum=MIN,
    )
    return cells, breaks, (row_m, col_m)


def _hidden_lines_fixed(n, c, flat, margins):
    """The hidden margins whose value the published cells and totals fix: a line's sum."""
    rows, columns = n.shape
    fixed = []
    for r, m in enumerate(margins[0]):
        line = [r * columns + k for k in range(columns)]
        if m is None and len(set(_range(n, c, flat, int(c.sum()), margins, line))) == 1:
            fixed.append(("row", r))
    for k, m in enumerate(margins[1]):
        line = [r * columns + k for r in range(rows)]
        if m is None and len(set(_range(n, c, flat, int(c.sum()), margins, line))) == 1:
            fixed.append(("column", k))
    return fixed


@pytest.mark.parametrize("seed", range(30))
def test_no_two_way_completion_fixes_what_must_stay_open(seed):
    n, c = _table(seed)
    alone = frozenset({(0, 0)}) if seed % 3 == 0 else frozenset()
    cells, breaks, margins = _two_way(n, c, alone)
    assert breaks | alone <= cells
    flat_cells = {r * n.shape[1] + k for r, k in cells}
    assert _hidden_lines_fixed(n, c, flat_cells, margins) == []
    columns = n.shape[1]
    flat = {r * columns + k for r, k in cells}
    protected = {r * columns + k for r, k in breaks | alone}
    found = _violations(
        n, c, flat, protected, {r * columns + k for r, k in alone}, int(c.sum()), margins
    )
    assert found == []


def test_the_brute_force_tables_do_hide_something():
    assert sum(bool(_two_way(*_table(seed))[1]) for seed in range(30)) >= 10


def test_a_two_way_table_keeps_each_two_year_cell_open_whatever_its_size():
    """``insights.guard_cells``: a cross-partition level's cells are hidden *and* kept open
    alone - a Two year cell with plenty of churners and stayers must not follow from its
    column's margin less the shown cells, nor may any sum of Two year cells."""
    from churnsense import insights

    n = np.array([[200] * 6, [150] * 6, [120] * 6])
    churned = [[80] * 6, [40] * 6, [30] * 6]
    # The one-way tables: Two year hidden for the partition it gives away, One year with it.
    margins = ([480, None, None], [150] * 6)
    partition = 900
    reasons = insights.guard_cells(("Contract", "tenure"), n, churned, margins, partition)
    assert all(reason == "cross_partition" for reason in reasons[2])
    hidden = {r * 6 + k for r in range(3) for k in range(6) if reasons[r][k] is not None}
    two_year = [12 + k for k in range(6)]
    for size in (1, 2, 6):
        for subset in itertools.combinations(two_year, size):
            low, high = _range(n, churned, hidden, partition, margins, subset)
            assert low != high, subset
