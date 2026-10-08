"""Tests for threshold economics.

The arithmetic here drives money figures in the dashboard, so the expected
values are hand-computed from tiny inputs. Every monetary result is a
*simulation* under stated assumptions - these tests pin the arithmetic, not
any claim about real savings.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense.config import Business, Config
from churnsense.evaluation.threshold import (
    ThresholdScenario,
    assign_risk_bands,
    recommend_threshold,
    sweep_thresholds,
)

Y = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
P = np.array([0.9, 0.8, 0.6, 0.4, 0.7, 0.55, 0.3, 0.2, 0.1, 0.05])
CHARGES = np.full(10, 100.0)

# cost 50, success 0.5, horizon 10 months, margin 0.6
#   value of one retained customer = 100 * 10 * 0.6 = 600
BUSINESS = Business(
    currency="USD",
    retention_offer_cost=50.0,
    offer_success_rate=0.5,
    expected_horizon_months=10,
    gross_margin=0.6,
)


def test_scenario_arithmetic_is_hand_checkable():
    """At 0.5: TP=3, FP=2 -> 5 offers at 50 = 250 spend; 3*0.5*600 = 900 saved."""
    row = sweep_thresholds(Y, P, CHARGES, BUSINESS, thresholds=[0.5]).iloc[0]
    assert row["flagged"] == 5
    assert row["true_positives"] == 3
    assert row["intervention_cost"] == pytest.approx(250.0)
    assert row["retained_value"] == pytest.approx(900.0)
    assert row["net_benefit"] == pytest.approx(650.0)


def test_value_uses_each_customer_s_own_charges_not_an_average():
    """A simulator that averages charges would misprice a skewed book."""
    charges = np.array([10.0] * 9 + [1000.0])
    y = np.array([0] * 9 + [1])
    proba = np.array([0.1] * 9 + [0.9])
    row = sweep_thresholds(y, proba, charges, BUSINESS, thresholds=[0.5]).iloc[0]
    assert row["retained_value"] == pytest.approx(1000.0 * 10 * 0.6 * 0.5)


def test_flagging_nobody_costs_and_saves_nothing():
    row = sweep_thresholds(Y, P, CHARGES, BUSINESS, thresholds=[1.0]).iloc[0]
    assert row["flagged"] == 0
    assert row["intervention_cost"] == 0.0
    assert row["retained_value"] == 0.0
    assert row["net_benefit"] == 0.0


def test_flagging_everybody_costs_the_full_campaign():
    row = sweep_thresholds(Y, P, CHARGES, BUSINESS, thresholds=[0.0]).iloc[0]
    assert row["flagged"] == len(Y)
    assert row["recall"] == pytest.approx(1.0)
    assert row["intervention_cost"] == pytest.approx(len(Y) * 50.0)


def test_recall_is_monotonically_non_increasing_in_the_threshold():
    sweep = sweep_thresholds(Y, P, CHARGES, BUSINESS)
    assert sweep["recall"].is_monotonic_decreasing


def test_flagged_count_is_monotonically_non_increasing():
    sweep = sweep_thresholds(Y, P, CHARGES, BUSINESS)
    assert sweep["flagged"].is_monotonic_decreasing


def test_sweep_covers_the_full_unit_interval_by_default():
    sweep = sweep_thresholds(Y, P, CHARGES, BUSINESS)
    assert sweep["threshold"].min() == pytest.approx(0.0)
    assert sweep["threshold"].max() == pytest.approx(1.0)
    assert sweep["threshold"].is_monotonic_increasing


def test_sweep_rejects_mismatched_charge_lengths():
    with pytest.raises(ValueError, match="same length"):
        sweep_thresholds(Y, P, np.array([100.0]), BUSINESS)


def test_sweep_rejects_negative_charges():
    with pytest.raises(ValueError, match="negative"):
        sweep_thresholds(Y, P, np.full(10, -1.0), BUSINESS)


def test_recommendation_maximises_net_benefit():
    scenario = recommend_threshold(Y, P, CHARGES, BUSINESS)
    sweep = sweep_thresholds(Y, P, CHARGES, BUSINESS)
    assert isinstance(scenario, ThresholdScenario)
    assert scenario.net_benefit == pytest.approx(sweep["net_benefit"].max())


def test_recommendation_is_reported_as_a_simulation():
    """Guards the honesty requirement: the label must travel with the number."""
    scenario = recommend_threshold(Y, P, CHARGES, BUSINESS)
    assert "simulat" in scenario.disclaimer.lower()
    assert "assumption" in scenario.disclaimer.lower()


def test_an_expensive_offer_that_never_works_recommends_flagging_nobody():
    """A sanity check on the economics: if nothing works, do nothing."""
    hopeless = Business(
        currency="USD",
        retention_offer_cost=10_000.0,
        offer_success_rate=0.001,
        expected_horizon_months=1,
        gross_margin=0.1,
    )
    scenario = recommend_threshold(Y, P, CHARGES, hopeless)
    assert scenario.flagged == 0
    assert scenario.net_benefit == pytest.approx(0.0)


def test_a_free_offer_that_always_works_catches_every_churner():
    """With a costless, perfect offer, no churner may be left unflagged.

    It must *not* flag everybody, though: an offer to someone who was never
    going to leave adds no value even at zero cost, and the tie-break on equal
    net benefit prefers the operating point that disturbs fewer customers.
    """
    generous = Business(
        currency="USD",
        retention_offer_cost=0.0,
        offer_success_rate=1.0,
        expected_horizon_months=24,
        gross_margin=1.0,
    )
    scenario = recommend_threshold(Y, P, CHARGES, generous)
    assert scenario.recall == pytest.approx(1.0)
    assert scenario.false_negatives == 0
    assert scenario.flagged < len(Y), "flagging non-churners buys nothing, even for free"


def test_scenario_serialises_to_plain_numbers():
    payload = recommend_threshold(Y, P, CHARGES, BUSINESS).as_dict()
    assert payload["threshold"] == pytest.approx(payload["threshold"])
    assert isinstance(payload["flagged"], int)


# --- risk bands -------------------------------------------------------------


def test_risk_bands_label_every_customer(cfg: Config):
    bands = assign_risk_bands(np.linspace(0, 1, 101), cfg)
    assert bands.notna().all()
    assert set(bands.unique()) <= {b.name for b in cfg.risk_bands}


def test_risk_bands_are_ordered_for_display(cfg: Config):
    bands = assign_risk_bands(np.array([0.05, 0.95]), cfg)
    assert isinstance(bands.dtype, pd.CategoricalDtype)
    assert bands.cat.ordered
    assert list(bands.cat.categories) == [b.name for b in cfg.risk_bands]


def test_risk_bands_are_independent_of_the_decision_threshold(cfg: Config):
    """Bands are an operational segmentation; retuning the threshold must not
    silently reshuffle which customers the team calls 'Critical'."""
    first = assign_risk_bands(np.array([0.1, 0.4, 0.6, 0.9]), cfg)
    assert list(first) == ["Low", "Moderate", "High", "Critical"]


def _near_tie_case():
    """A population where the top thresholds differ by less than one offer.

    Found by search over seeds, then pinned: with this data the plain argmax
    lands on 0.27 ($10,108.55) while 0.28 scores $10,100.71 - a $7.84 gap
    against a $50 offer - and flags 6 fewer customers at higher precision.
    """
    rng = np.random.default_rng(0)
    n = 200
    y = rng.binomial(1, 0.3, n)
    proba = np.clip(rng.beta(2, 5, n) + y * rng.uniform(0.1, 0.4, n), 0, 1)
    charges = rng.uniform(40, 120, n)
    return y, proba, charges


def test_a_difference_smaller_than_one_offer_is_not_a_real_difference():
    """Net benefit is a simulation; it cannot resolve sub-offer differences.

    The same problem as picking a model on a 0.0013 PR-AUC gap. Committing the
    campaign to extra customers for a difference smaller than the cost of one
    intervention is reading precision the estimate does not have.
    """
    y, proba, charges = _near_tie_case()
    sweep = sweep_thresholds(y, proba, charges, BUSINESS)
    best = sweep["net_benefit"].max()
    band = sweep[sweep["net_benefit"] >= best - BUSINESS.retention_offer_cost]
    assert len(band) > 1, "fixture must actually contain a near-tie"

    scenario = recommend_threshold(y, proba, charges, BUSINESS)
    assert scenario.threshold == pytest.approx(band["threshold"].max()), (
        "within the indifference band, the fewest-customers option must win"
    )


def test_the_near_tie_choice_flags_fewer_customers():
    """The point of the band: fewer people contacted, for the same simulated value."""
    y, proba, charges = _near_tie_case()
    sweep = sweep_thresholds(y, proba, charges, BUSINESS)
    argmax_row = sweep.loc[sweep["net_benefit"].idxmax()]
    scenario = recommend_threshold(y, proba, charges, BUSINESS)
    assert scenario.flagged < argmax_row["flagged"]
    assert scenario.precision > argmax_row["precision"]


def test_the_indifference_band_scales_with_the_offer_cost():
    """A costlier offer makes more operating points indistinguishable."""
    cheap = Business("USD", 1.0, 0.5, 10, 0.6)
    pricey = Business("USD", 500.0, 0.5, 10, 0.6)
    assert (
        recommend_threshold(Y, P, CHARGES, pricey).threshold
        >= recommend_threshold(Y, P, CHARGES, cheap).threshold
    )


def test_out_of_range_probabilities_are_not_labelled_critical(cfg: Config):
    """A scalar band helper once labelled NaN and p > 1 as "Critical".

    It had no production callers, so the bug was latent — but "Critical" is
    the highest-priority retention queue, and a NaN landing there is the worst
    possible default. `assign_risk_bands` returns NaN for anything outside
    the configured range, which is the honest answer.
    """
    bands = assign_risk_bands(np.array([-0.5, 1.5, np.nan]), cfg)
    assert bands.isna().all(), f"expected NaN outside [0, 1], got {list(bands)}"


def test_a_single_probability_can_be_banded(cfg: Config):
    """The scalar case goes through the same function as the vector case."""
    assert assign_risk_bands(np.array([0.9]), cfg).iloc[0] == "Critical"
    assert assign_risk_bands(np.array([0.01]), cfg).iloc[0] == "Low"
