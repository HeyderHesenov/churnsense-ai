"""Tests for training, model selection and artifact persistence.

These run on the seeded synthetic fixture, so they exercise the full path
without the real dataset. No test asserts a performance number: the fixture's
statistics are invented, and a passing accuracy threshold on invented data
would be a lie dressed as a test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
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
    """Checked from the outside, on cross-validated scores only: the winner sits
    within one standard error of the CV leader, and no simpler candidate does.

    An earlier version took the leader on validation PR-AUC and subtracted a
    standard error computed from CV folds - two quantities from different
    samples. Every number here comes from the same folds.
    """
    from churnsense.models.registry import COMPLEXITY_ORDER

    leader = max(outcome.results, key=lambda r: r.cv_score)
    cutoff = leader.cv_score - leader.cv_std / np.sqrt(cfg.training.cv_folds)
    tied = {r.key for r in outcome.results if r.cv_score >= cutoff}

    assert outcome.selection.cutoff == pytest.approx(cutoff)
    assert {r.key for r in outcome.selection.eligible} == tied
    assert outcome.selected_key in tied
    simpler = COMPLEXITY_ORDER[: COMPLEXITY_ORDER.index(outcome.selected_key)]
    assert not tied & set(simpler)


def test_every_candidate_records_the_folds_its_score_came_from(outcome, cfg: Config):
    assert {r.cv_folds for r in outcome.results} == {cfg.training.cv_folds}


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


def test_the_threshold_is_chosen_on_validation_with_the_shipped_model(
    outcome: TrainingOutcome, splits, cfg: Config
):
    """`make train` leaves a servable artifact: model and operating point together,
    both from train/validation only."""
    from churnsense.evaluation.threshold import recommend_threshold

    expected = recommend_threshold(
        splits.y_val,
        outcome.model.predict_proba(splits.X_val)[:, 1],
        splits.X_val["MonthlyCharges"],
        cfg.business,
    )
    assert outcome.operating_point.threshold == pytest.approx(expected.threshold)
    assert len(outcome.threshold_sweep) == 101


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


def test_metadata_records_the_operating_point_and_its_assumptions(outcome, tmp_path, cfg):
    from dataclasses import asdict

    save_artifact(outcome, tmp_path, cfg)
    meta = json.loads((tmp_path / "model_meta.json").read_text())
    assert meta["threshold"] == pytest.approx(outcome.operating_point.threshold)
    assert "validation" in meta["threshold_source"]
    assert meta["business_assumptions"] == asdict(cfg.business)
    assert meta["selection"]["eligible"] == [r.key for r in outcome.selection.eligible]


def test_metadata_records_provenance_only_when_it_is_known(outcome, tmp_path, cfg):
    """In-memory data has no file hash; claiming the pinned one would be false."""
    save_artifact(outcome, tmp_path / "a", cfg)
    save_artifact(outcome, tmp_path / "b", cfg, data_sha256="d" * 64, split_digest="s" * 64)
    a = json.loads((tmp_path / "a" / "model_meta.json").read_text())
    b = json.loads((tmp_path / "b" / "model_meta.json").read_text())
    assert a["data_sha256"] is None and a["split_digest"] is None
    assert b["data_sha256"] == "d" * 64 and b["split_digest"] == "s" * 64


def test_the_loader_refuses_a_model_its_metadata_does_not_describe(outcome, tmp_path, cfg):
    """The hash is checked before unpickling: the sidecar's threshold and
    metrics would otherwise be served for a different model."""
    from churnsense.data.download import sha256_of

    model_path = save_artifact(outcome, tmp_path, cfg)
    meta = json.loads((tmp_path / "model_meta.json").read_text())
    assert meta["model_sha256"] == sha256_of(model_path)

    with model_path.open("ab") as fh:
        fh.write(b"\0")
    with pytest.raises(ModelNotAvailableError, match="does not match"):
        load_artifact(tmp_path)


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


def _result(
    key: str,
    cv_score: float,
    cv_std: float = 0.02,
    *,
    cv_folds: int = 5,
    val_ap: float | None = None,
):
    """A CandidateResult carrying only the fields the selection rule reads."""
    from churnsense.evaluation.metrics import evaluate
    from churnsense.models.train import CandidateResult

    rng = np.random.default_rng(0)
    y = np.array([0, 1] * 50)
    metrics = evaluate(y, rng.uniform(0, 1, 100))
    ap = cv_score if val_ap is None else val_ap
    metrics = type(metrics)(**{**metrics.as_dict(), "average_precision": ap})
    return CandidateResult(
        key=key,
        display_name=key,
        best_params={},
        cv_score=cv_score,
        cv_std=cv_std,
        cv_folds=cv_folds,
        validation=metrics,
        fit_seconds=0.1,
    )


#: The committed comparison (reports/model_comparison.csv): CV score and std.
MEASURED = [
    ("dummy", 0.2653, 0.0005),
    ("logistic_regression", 0.6663, 0.0244),
    ("random_forest", 0.6714, 0.0152),
    ("hist_gradient_boosting", 0.6705, 0.0148),
]


def test_the_measured_field_is_a_tie_won_by_the_simplest_model():
    """Leader RF 0.6714, SE 0.0152/sqrt(5) = 0.0068, cutoff 0.6646: all three
    real models clear it, the baseline does not, logistic regression wins."""
    from churnsense.models.train import one_se_selection

    selection = one_se_selection([_result(*row) for row in MEASURED])
    assert selection.leader.key == "random_forest"
    assert selection.standard_error == pytest.approx(0.0152 / np.sqrt(5))
    assert selection.cutoff == pytest.approx(0.6714 - 0.0152 / np.sqrt(5))
    assert {r.key for r in selection.eligible} == {
        "random_forest",
        "hist_gradient_boosting",
        "logistic_regression",
    }
    assert selection.selected.key == "logistic_regression"


def test_a_clearly_better_complex_model_wins():
    """The rule must not punish a model that is genuinely ahead."""
    from churnsense.models.train import select_model

    results = [
        _result("dummy", 0.26),
        _result("logistic_regression", 0.55, cv_std=0.01),
        _result("hist_gradient_boosting", 0.70, cv_std=0.01),
    ]
    assert select_model(results).key == "hist_gradient_boosting"


def test_a_candidate_below_the_cutoff_is_not_eligible():
    """0.66 against a leader of 0.70 with SE 0.0089: outside, so not tied."""
    from churnsense.models.train import one_se_selection

    selection = one_se_selection(
        [_result("logistic_regression", 0.66), _result("random_forest", 0.70)]
    )
    assert [r.key for r in selection.eligible] == ["random_forest"]
    assert selection.selected.key == "random_forest"


def test_a_candidate_exactly_on_the_cutoff_is_eligible():
    from churnsense.models.train import one_se_selection

    leader = _result("random_forest", 0.70, cv_std=0.02 * np.sqrt(5))  # SE exactly 0.02
    selection = one_se_selection([leader, _result("logistic_regression", 0.68)])
    assert selection.selected.key == "logistic_regression"


def test_a_tie_breaks_on_simplicity_not_on_score():
    """Even when the complex model nominally leads, a tie favours simplicity."""
    from churnsense.models.train import select_model

    results = [
        _result("hist_gradient_boosting", 0.6450, cv_std=0.0244),
        _result("random_forest", 0.6440, cv_std=0.0244),
        _result("logistic_regression", 0.6400, cv_std=0.0244),
    ]
    assert select_model(results).key == "logistic_regression"


def test_validation_scores_play_no_part_in_the_rule():
    """Validation is reported beside CV as evidence; swapping it changes nothing."""
    from churnsense.models.train import one_se_selection

    a = [_result("logistic_regression", 0.60, val_ap=0.90), _result("random_forest", 0.70)]
    b = [_result("logistic_regression", 0.60, val_ap=0.10), _result("random_forest", 0.70)]
    assert one_se_selection(a) == one_se_selection(b)


def test_the_baseline_is_never_selected_over_a_real_model():
    """Dummy is the simplest of all; it must still have to earn selection."""
    from churnsense.models.train import select_model

    results = [_result("dummy", 0.265), _result("logistic_regression", 0.6444)]
    assert select_model(results).key == "logistic_regression"


def test_a_baseline_win_stops_training_instead_of_shipping():
    """If nothing beats predicting the prior by one standard error, the pipeline
    is broken: refuse, rather than persist a model that learned nothing."""
    from churnsense.exceptions import ModelSelectionError
    from churnsense.models.train import select_model

    results = [_result("dummy", 0.270), _result("logistic_regression", 0.275, cv_std=0.05)]
    with pytest.raises(ModelSelectionError, match="baseline"):
        select_model(results)


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([], "empty"),
        ([_result("random_forest", 0.7), _result("random_forest", 0.6)], "duplicate"),
        ([_result("xgboost", 0.7)], "COMPLEXITY_ORDER"),
        ([_result("random_forest", float("nan"))], "invalid CV result"),
        ([_result("random_forest", 0.7, cv_std=float("inf"))], "invalid CV result"),
        ([_result("random_forest", 0.7, cv_std=-0.01)], "invalid CV result"),
        ([_result("random_forest", 0.7, cv_folds=1)], "at least 2 folds"),
        ([_result("random_forest", 0.7, cv_folds=0)], "at least 2 folds"),
    ],
)
def test_results_the_rule_cannot_use_are_refused(results, message):
    from churnsense.models.train import select_model

    with pytest.raises(ValueError, match=message):
        select_model(results)


def test_selection_does_not_depend_on_the_order_of_the_results():
    """Including an exact tie at the top: the leader - and with it the standard
    error - is the simpler of the two, whatever order they arrive in."""
    from itertools import permutations

    from churnsense.models.train import one_se_selection

    results = [
        _result("random_forest", 0.70, cv_std=0.01),
        _result("logistic_regression", 0.70, cv_std=0.03),
        _result("hist_gradient_boosting", 0.69, cv_std=0.01),
    ]
    first, *rest = [one_se_selection(list(order)) for order in permutations(results)]
    assert all(selection == first for selection in rest)
    assert first.leader.key == "logistic_regression"


def test_the_reason_the_table_and_the_report_agree_with_the_selection(
    outcome: TrainingOutcome, cfg: Config, tmp_path
):
    """One ModelSelection feeds all three, so they cannot drift apart."""
    from churnsense.evaluation.report import write_comparison_report

    selection = outcome.selection
    reason = outcome.selection_reason
    assert selection.leader.display_name in reason
    assert f"{selection.cutoff:.4f}" in reason
    assert outcome.selected.display_name in reason

    table = outcome.comparison_frame()
    assert list(table["cv_pr_auc"]) == sorted(table["cv_pr_auc"], reverse=True)
    assert set(table.loc[table["within_one_se"], "model"]) == {
        r.display_name for r in selection.eligible
    }
    assert table.loc[table["selected"], "model"].tolist() == [outcome.selected.display_name]

    text = write_comparison_report(outcome, cfg, directory=tmp_path).read_text()
    assert reason in text
    assert f"| {selection.cutoff:.4f} |" in text


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
        threshold=meta["validation_metrics"]["threshold"],
    )
    reported = meta["validation_metrics"]
    assert reported["brier"] == pytest.approx(actual.brier, abs=1e-9)
    assert reported["precision"] == pytest.approx(actual.precision, abs=1e-9)
    assert reported["recall"] == pytest.approx(actual.recall, abs=1e-9)
    assert reported["average_precision"] == pytest.approx(actual.average_precision, abs=1e-9)


# --- the tracked comparison reports -----------------------------------------

#: The columns of reports/model_comparison.csv, in order. Each is a quantity a
#: re-run reproduces: the reports are diffed in git, so wall-clock fit time,
#: which varies by machine and by run, is logged rather than reported.
REPORTED_COLUMNS = [
    "model",
    "cv_pr_auc",
    "cv_std",
    "cv_se",
    "within_one_se",
    "val_pr_auc",
    "val_roc_auc",
    "val_precision",
    "val_recall",
    "val_f1",
    "val_brier",
    "selected",
]
#: The same columns as reports/model_comparison.md heads them.
REPORTED_HEADERS = [
    "Model",
    "CV PR-AUC",
    "CV std",
    "CV SE",
    "Within 1 SE",
    "Val PR-AUC",
    "Val ROC-AUC",
    "Val precision",
    "Val recall",
    "Val F1",
    "Val Brier",
    "Selected",
]


def test_the_comparison_columns_are_the_reproducible_metrics(
    outcome: TrainingOutcome, cfg: Config, tmp_path
):
    """Each column carries its own candidate's number, under its own header."""
    from churnsense.evaluation.report import write_comparison_report

    table = outcome.comparison_frame()
    assert table.to_csv(index=False).splitlines()[0] == ",".join(REPORTED_COLUMNS)
    by_name = {r.display_name: r for r in outcome.results}
    for row in table.to_dict("records"):
        result = by_name[row["model"]]
        v = result.validation
        expected = {
            "cv_pr_auc": result.cv_score,
            "cv_std": result.cv_std,
            "cv_se": result.cv_standard_error,
            "val_pr_auc": v.average_precision,
            "val_roc_auc": v.roc_auc,
            "val_precision": v.precision,
            "val_recall": v.recall,
            "val_f1": v.f1,
            "val_brier": v.brier,
        }
        np.testing.assert_array_equal([row[c] for c in expected], list(expected.values()))

    text = write_comparison_report(outcome, cfg, directory=tmp_path).read_text()
    assert "| " + " | ".join(REPORTED_HEADERS) + " |" in text


def test_fit_time_alone_does_not_change_the_tracked_reports(
    outcome: TrainingOutcome, cfg: Config, tmp_path
):
    """Identical results render byte-identically, whatever the clock said.

    Fit time varies from machine to machine and run to run. If it reached a
    tracked report, every `make train` would leave a diff that is not a result.
    """
    from churnsense.evaluation.report import write_comparison_report
    from churnsense.models.train import one_se_selection

    results = [replace(r, fit_seconds=r.fit_seconds + 1000.0) for r in outcome.results]
    slower = replace(outcome, results=results, selection=one_se_selection(results))

    def rendered(run: TrainingOutcome, name: str) -> tuple[str, bytes]:
        directory = tmp_path / name
        directory.mkdir()
        markdown = write_comparison_report(run, cfg, directory=directory).read_bytes()
        return run.comparison_frame().to_csv(index=False), markdown

    first = rendered(outcome, "first")
    assert rendered(outcome, "again") == first
    assert rendered(slower, "slower") == first


def test_the_tracked_comparison_reports_come_from_one_run():
    """The committed CSV and markdown table are one run through one renderer.

    `make train` writes both. A hand edit to either, or a renderer change
    committed without re-running, makes them disagree.
    """
    from churnsense.evaluation.report import _markdown_table

    reports = Path(__file__).resolve().parents[1] / "reports"
    # The default parser can move the 17th significant digit; read what was written.
    tracked = pd.read_csv(reports / "model_comparison.csv", float_precision="round_trip")
    assert list(tracked.columns) == REPORTED_COLUMNS
    table = _markdown_table(tracked.set_axis(REPORTED_HEADERS, axis=1))
    assert table in (reports / "model_comparison.md").read_text()


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
