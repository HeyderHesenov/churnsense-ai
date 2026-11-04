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


def test_a_breakdown_never_reports_a_small_cell(book):
    split = insights.breakdown(book, "PaymentMethod", "TechSupport")
    for cell in [*split.cells, *split.row_totals, *split.column_totals]:
        if cell.customers < insights.MIN_CELL:
            assert cell.reason == "too_few"
            assert cell.rate is None and cell.churned is None and cell.lift is None
            assert cell.low is None and cell.high is None
        elif cell.rate is None:
            assert cell.reason == "complementary"
            assert cell.churned is None and cell.lift is None and cell.low is None
        else:
            assert cell.reason is None


#: Every pair of breakdown dimensions, each once, in their canonical order.
_PAIRS = list(itertools.combinations(insights.DIMENSIONS, 2))


def _hidden(cells) -> set[tuple[str, str | None]]:
    return {(c.row, c.column) for c in cells if c.rate is None}


def test_a_breakdown_reports_each_cell_with_its_wilson_interval(book):
    split = insights.breakdown(book, "Contract", "PaymentMethod")
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
    hides_more = [
        pair
        for pair in _PAIRS
        if any(c.reason == "complementary" for c in insights.breakdown(book, *pair).cells)
    ]
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


def test_a_group_of_exactly_min_cell_customers_is_reported_and_one_fewer_is_not():
    cell = insights._cell("a", None, insights.MIN_CELL, 5, False, 0.25)
    assert cell.reason is None and cell.rate == 0.25 and cell.lift == 1.0
    assert insights._cell("a", None, insights.MIN_CELL - 1, 5, False, 0.25).reason == "too_few"
    assert insights._cell("a", None, 40, 5, True, 0.25).reason == "complementary"


def test_no_lift_is_given_against_a_partition_where_nobody_churned():
    cell = insights._cell("a", None, 30, 0, False, 0.0)
    assert cell.rate == 0.0 and cell.lift is None and cell.low == 0.0


def _spy_two_way(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = insights.suppression.two_way

    def spy(customers, churned, **kwargs):
        calls.append({"shape": np.shape(customers), **kwargs})
        return real(customers, churned, **kwargs)

    monkeypatch.setattr(insights.suppression, "two_way", spy)
    return calls


def test_a_grid_is_protected_in_dimension_order_whichever_way_it_is_asked_for(book, monkeypatch):
    calls = _spy_two_way(monkeypatch)
    insights.breakdown(book, "tenure", "Contract")
    insights.breakdown(book, "Contract", "tenure")
    assert [c["shape"] for c in calls] == [(3, 6), (3, 6)]


def test_a_grid_is_protected_around_the_margins_its_one_way_breakdowns_hide(book, monkeypatch):
    real = insights.suppression.one_way

    def hide_the_first_level(customers, churned, minimum):
        return real(customers, churned, minimum) | ({0} if len(customers) == 3 else set())

    monkeypatch.setattr(insights.suppression, "one_way", hide_the_first_level)
    calls = _spy_two_way(monkeypatch)
    split = insights.breakdown(book, "Contract", "tenure")
    assert calls[0]["hidden_rows"] == {0}
    assert split.row_totals[0].rate is None
