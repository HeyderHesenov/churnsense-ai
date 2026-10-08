"""Tests for classification metrics and calibration assessment.

Expected values are computed by hand from small, explicit arrays rather than
from another library call, so a test failure means the metric is wrong rather
than that two implementations disagree.
"""

from __future__ import annotations

import numpy as np
import pytest

from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
    reliability_table,
)

# 10 rows, 4 positives. At threshold 0.5 the predictions are:
#   TP = 3  (0.9, 0.8, 0.6 with y=1)
#   FP = 2  (0.7, 0.55 with y=0)
#   FN = 1  (0.4 with y=1)
#   TN = 4  (0.3, 0.2, 0.1, 0.05 with y=0)
Y_TRUE = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
Y_PROBA = np.array([0.9, 0.8, 0.6, 0.4, 0.7, 0.55, 0.3, 0.2, 0.1, 0.05])


def test_confusion_counts_are_exact():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=0.5)
    assert (m.tp, m.fp, m.fn, m.tn) == (3, 2, 1, 4)


def test_precision_recall_f1_match_hand_computation():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=0.5)
    assert m.precision == pytest.approx(3 / 5)
    assert m.recall == pytest.approx(3 / 4)
    assert m.f1 == pytest.approx(2 * (0.6 * 0.75) / (0.6 + 0.75))


def test_accuracy_is_reported_but_not_the_headline():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=0.5)
    assert m.accuracy == pytest.approx(7 / 10)


def test_perfect_separation_scores_one():
    y = np.array([0, 0, 1, 1])
    m = evaluate(y, np.array([0.1, 0.2, 0.8, 0.9]), threshold=0.5)
    assert m.roc_auc == pytest.approx(1.0)
    assert m.average_precision == pytest.approx(1.0)
    assert m.f1 == pytest.approx(1.0)


def test_average_precision_of_a_constant_score_is_the_base_rate():
    """A model with no discrimination earns exactly the prevalence, not 0.5."""
    y = np.array([1] * 3 + [0] * 7)
    m = evaluate(y, np.full(10, 0.42), threshold=0.5)
    assert m.average_precision == pytest.approx(0.3, abs=0.01)


def test_threshold_of_zero_flags_everyone():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=0.0)
    assert m.recall == pytest.approx(1.0)
    assert m.tp + m.fp == len(Y_TRUE)
    assert m.fn == 0


def test_threshold_above_every_score_flags_nobody():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=1.0)
    assert (m.tp, m.fp) == (0, 0)
    assert m.recall == pytest.approx(0.0)
    assert m.precision == pytest.approx(0.0), "precision is defined as 0 when nothing is flagged"


def test_brier_score_is_mean_squared_error_of_the_probability():
    y = np.array([1, 0])
    m = evaluate(y, np.array([0.75, 0.25]), threshold=0.5)
    assert m.brier == pytest.approx((0.25**2 + 0.25**2) / 2)


def test_metrics_round_trip_through_a_dict():
    m = evaluate(Y_TRUE, Y_PROBA, threshold=0.5)
    payload = m.as_dict()
    assert payload["precision"] == pytest.approx(m.precision)
    assert payload["threshold"] == pytest.approx(0.5)
    assert payload["n"] == 10
    assert payload["positives"] == 4
    assert all(isinstance(v, (int, float)) for v in payload.values())


def test_evaluate_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="same length"):
        evaluate(np.array([0, 1]), np.array([0.5]))


def test_evaluate_rejects_probabilities_outside_the_unit_interval():
    with pytest.raises(ValueError, match="probabilit"):
        evaluate(np.array([0, 1]), np.array([0.5, 1.4]))


def test_evaluate_rejects_a_single_class_target():
    """ROC-AUC is undefined with one class; failing loudly beats reporting NaN."""
    with pytest.raises(ValueError, match="both classes"):
        evaluate(np.array([0, 0, 0]), np.array([0.1, 0.2, 0.3]))


def test_metrics_is_immutable():
    m = evaluate(Y_TRUE, Y_PROBA)
    with pytest.raises((AttributeError, TypeError)):
        m.precision = 1.0  # type: ignore[misc]


def test_classification_metrics_is_constructible_for_reporting():
    """model_meta.json stores metrics as plain numbers and reads them back."""
    m = evaluate(Y_TRUE, Y_PROBA)
    assert ClassificationMetrics(**m.as_dict()) == m


# --- calibration ------------------------------------------------------------


def test_reliability_table_bins_predictions_with_their_observed_rate():
    y = np.array([0] * 50 + [1] * 50)
    proba = np.concatenate([np.full(50, 0.1), np.full(50, 0.9)])
    table = reliability_table(y, proba, n_bins=10)

    assert set(table.columns) >= {"mean_predicted", "observed_rate", "count"}
    assert table["count"].sum() == 100
    low = table.loc[table["mean_predicted"].idxmin()]
    high = table.loc[table["mean_predicted"].idxmax()]
    assert low["observed_rate"] == pytest.approx(0.0)
    assert high["observed_rate"] == pytest.approx(1.0)


def test_reliability_table_drops_empty_bins():
    y = np.array([0, 1] * 20)
    proba = np.full(40, 0.5)
    table = reliability_table(y, proba, n_bins=10)
    assert len(table) == 1, "only the bin containing 0.5 should appear"


def test_expected_calibration_error_is_zero_for_a_perfectly_calibrated_model():
    rng = np.random.default_rng(0)
    proba = rng.uniform(0, 1, 20_000)
    y = (rng.uniform(0, 1, 20_000) < proba).astype(int)
    assert expected_calibration_error(y, proba, n_bins=10) < 0.02


def test_expected_calibration_error_is_large_for_a_badly_calibrated_model():
    """Confident-and-wrong must score far worse than confident-and-right.

    Hand-computed: both bins have |observed - predicted| = 0.9 and weight 0.5,
    so ECE = 0.9 - just short of the 1.0 a maximally wrong model would score.
    """
    y = np.array([0] * 500 + [1] * 500)
    proba = np.concatenate([np.full(500, 0.9), np.full(500, 0.1)])
    assert expected_calibration_error(y, proba, n_bins=10) == pytest.approx(0.9, abs=0.01)
