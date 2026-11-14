"""The aggregates the dashboard pages show, on the synthetic book."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from churnsense import analysis as an
from churnsense import insights
from churnsense.book import labelled
from churnsense.evaluation.metrics import wilson_interval


def test_histogram_bins_are_closed_on_the_left_and_keep_one_in_the_last_bin():
    bins = insights.histogram_bins(np.array([0.0, 0.0499, 0.05, 0.36, 0.9999, 1.0]))
    assert bins.tolist() == [0, 0, 1, 7, 19, 19]


def test_the_cross_tab_agrees_with_the_risk_concentration_matrix(book):
    train = labelled(book.frame, "train", shown=book.outcomes)
    rate, counts = an.risk_concentration(train, index="Contract", columns="tenure_bucket")
    tidy = an.cross_tab(train, "Contract", "tenure_bucket")
    for row in tidy.itertuples():
        assert counts.loc[row.Contract, row.tenure_bucket] == row.customers
        expected = rate.loc[row.Contract, row.tenure_bucket]
        if pd.isna(expected):
            assert pd.isna(row.churn_rate) and pd.isna(row.churned)
        else:
            assert row.churn_rate == pytest.approx(expected)


@pytest.mark.parametrize(
    ("rows", "columns"),
    [("PaymentMethod", "TechSupport"), ("Contract", "tenure"), ("Contract", None)],
)
def test_a_breakdown_never_reports_a_small_group(public_book, rows, columns):
    """Shown: at least ``MIN_CELL`` churners and ``MIN_CELL`` stayers. Hidden: nothing of it."""
    split = insights.breakdown(public_book, rows, columns)
    train = labelled(public_book.frame, "train", shown=public_book.outcomes)
    for cell in [*split.cells, *split.row_totals]:
        if cell.customers < insights.MIN_CELL:
            assert cell.reason == "too_few"
        if cell.rate is None:
            assert cell.reason in {"too_few", "cross_partition", "withheld"}
            assert cell.churned is None and cell.lift is None
            assert cell.low is None and cell.high is None
            continue
        assert cell.reason is None
        assert cell.churned >= insights.MIN_CELL
        assert cell.customers - cell.churned >= insights.MIN_CELL
        # And the count is the group's own.
        mask = train[insights.DIMENSIONS[rows].column].astype(str) == cell.row
        if cell.column is not None:
            mask &= train[insights.DIMENSIONS[columns].column].astype(str) == cell.column
        assert (cell.customers, cell.churned) == (int(mask.sum()), int(train[mask]["Churn"].sum()))


def test_the_rule_still_leaves_groups_to_show_on_a_large_enough_book(public_book):
    shown = [
        c
        for key in insights.DIMENSIONS
        for c in insights.breakdown(public_book, key).row_totals
        if c.rate is not None
    ]
    assert len(shown) >= 5


def test_a_group_of_few_churners_or_few_stayers_is_hidden_whatever_its_size():
    assert insights._reason(400, 19, hidden=False, crossed=False) == "withheld"
    assert insights._reason(400, 381, hidden=False, crossed=False) == "withheld"
    assert insights._reason(30, 0, hidden=False, crossed=False) == "withheld"
    assert insights._reason(40, 20, hidden=False, crossed=False) is None
    assert insights._reason(19, 0, hidden=False, crossed=False) == "too_few"
    assert insights._reason(400, 200, hidden=True, crossed=False) == "withheld"
    assert insights._reason(400, None, hidden=False, crossed=False) == "withheld"
    assert insights._reason(400, 200, hidden=True, crossed=True) == "cross_partition"


def test_two_year_contracts_never_show_their_training_churn(book):
    """With the test report's churners per Contract level and the dataset's public totals,
    the training count would give validation's by subtraction (``CROSS_PARTITION``)."""
    for rows, columns in [
        ("Contract", None),
        *[("Contract", k) for k in insights.DIMENSIONS if k != "Contract"],
    ]:
        for split in (
            insights.breakdown(book, rows, columns),
            *([insights.breakdown(book, columns, rows)] if columns else []),
        ):
            for cell in [*split.cells, *split.row_totals, *split.column_totals, *split.ranked]:
                if "Two year" in (cell.row, cell.column) and cell.customers:
                    assert cell.rate is None
    one_way = insights.breakdown(book, "Contract")
    two_year = next(c for c in one_way.row_totals if c.row == "Two year")
    assert two_year.reason in {"cross_partition", "too_few"}
    # Nor can it come back as the partition's total less the others.
    assert sum(c.rate is None for c in one_way.row_totals if c.customers) >= 2


#: Every pair of breakdown dimensions, each once, in their canonical order.
_PAIRS = list(itertools.combinations(insights.DIMENSIONS, 2))


def _hidden(cells) -> set[tuple[str, str | None]]:
    return {(c.row, c.column) for c in cells if c.rate is None}


def test_a_breakdown_reports_each_cell_with_its_wilson_interval(public_book):
    """On the larger synthetic book: the small one shows almost no group of twenty of each."""
    split = insights.breakdown(public_book, "PaymentMethod", "PaperlessBilling")
    reported = [c for c in [*split.cells, *split.row_totals] if c.rate is not None]
    assert reported
    for cell in reported:
        assert (cell.low, cell.high) == pytest.approx(wilson_interval(cell.churned, cell.customers))
        assert 0 <= cell.low <= cell.rate <= cell.high <= 1
        assert cell.rate == cell.churned / cell.customers
        assert cell.lift == pytest.approx(cell.rate / split.base_rate)


@pytest.mark.parametrize(("rows", "columns"), _PAIRS)
def test_a_breakdown_hides_the_same_cells_whichever_way_round(book, rows, columns):
    across = insights.breakdown(book, rows, columns)
    down = insights.breakdown(book, columns, rows)
    assert _hidden(across.cells) == {(b, a) for a, b in _hidden(down.cells)}
    assert across.row_totals == down.column_totals
    assert across.column_totals == down.row_totals


@pytest.mark.parametrize(("rows", "columns"), _PAIRS)
def test_a_two_way_breakdown_keeps_its_one_way_margins_and_every_customer(book, rows, columns):
    split = insights.breakdown(book, rows, columns)
    assert split.row_totals == insights.breakdown(book, rows).row_totals
    assert split.column_totals == insights.breakdown(book, columns).row_totals
    assert sum(c.customers for c in split.cells) == split.partition_customers
    for margin in split.row_totals:
        cells = [c for c in split.cells if c.row == margin.row]
        assert sum(c.customers for c in cells) == margin.customers


@pytest.mark.parametrize(("rows", "columns"), _PAIRS)
def test_the_ranked_cells_are_the_highest_reported_rates(book, rows, columns):
    split = insights.breakdown(book, rows, columns)
    reported = sorted((c.rate for c in split.cells if c.rate is not None), reverse=True)
    assert [c.rate for c in split.ranked] == reported[:7]
    assert all(c.rate is not None for c in split.ranked)


def test_a_breakdown_hides_more_than_its_small_cells_when_subtraction_would_give_them_away(book):
    """Some hidden group keeps the rule itself: it is hidden for what it would give away."""
    train = labelled(book.frame, "train", shown=book.outcomes)
    counts = insights._training_counts(train)
    hides_more = []
    for rows, columns in _PAIRS:
        split = insights.breakdown(book, rows, columns)
        a, b = (
            (rows, columns) if insights._ORDER[rows] < insights._ORDER[columns] else (columns, rows)
        )
        customers, churned = counts.two_way[(a, b)]
        levels = (insights.DIMENSIONS[a].levels, insights.DIMENSIONS[b].levels)
        for cell in split.cells:
            i, j = (cell.row, cell.column) if a == rows else (cell.column, cell.row)
            at = (levels[0].index(i), levels[1].index(j))
            n, c = int(customers[at]), int(churned[at])
            if cell.reason == "withheld" and insights._keeps(c, n):
                hides_more.append((rows, columns, cell.row, cell.column))
    assert hides_more, "the synthetic book never needed a complementary cell"


def test_a_breakdown_of_an_unknown_dimension_raises(book):
    with pytest.raises(KeyError):
        insights.breakdown(book, "gender")


def test_listing_order_is_total_and_stable(book):
    frame = insights.with_outcomes(book)
    once = insights.ordered(frame, "tenure", descending=False)
    again = insights.ordered(frame.sample(frac=1, random_state=3), "tenure", descending=False)
    assert once.index.tolist() == again.index.tolist()


def test_a_listing_by_ids_keeps_exactly_those_customers(book):
    frame = insights.with_outcomes(book)
    chosen = tuple(frame["customerID"].iloc[[4, 9]])
    assert set(insights.select(frame, insights.CustomerFilter(ids=chosen))["customerID"]) == set(
        chosen
    )


def test_example_customers_are_deterministic_and_from_validation(book):
    first, second = insights.example_customers(book), insights.example_customers(book)
    assert first == second and first
    validation = set(book.frame.loc[book.frame["partition"] == "validation", "customerID"])
    assert set(first) <= validation


def test_the_summary_of_a_test_only_selection_shows_no_outcome(book):
    frame = insights.with_outcomes(book)
    test = insights.select(frame, insights.CustomerFilter(partition="test"))
    summary = insights.summarise(test, len(frame), book.outcomes)
    assert summary.customers == len(test) > 0
    assert summary.observed is None


def test_a_group_of_fewer_than_twenty_shows_no_observed_rate(book):
    frame = insights.with_outcomes(book)
    train = frame[frame["partition"] == "train"]
    small = insights.summarise(train.head(19), len(frame), book.outcomes)
    assert small.observed == insights.Observed(customers=19, churned=None, rate=None)
    enough = insights.summarise(train.head(20), len(frame), book.outcomes)
    assert enough.observed.customers == 20 and enough.observed.rate is not None


def test_a_group_of_exactly_min_cell_of_each_outcome_is_reported_and_one_fewer_is_not():
    m = insights.MIN_CELL
    assert insights._reason(2 * m, m, hidden=False, crossed=False) is None
    assert insights._reason(2 * m - 1, m, hidden=False, crossed=False) == "withheld"
    assert insights._reason(2 * m - 1, m - 1, hidden=False, crossed=False) == "withheld"
    cell = insights._cell("a", None, 2 * m, m, None, 0.25)
    assert cell.reason is None and cell.rate == 0.5 and cell.lift == 2.0
    hidden = insights._cell("a", None, 2 * m, m, "withheld", 0.25)
    assert hidden.reason == "withheld" and hidden.rate is None and hidden.churned is None


def test_no_lift_is_given_against_a_partition_where_nobody_churned():
    cell = insights._cell("a", None, 30, 0, None, 0.0)
    assert cell.rate == 0.0 and cell.lift is None and cell.low == 0.0


def _spy_two_way(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = insights.suppression.guard_two_way

    def spy(customers, churned, row_margins, column_margins, partition, **kwargs):
        calls.append(
            {"shape": np.shape(customers), "rows": row_margins, "columns": column_margins, **kwargs}
        )
        return real(customers, churned, row_margins, column_margins, partition, **kwargs)

    monkeypatch.setattr(insights.suppression, "guard_two_way", spy)
    return calls


def test_a_grid_is_protected_in_dimension_order_whichever_way_it_is_asked_for(book, monkeypatch):
    """Every pair is protected once, in ``DIMENSIONS`` order, and both orientations read it."""
    monkeypatch.setattr(insights, "_TABLES", {})
    calls = _spy_two_way(monkeypatch)
    turned = insights.breakdown(book, "tenure", "Contract")
    straight = insights.breakdown(book, "Contract", "tenure")
    shapes = [c["shape"] for c in calls]
    assert shapes.count((3, 6)) == 1
    assert len(calls) == len(list(itertools.combinations(insights.DIMENSIONS, 2)))
    assert {(c.column, c.row, c.reason) for c in turned.cells} == {
        (c.row, c.column, c.reason) for c in straight.cells
    }


def test_a_grid_is_protected_around_the_margins_its_one_way_breakdowns_hide(book, monkeypatch):
    real = insights.suppression.guard_one_way

    def hide_the_first_level(customers, churned, partition, **kwargs):
        return real(customers, churned, partition, **kwargs) | (
            {0} if len(customers) == 3 else set()
        )

    monkeypatch.setattr(insights.suppression, "guard_one_way", hide_the_first_level)
    monkeypatch.setattr(insights, "_TABLES", {})
    calls = _spy_two_way(monkeypatch)
    split = insights.breakdown(book, "Contract", "tenure")
    contract_tenure = next(c for c in calls if c["shape"] == (3, 6))
    assert contract_tenure["rows"][0] is None
    assert split.row_totals[0].rate is None
