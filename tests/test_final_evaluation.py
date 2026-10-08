"""Tests for the final evaluation: the one time the test partition is scored.

The protocol under test:
  * the operating threshold is chosen on **validation**;
  * the test partition is scored with that already-fixed threshold;
  * nothing about the model or the threshold is chosen using test data.
"""

from __future__ import annotations

import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.evaluation.report import run_final_evaluation
from churnsense.evaluation.threshold import recommend_threshold
from churnsense.models.train import train_all


@pytest.fixture(scope="module")
def evaluated(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    return run_final_evaluation(outcome.model, splits, cfg), outcome, splits


def test_threshold_is_chosen_on_validation_not_on_test(evaluated, cfg: Config):
    """Recomputing the recommendation from validation alone must reproduce it."""
    evaluation, outcome, splits = evaluated
    expected = recommend_threshold(
        splits.y_val,
        outcome.model.predict_proba(splits.X_val)[:, 1],
        splits.X_val["MonthlyCharges"],
        cfg.business,
    )
    assert evaluation.threshold == pytest.approx(expected.threshold)


def test_the_threshold_optimal_on_test_is_not_used(evaluated, cfg: Config):
    """Guards against quietly tuning on test: the reported threshold is the
    validation one, whether or not test would have preferred another."""
    evaluation, outcome, splits = evaluated
    test_optimal = recommend_threshold(
        splits.y_test,
        outcome.model.predict_proba(splits.X_test)[:, 1],
        splits.X_test["MonthlyCharges"],
        cfg.business,
    )
    assert evaluation.threshold == pytest.approx(evaluation.validation_scenario.threshold)
    assert evaluation.test_optimal_threshold == pytest.approx(test_optimal.threshold)


def test_test_metrics_are_computed_at_the_chosen_threshold(evaluated):
    evaluation, _, _ = evaluated
    assert evaluation.test_metrics.threshold == pytest.approx(evaluation.threshold)


def test_both_the_default_and_the_tuned_operating_point_are_reported(evaluated):
    """A reader must be able to see what tuning actually bought."""
    evaluation, _, _ = evaluated
    assert evaluation.test_metrics_at_half.threshold == pytest.approx(0.5)
    assert evaluation.test_metrics.threshold == pytest.approx(evaluation.threshold)


def test_test_partition_size_is_reported(evaluated, cfg: Config):
    evaluation, _, splits = evaluated
    assert evaluation.test_metrics.n == len(splits.y_test)


def test_calibration_is_assessed_on_test(evaluated):
    evaluation, _, _ = evaluated
    assert 0.0 <= evaluation.test_ece <= 1.0
    assert not evaluation.reliability.empty


def test_business_figures_carry_the_simulation_disclaimer(evaluated):
    evaluation, _, _ = evaluated
    assert "simulat" in evaluation.test_scenario.disclaimer.lower()


def test_report_writes_a_file_with_the_measured_numbers(evaluated, cfg: Config, tmp_path):
    from churnsense.evaluation.report import write_final_report

    evaluation, _, _ = evaluated
    path = write_final_report(evaluation, {"display_name": "Test Model"}, cfg, directory=tmp_path)
    text = path.read_text()

    assert f"{evaluation.test_metrics.roc_auc:.4f}" in text
    assert "SIMULAT" in text.upper()
    assert "test" in text.lower()
