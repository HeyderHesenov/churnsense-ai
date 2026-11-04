"""Which counts of a breakdown stay hidden, checked against an independent oracle.

The oracle (``tests.disclosure``) states each table as equations and asks a
linear program what every small group's hidden churn can sum to.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from churnsense.suppression import one_way, two_way
from tests.disclosure import MINIMUM, fixed_groups, grid_equations, line_equation


def _grid_leaks(customers, churned, cells, rows=(), cols=()):
    n, c = np.asarray(customers), np.asarray(churned)
    row_keys, col_keys = list(range(n.shape[0])), list(range(n.shape[1]))
    sizes, values = {}, {}
    for r, k in itertools.product(row_keys, col_keys):
        sizes[(r, k)], values[(r, k)] = int(n[r, k]), int(c[r, k])
    for r in row_keys:
        sizes[("row", r)], values[("row", r)] = int(n[r].sum()), int(c[r].sum())
    for k in col_keys:
        sizes[("col", k)], values[("col", k)] = int(n[:, k].sum()), int(c[:, k].sum())
    sizes["all"], values["all"] = int(n.sum()), int(c.sum())
    hidden = set(cells) | {("row", r) for r in rows} | {("col", k) for k in cols}
    return fixed_groups(sizes, values, hidden, grid_equations(row_keys, col_keys))


def _line_leaks(customers, churned, levels):
    sizes = dict(enumerate(customers)) | {"all": sum(customers)}
    values = dict(enumerate(churned)) | {"all": sum(churned)}
    return fixed_groups(sizes, values, set(levels), line_equation(list(range(len(customers)))))


# --- one-way ----------------------------------------------------------------------------------


def test_a_lone_small_level_takes_the_smallest_other_level_with_it():
    assert one_way([300, 15, 60, 40], [90, 5, 20, 8], MINIMUM) == {1, 3}
    assert _line_leaks([300, 15, 60, 40], [90, 5, 20, 8], {1}) == {(1,)}
    assert _line_leaks([300, 15, 60, 40], [90, 5, 20, 8], {1, 3}) == set()


def test_small_levels_that_together_reach_the_minimum_and_stay_open_hide_nothing_more():
    assert one_way([100, 12, 10], [30, 3, 4], MINIMUM) == {1, 2}


def test_small_levels_whose_sum_covers_too_few_customers_hide_another():
    # 8 + 9 customers: their joint churn would follow from the partition's.
    assert one_way([100, 8, 9], [30, 3, 4], MINIMUM) == {0, 1, 2}


def test_small_levels_that_hold_no_churner_between_them_hide_another():
    # 12 + 10 customers and 0 churners: the total says so, and a count is never negative.
    assert _line_leaks([100, 12, 10], [30, 0, 0], {1, 2}) == {(1,), (2,)}
    assert one_way([100, 12, 10], [30, 0, 0], MINIMUM) == {0, 1, 2}


def test_an_empty_level_is_never_hidden_or_used_to_hide_another():
    assert one_way([100, 0, 50, 5], [30, 0, 10, 1], MINIMUM) == {2, 3}


def test_a_line_of_large_levels_hides_nothing():
    assert one_way([100, 50, 20], [30, 10, 0], MINIMUM) == frozenset()


# --- two-way ----------------------------------------------------------------------------------


def test_a_lone_small_cell_is_hidden_with_the_cheapest_rectangle():
    customers = [[200, 90, 40], [80, 15, 60], [70, 50, 30]]
    churned = [[60, 30, 10], [20, 5, 15], [10, 10, 5]]
    assert _grid_leaks(customers, churned, {(1, 1)}) == {((1, 1),)}
    hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    # Row 1's next smallest is 60 (column 2); column 1's is 50 (row 2): 30 closes it.
    assert hidden == {(1, 1), (1, 2), (2, 1), (2, 2)}
    assert _grid_leaks(customers, churned, hidden) == set()


def test_small_cells_that_hold_no_churner_hide_the_large_cell_their_total_shares():
    # The real Contract x tenure shape: two long contracts are new and nobody left.
    customers = [[626, 300, 400], [9, 9, 200], [11, 11, 300]]
    churned = [[360, 150, 120], [0, 1, 30], [0, 0, 10]]
    small = {(1, 0), (1, 1), (2, 0), (2, 1)}
    assert ((1, 0),) in _grid_leaks(customers, churned, small)
    hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    # Month-to-month's 0-3 and 4-6 open the zero columns; the one-year row's 9 + 9
    # customers need a third cell, and that one a cycle of its own.
    assert hidden == {(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)}
    assert _grid_leaks(customers, churned, hidden) == set()


def test_a_rectangle_of_small_cells_that_stays_open_is_left_alone():
    customers = [[300, 200, 100], [12, 10, 100], [10, 12, 100]]
    churned = [[90, 50, 20], [3, 4, 30], [5, 2, 25]]
    small = {(1, 0), (1, 1), (2, 0), (2, 1)}
    assert _grid_leaks(customers, churned, small) == set()
    assert two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM) == small


def test_an_empty_cell_is_never_hidden_or_part_of_a_rectangle():
    customers = [[200, 0, 40], [80, 15, 60], [70, 50, 30]]
    churned = [[60, 0, 10], [20, 5, 15], [10, 10, 5]]
    hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert (0, 1) not in hidden
    assert _grid_leaks(customers, churned, hidden) == set()


def test_a_hidden_margin_keeps_its_line_from_adding_up():
    # Row 1 has 18 customers, so its own one-way breakdown hides it (and row 2 with it).
    customers = [[200, 90, 40], [6, 7, 5], [70, 50, 30]]
    churned = [[60, 30, 10], [2, 3, 1], [10, 10, 5]]
    rows = one_way(np.sum(customers, axis=1).tolist(), np.sum(churned, axis=1).tolist(), MINIMUM)
    assert rows == {1, 2}
    hidden = two_way(customers, churned, hidden_rows=rows, hidden_columns=(), minimum=MINIMUM)
    assert {(1, 0), (1, 1), (1, 2)} <= hidden
    assert _grid_leaks(customers, churned, hidden, rows=rows) == set()


def test_a_table_without_small_cells_hides_nothing():
    customers = [[200, 90], [80, 60]]
    churned = [[0, 30], [20, 60]]
    assert two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM) == set()


def _random_table(rng):
    rows, cols = rng.integers(2, 5, size=2)
    kind = rng.choice(3, size=(rows, cols), p=[0.1, 0.35, 0.55])
    small = rng.integers(1, MINIMUM, size=(rows, cols))
    large = rng.integers(MINIMUM, 90, size=(rows, cols))
    customers = np.where(kind == 0, 0, np.where(kind == 1, small, large))
    rate = rng.choice([0.0, 0.1, 0.3, 0.6, 1.0], size=(rows, 1), p=[0.15, 0.2, 0.3, 0.2, 0.15])
    churned = rng.binomial(customers, np.broadcast_to(rate, customers.shape))
    return customers, churned


@pytest.mark.parametrize("seed", range(40))
def test_no_small_group_can_be_worked_out_unless_its_published_totals_fix_it(seed):
    rng = np.random.default_rng(seed)
    customers, churned = _random_table(rng)
    rows = one_way(customers.sum(1).tolist(), churned.sum(1).tolist(), MINIMUM)
    cols = one_way(customers.sum(0).tolist(), churned.sum(0).tolist(), MINIMUM)
    hidden = two_way(customers, churned, hidden_rows=rows, hidden_columns=cols, minimum=MINIMUM)
    small = {tuple(int(v) for v in rc) for rc in np.argwhere((customers > 0) & (customers < 20))}
    assert small <= hidden and all(customers[rc] > 0 for rc in hidden)
    leaks = _grid_leaks(customers, churned, hidden, rows, cols)
    # Hiding every cell is the most any table can do; what still shows then is the totals'.
    everything = {tuple(int(v) for v in rc) for rc in np.argwhere(customers > 0)}
    assert leaks <= _grid_leaks(customers, churned, everything, rows, cols)


def test_a_level_of_exactly_the_minimum_is_reported_and_hides_nothing():
    assert one_way([300, 20, 60], [90, 5, 20], MINIMUM) == frozenset()
    customers = [[200, 20, 40], [80, 60, 60], [70, 50, 30]]
    churned = [[60, 5, 10], [20, 15, 15], [10, 10, 5]]
    assert two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM) == set()


def test_two_small_levels_that_together_reach_the_minimum_hide_nothing_more():
    # 10 + 10 customers: their joint churn follows from the partition's, as a group of 20.
    assert one_way([300, 10, 10, 40], [90, 3, 4, 8], MINIMUM) == {1, 2}


def test_hidden_margins_are_hidden_around_and_add_no_cells_of_their_own():
    customers = [[25, 25, 1000], [6, 7, 5], [100, 100, 100]]
    churned = [[8, 9, 300], [2, 3, 1], [30, 31, 29]]
    rows = one_way(np.sum(customers, axis=1).tolist(), np.sum(churned, axis=1).tolist(), MINIMUM)
    assert rows == {1, 2}
    hidden = two_way(customers, churned, hidden_rows=rows, hidden_columns=(), minimum=MINIMUM)
    assert hidden == {(r, k) for r in (1, 2) for k in range(3)}
    assert _grid_leaks(customers, churned, hidden, rows=rows) == set()


def test_hiding_prefers_the_option_that_frees_a_group_to_a_cheaper_one_that_does_not():
    customers = [[35, 37, 9, 73], [3, 2, 8, 60], [18, 58, 0, 19], [18, 50, 4, 29]]
    churned = [[2, 3, 1, 8], [2, 1, 6, 36], [4, 14, 0, 4], [0, 3, 1, 4]]
    hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert hidden == {
        (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 3), (3, 0), (3, 1), (3, 2)
    }  # fmt: skip
    assert _grid_leaks(customers, churned, hidden) == set()


# --- what no hiding can protect ---------------------------------------------------------------


def test_a_line_whose_total_already_says_nobody_churned_hides_only_its_small_cell(caplog):
    # Row 0 holds no churner: its total gives each of its cells away, whatever is hidden.
    customers = [[10, 40, 40, 40], [50, 60, 70, 80], [55, 65, 75, 85], [45, 35, 25, 90]]
    churned = [[0, 0, 0, 0], [10, 20, 30, 40], [11, 22, 33, 44], [9, 7, 5, 30]]
    with caplog.at_level("WARNING", logger="churnsense.suppression"):
        hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert hidden == {(0, 0)}
    assert [r.getMessage() for r in caplog.records] == [
        "1 small group(s) have a churn count the published totals fix by themselves"
    ]


def test_a_partition_with_no_churner_hides_only_its_small_levels():
    assert one_way([100, 10, 50], [0, 0, 0], MINIMUM) == {1}
    assert one_way([100, 10, 50], [100, 10, 50], MINIMUM) == {1}


def test_a_wide_line_of_tiny_cells_in_a_silent_row_is_quick_and_quiet(caplog):
    customers = [[1, 1, 1, 1, 1, 15]] + [[30 + 7 * r + k for k in range(6)] for r in range(4)]
    churned = [[0] * 6] + [[(30 + 7 * r + k) // 3 for k in range(6)] for r in range(4)]
    with caplog.at_level("WARNING", logger="churnsense.suppression"):
        hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert hidden == {(0, k) for k in range(6)}
    assert len(caplog.records) == 1


def test_a_solver_failure_hides_every_count_the_table_may_hide(monkeypatch, caplog):
    from churnsense import suppression

    class Failed:
        status, message, fun = 4, "numerical trouble", 0.0

    def failing(*_args, **_kwargs):
        return Failed()

    monkeypatch.setattr(suppression, "linprog", failing)
    customers = [[200, 90, 40], [80, 15, 60], [70, 0, 30]]
    churned = [[60, 30, 10], [20, 5, 15], [10, 0, 5]]
    with caplog.at_level("ERROR", logger="churnsense.suppression"):
        hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert hidden == {(r, k) for r in range(3) for k in range(3) if customers[r][k]}
    assert one_way([100, 10, 50], [30, 2, 9], MINIMUM) == {0, 1, 2}
    assert caplog.records


def test_a_pair_its_published_total_gives_away_costs_no_further_cell(caplog):
    # 5 + 6 customers fill row 0, whose total is published: their sum is out, whatever is
    # hidden. Each cell alone still needs a cycle, and nothing more is hidden for the pair.
    customers = [[5, 6], [40, 50], [60, 70]]
    churned = [[2, 3], [10, 20], [30, 35]]
    with caplog.at_level("WARNING", logger="churnsense.suppression"):
        hidden = two_way(customers, churned, hidden_rows=(), hidden_columns=(), minimum=MINIMUM)
    assert hidden == {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert len(caplog.records) == 1
