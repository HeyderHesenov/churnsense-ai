"""Tests for the descriptive statistics shared by the report and the dashboard."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense import analysis as an
from churnsense.data import schema


@pytest.fixture
def toy() -> pd.DataFrame:
    """A frame with hand-computable answers, so the assertions are not circular."""
    return pd.DataFrame(
        {
            "Contract": ["A", "A", "A", "A", "B", "B"],
            "tenure": [1, 2, 3, 70, 71, 72],
            "MonthlyCharges": [20.0, 30.0, 80.0, 100.0, 40.0, 60.0],
            schema.TARGET: [1, 1, 1, 0, 0, 0],
        }
    )


def test_churn_rate_by_computes_rate_and_denominator(toy: pd.DataFrame):
    out = an.churn_rate_by(toy, "Contract").set_index("Contract")
    assert out.loc["A", "customers"] == 4
    assert out.loc["A", "churned"] == 3
    assert out.loc["A", "churn_rate"] == pytest.approx(0.75)
    assert out.loc["B", "churn_rate"] == pytest.approx(0.0)


def test_churn_rate_by_can_sort_by_rate(toy: pd.DataFrame):
    out = an.churn_rate_by(toy, "Contract", sort_by_rate=True)
    assert list(out["churn_rate"]) == sorted(out["churn_rate"], reverse=True)


def test_churn_rate_by_rejects_an_unknown_column(toy: pd.DataFrame):
    with pytest.raises(KeyError):
        an.churn_rate_by(toy, "NoSuchColumn")


def test_tenure_buckets_are_ordered_and_cover_the_full_range():
    buckets = an.tenure_bucket(pd.Series([0, 3, 4, 12, 24, 48, 72]))
    assert buckets.isna().sum() == 0
    assert buckets.cat.ordered
    assert list(buckets.cat.categories) == list(an.TENURE_LABELS)


def test_charge_bands_are_ordered_and_cover_the_full_range():
    bands = an.charge_band(pd.Series([0.0, 18.25, 35.0, 94.99, 95.0, 118.75]))
    assert bands.isna().sum() == 0
    assert list(bands.cat.categories) == list(an.CHARGE_LABELS)


def test_cramers_v_is_one_for_a_perfect_association():
    x = pd.Series(["a", "a", "b", "b"] * 25)
    y = pd.Series([0, 0, 1, 1] * 25)
    assert an.cramers_v(x, y) == pytest.approx(1.0, abs=0.02)


def test_cramers_v_is_near_zero_for_independence():
    rng = np.random.default_rng(0)
    n = 4000
    x = pd.Series(rng.choice(["a", "b", "c"], n))
    y = pd.Series(rng.integers(0, 2, n))
    assert an.cramers_v(x, y) < 0.05


def test_cramers_v_handles_a_constant_column():
    """Bias correction can drive a degenerate table negative under the sqrt."""
    assert an.cramers_v(pd.Series(["a"] * 10), pd.Series([0, 1] * 5)) == 0.0


def test_point_biserial_sign_follows_the_relationship(toy: pd.DataFrame):
    r = an.point_biserial(toy["tenure"], toy[schema.TARGET])
    assert r < 0, "longer tenure should associate with less churn in this toy frame"


def test_point_biserial_is_zero_for_a_constant_input():
    assert an.point_biserial(pd.Series([5.0] * 10), pd.Series([0, 1] * 5)) == 0.0


def test_association_ranking_is_sorted_and_bounded(clean_frame: pd.DataFrame):
    ranking = an.association_ranking(clean_frame.drop(columns=[schema.ID_COLUMN]))
    assert list(ranking["strength"]) == sorted(ranking["strength"], reverse=True)
    assert ranking["strength"].between(0, 1).all()
    assert schema.TARGET not in set(ranking["feature"])


def test_risk_concentration_suppresses_small_cells(clean_frame: pd.DataFrame):
    rate, counts = an.risk_concentration(clean_frame, min_cell=1_000_000)
    assert rate.isna().all().all(), "every cell should be suppressed at an absurd threshold"
    assert counts.to_numpy().sum() == len(clean_frame)


def test_risk_concentration_matrices_are_aligned(clean_frame: pd.DataFrame):
    rate, counts = an.risk_concentration(clean_frame)
    assert list(rate.index) == list(counts.index)
    assert list(rate.columns) == list(counts.columns)


def test_service_addon_rates_only_consider_internet_customers(clean_frame: pd.DataFrame):
    out = an.service_addon_rates(clean_frame)
    subscribers = int(clean_frame["InternetService"].ne("No").sum())
    assert (out["n_with"] + out["n_without"] <= subscribers).all()
    assert out["difference"].equals(out["with_addon"] - out["without_addon"])
