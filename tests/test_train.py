"""Tests for training, model selection and artifact persistence.

These run on the seeded synthetic fixture, so they exercise the full path
without the real dataset. No test asserts a performance number: the fixture's
statistics are invented, and a passing accuracy threshold on invented data
would be a lie dressed as a test.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from sklearn.calibration import CalibratedClassifierCV

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.exceptions import ModelNotAvailableError
from churnsense.models.calibration_clamp import ProbabilityClamp
from churnsense.models.registry import MODEL_KEYS
from churnsense.models.train import (
    TrainingOutcome,
    load_artifact,
    save_artifact,
    train_all,
)


@pytest.fixture(scope="module")
def splits(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    return make_splits(X, y, cfg)


@pytest.fixture(scope="module")
def outcome(splits, cfg: Config) -> TrainingOutcome:
    return train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)


def test_train_all_cannot_reach_the_test_partition():
    """The guarantee is structural: test data is not a parameter of training."""
    import inspect

    parameters = set(inspect.signature(train_all).parameters)
    assert not any("test" in name for name in parameters), parameters


def test_every_candidate_is_evaluated(outcome: TrainingOutcome):
    assert [r.key for r in outcome.results] == list(MODEL_KEYS)


def test_selection_follows_the_one_standard_error_rule(outcome: TrainingOutcome, cfg: Config):
    """Replaces a test that asserted a plain argmax - the rule the code does
    *not* implement - and so only passed when the two happened to agree.

    Checked from the outside: the winner sits within one standard error of the
    validation leader, and no simpler candidate does.
    """
    from churnsense.models.registry import COMPLEXITY_ORDER

    leader = max(outcome.results, key=lambda r: r.validation.average_precision)
    cutoff = leader.validation.average_precision - leader.cv_std / np.sqrt(cfg.training.cv_folds)
    tied = {r.key for r in outcome.results if r.validation.average_precision >= cutoff}

    assert outcome.selected_key in tied
    simpler = COMPLEXITY_ORDER[: COMPLEXITY_ORDER.index(outcome.selected_key)]
    assert not tied & set(simpler)


def test_the_baseline_is_not_selected(outcome: TrainingOutcome):
    """If the dummy ever wins, the pipeline is broken and must not ship quietly."""
    assert outcome.selected_key != "dummy"


def test_the_baseline_has_no_discrimination(outcome: TrainingOutcome):
    """Anchors the comparison: ROC-AUC 0.5 is what 'learned nothing' looks like."""
    dummy = next(r for r in outcome.results if r.key == "dummy")
    assert dummy.validation.roc_auc == pytest.approx(0.5, abs=0.01)


def test_selected_model_beats_the_baseline(outcome: TrainingOutcome):
    dummy = next(r for r in outcome.results if r.key == "dummy")
    best = next(r for r in outcome.results if r.key == outcome.selected_key)
    assert best.validation.average_precision > dummy.validation.average_precision


def test_outcome_exposes_a_fitted_pipeline(outcome: TrainingOutcome, splits):
    proba = outcome.model.predict_proba(splits.X_val)[:, 1]
    assert proba.shape == (len(splits.X_val),)
    assert ((proba >= 0) & (proba <= 1)).all()


def test_calibration_decision_is_measured_not_assumed(outcome: TrainingOutcome):
    """Calibration is kept only when it actually lowers the calibration error."""
    assert outcome.calibration_applied == (
        outcome.calibrated_ece is not None and outcome.calibrated_ece < outcome.uncalibrated_ece
    )
    if outcome.calibration_applied:
        # The shipped model is the probability clamp around the calibrator.
        assert isinstance(outcome.model, ProbabilityClamp)
        assert isinstance(outcome.model.estimator, CalibratedClassifierCV)


def test_calibration_refits_preprocessing_inside_every_fold(splits, cfg: Config):
    """Leak-free calibration, checked by behaviour rather than by reading code.

    Each calibration fold must carry its own preprocessor, fitted on that
    fold's training rows. The earlier recipe shared one scaler fitted on the
    whole partition, so every fold had seen its own held-out rows.
    """
    from churnsense.models.registry import build_candidate
    from churnsense.models.train import _calibrate

    tuned = build_candidate("logistic_regression", list(splits.X_train.columns), seed=0)
    calibrated, _ = _calibrate(
        tuned.fit(splits.X_train, splits.y_train), splits.X_train, splits.y_train, cfg
    )

    means = [
        fold.estimator.named_steps["preprocess"].named_transformers_["num"]["scale"].mean_[0]
        for fold in calibrated.calibrated_classifiers_
    ]
    full = splits.X_train["tenure"].mean()  # tenure is the first numeric column
    assert len(set(np.round(means, 9))) == len(means), "each fold must fit its own scaler"
    assert all(abs(m - full) > 1e-9 for m in means), "no fold may reuse the full-partition fit"


def test_cv_scores_are_finite(outcome: TrainingOutcome):
    for result in outcome.results:
        assert np.isfinite(result.cv_score), result.key
        assert result.cv_std >= 0


def test_training_is_reproducible(splits, cfg: Config):
    a = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    b = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    assert a.selected_key == b.selected_key
    np.testing.assert_allclose(
        a.model.predict_proba(splits.X_val)[:, 1],
        b.model.predict_proba(splits.X_val)[:, 1],
    )


# --- persistence ------------------------------------------------------------


def test_saved_artifact_round_trips(outcome: TrainingOutcome, splits, tmp_path):
    save_artifact(outcome, tmp_path)
    model, meta = load_artifact(tmp_path)

    np.testing.assert_allclose(
        model.predict_proba(splits.X_val)[:, 1],
        outcome.model.predict_proba(splits.X_val)[:, 1],
    )
    assert isinstance(model, ProbabilityClamp)
    assert meta["model_key"] == outcome.selected_key


def test_metadata_records_what_is_needed_to_reproduce_and_serve(outcome, tmp_path):
    save_artifact(outcome, tmp_path)
    meta = json.loads((tmp_path / "model_meta.json").read_text())

    for field in (
        "model_key",
        "display_name",
        "trained_at",
        "random_seed",
        "feature_columns",
        "threshold",
        "validation_metrics",
        "sklearn_version",
        "python_version",
        "calibrated",
        "n_train",
        "n_validation",
    ):
        assert field in meta, field
    assert meta["feature_columns"], "serving reindexes on this list; it cannot be empty"


def test_metadata_feature_columns_match_the_training_matrix(outcome, splits, tmp_path):
    save_artifact(outcome, tmp_path)
    meta = json.loads((tmp_path / "model_meta.json").read_text())
    assert meta["feature_columns"] == list(splits.X_train.columns)


def test_metadata_is_json_serialisable_without_numpy_scalars(outcome, tmp_path):
    """numpy floats break json.dump; the saver must convert them."""
    save_artifact(outcome, tmp_path)
    raw = (tmp_path / "model_meta.json").read_text()
    assert json.loads(raw)


def test_loading_a_missing_artifact_raises_rather_than_faking_predictions(tmp_path):
    with pytest.raises(ModelNotAvailableError, match="make train"):
        load_artifact(tmp_path / "nothing_here")


def test_loading_an_artifact_without_metadata_is_refused(outcome, tmp_path):
    save_artifact(outcome, tmp_path)
    (tmp_path / "model_meta.json").unlink()
    with pytest.raises(ModelNotAvailableError):
        load_artifact(tmp_path)


# --- selection rule ---------------------------------------------------------


def _result(key: str, val_ap: float, cv_std: float = 0.02):
    """A CandidateResult carrying only the fields the selection rule reads."""
    from churnsense.evaluation.metrics import evaluate
    from churnsense.models.train import CandidateResult

    rng = np.random.default_rng(0)
    y = np.array([0, 1] * 50)
    metrics = evaluate(y, rng.uniform(0, 1, 100))
    metrics = type(metrics)(**{**metrics.as_dict(), "average_precision": val_ap})
    return CandidateResult(
        key=key,
        display_name=key,
        best_params={},
        cv_score=val_ap,
        cv_std=cv_std,
        validation=metrics,
        fit_seconds=0.1,
    )


def test_a_clearly_better_complex_model_wins(cfg: Config):
    """The rule must not punish a model that is genuinely ahead."""
    from churnsense.models.train import select_model

    results = [
        _result("dummy", 0.26),
        _result("logistic_regression", 0.55, cv_std=0.01),
        _result("hist_gradient_boosting", 0.70, cv_std=0.01),
    ]
    assert select_model(results, cfg).key == "hist_gradient_boosting"


def test_a_statistical_tie_goes_to_the_simpler_model(cfg: Config):
    """0.6444 vs 0.6431 against a CV std of 0.02 is noise, not evidence."""
    from churnsense.models.train import select_model

    results = [
        _result("dummy", 0.265),
        _result("logistic_regression", 0.6444, cv_std=0.0244),
        _result("random_forest", 0.6383, cv_std=0.0153),
        _result("hist_gradient_boosting", 0.6431, cv_std=0.0159),
    ]
    assert select_model(results, cfg).key == "logistic_regression"


def test_a_tie_at_the_top_still_prefers_the_simpler_model(cfg: Config):
    """Even when the complex model nominally leads, a tie favours simplicity."""
    from churnsense.models.train import select_model

    results = [
        _result("logistic_regression", 0.6400, cv_std=0.0244),
        _result("hist_gradient_boosting", 0.6450, cv_std=0.0244),
    ]
    assert select_model(results, cfg).key == "logistic_regression"


def test_the_baseline_is_never_selected_over_a_real_model(cfg: Config):
    """Dummy is the simplest of all; it must still have to earn selection."""
    from churnsense.models.train import select_model

    results = [_result("dummy", 0.265), _result("logistic_regression", 0.6444)]
    assert select_model(results, cfg).key == "logistic_regression"


def test_selection_records_why(outcome: TrainingOutcome):
    """An interview answer needs the reason, not just the winner."""
    assert outcome.selection_reason
    assert outcome.selected_key in outcome.selection_reason or "tie" in outcome.selection_reason


def test_reported_validation_metrics_describe_the_shipped_model(outcome, splits, tmp_path):
    """Calibration changes probabilities, so pre-calibration metrics would lie.

    Without this, model_meta.json reports the scores of the *candidate* while
    model.joblib contains the *calibrated* pipeline - two different models
    described by one set of numbers.
    """
    from churnsense.evaluation.metrics import evaluate
    from churnsense.models.train import build_metadata

    meta = build_metadata(outcome, pytest.importorskip("churnsense.config").load_config())
    actual = evaluate(
        splits.y_val,
        outcome.model.predict_proba(splits.X_val)[:, 1],
        threshold=meta["threshold"],
    )
    reported = meta["validation_metrics"]
    assert reported["brier"] == pytest.approx(actual.brier, abs=1e-9)
    assert reported["precision"] == pytest.approx(actual.precision, abs=1e-9)
    assert reported["recall"] == pytest.approx(actual.recall, abs=1e-9)
    assert reported["average_precision"] == pytest.approx(actual.average_precision, abs=1e-9)


# --- feature decisions ------------------------------------------------------


def test_feature_ablation_reverses_each_configured_decision(
    clean_frame, splits, outcome: TrainingOutcome, cfg: Config
):
    """The documentation's "excluding gender costs nothing" must be a measurement."""
    from churnsense.models.train import feature_ablation

    table = feature_ablation(
        clean_frame.loc[splits.X_train.index], splits.y_train, outcome.selected, cfg
    )

    assert list(table["variant"]) == ["as configured", "with gender", "without TotalCharges"]
    assert table.loc[0, "change"] == 0.0
    assert list(table["features"]) == [18, 19, 17]
    assert np.isfinite(table["cv_score"]).all()


def test_the_comparison_report_publishes_the_ablation(
    clean_frame, splits, outcome: TrainingOutcome, cfg: Config, tmp_path
):
    from churnsense.evaluation.report import write_comparison_report
    from churnsense.models.train import feature_ablation

    ablation = feature_ablation(
        clean_frame.loc[splits.X_train.index], splits.y_train, outcome.selected, cfg
    )
    text = write_comparison_report(outcome, cfg, ablation=ablation, directory=tmp_path).read_text()

    assert "## Feature decisions, measured" in text
    assert "with gender" in text
    calibrated_label = "Shipped (calibrated" in text
    assert calibrated_label == outcome.calibration_applied
