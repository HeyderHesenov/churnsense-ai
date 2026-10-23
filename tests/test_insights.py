"""The aggregates the dashboard pages show, on the synthetic book."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense import analysis as an
from churnsense import insights
from churnsense.book import labelled


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
            assert cell.rate is None and cell.churned is None and cell.lift is None
        else:
            assert cell.rate is not None


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
