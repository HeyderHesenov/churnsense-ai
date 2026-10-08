"""Train every candidate, select one on validation, and persist it.

Run as ``make train`` / ``python -m churnsense.models.train``.

**The test partition is not a parameter of this module.** ``train_all`` takes
training and validation data only, so there is no code path by which model
selection, hyper-parameter tuning or calibration could see the held-out set.
That is a structural guarantee rather than a convention, and the test suite
asserts the signature to keep it that way.

The shipped artifact is fitted on the training partition alone. Refitting on
train+validation would use a third more data, but the operating point - the
calibration map and, in Stage 4, the decision threshold - was chosen on
validation, and an estimator that has seen the data its own operating point
was tuned on cannot be honestly described as held out.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from sklearn.pipeline import Pipeline

from churnsense.config import Config, load_config
from churnsense.data.loader import features_and_target, load_clean
from churnsense.data.split import make_splits
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
)
from churnsense.exceptions import ModelNotAvailableError
from churnsense.logging_setup import get_logger
from churnsense.models.registry import COMPLEXITY_ORDER, DISPLAY_NAMES, iter_candidates

logger = get_logger(__name__)

MODEL_FILENAME = "model.joblib"
META_FILENAME = "model_meta.json"
DEFAULT_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """One model's cross-validated score and its validation performance."""

    key: str
    display_name: str
    best_params: dict[str, Any]
    cv_score: float
    cv_std: float
    validation: ClassificationMetrics
    fit_seconds: float


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    """Everything one training run produced, ready to persist or report."""

    results: list[CandidateResult]
    selected_key: str
    model: Pipeline
    feature_columns: list[str]
    n_train: int
    n_validation: int
    selection_reason: str
    # Scored on the pipeline that is actually persisted. Calibration changes
    # the probabilities, so the selected candidate's own pre-calibration
    # metrics would describe a model that never ships.
    shipped_validation: ClassificationMetrics
    uncalibrated_ece: float
    calibrated_ece: float | None
    calibration_applied: bool
    calibration_method: str | None

    @property
    def selected(self) -> CandidateResult:
        return next(r for r in self.results if r.key == self.selected_key)

    def comparison_frame(self) -> pd.DataFrame:
        """Model comparison as a table, sorted by the selection metric."""
        rows = [
            {
                "model": r.display_name,
                "cv_pr_auc": r.cv_score,
                "cv_std": r.cv_std,
                "val_pr_auc": r.validation.average_precision,
                "val_roc_auc": r.validation.roc_auc,
                "val_precision": r.validation.precision,
                "val_recall": r.validation.recall,
                "val_f1": r.validation.f1,
                "val_brier": r.validation.brier,
                "fit_seconds": r.fit_seconds,
                "selected": r.key == self.selected_key,
            }
            for r in self.results
        ]
        return pd.DataFrame(rows).sort_values("val_pr_auc", ascending=False).reset_index(drop=True)


def _standard_error(result: CandidateResult, cfg: Config) -> float:
    """Standard error of a model's cross-validated score across folds."""
    return result.cv_std / np.sqrt(cfg.training.cv_folds)


def _within_one_se(results: list[CandidateResult], cfg: Config) -> list[CandidateResult]:
    leader = max(results, key=lambda r: r.validation.average_precision)
    cutoff = leader.validation.average_precision - _standard_error(leader, cfg)
    return [r for r in results if r.validation.average_precision >= cutoff]


def select_model(results: list[CandidateResult], cfg: Config | None = None) -> CandidateResult:
    """Pick a model by the one-standard-error rule.

    Taking the plain argmax of validation PR-AUC is tempting and wrong when the
    field is tightly packed: on the real dataset the top three models land
    within 0.006 of each other against a cross-validation standard deviation of
    roughly 0.02, so the "winner" is decided by sampling noise. Re-run with a
    different seed and the ranking reshuffles.

    The classic remedy (Breiman; Hastie et al.) is to treat every model whose
    score is within one standard error of the leader as tied, and to break the
    tie on simplicity. A simpler model is cheaper to serve, easier to explain to
    a retention team, and more likely to hold up on next quarter's data.

    The baseline enjoys no exemption: it is the simplest candidate, but it
    reaches the tie set only if nothing else is meaningfully better - which
    would mean the pipeline had failed and should not ship quietly.
    """
    cfg = cfg or load_config()
    if not results:
        raise ValueError("cannot select a model from an empty result list")
    tied = _within_one_se(results, cfg)
    rank = {key: i for i, key in enumerate(COMPLEXITY_ORDER)}
    return min(tied, key=lambda r: (rank.get(r.key, len(rank)), -r.validation.average_precision))


def selection_reason(
    results: list[CandidateResult], selected: CandidateResult, cfg: Config | None = None
) -> str:
    """One sentence explaining why ``selected`` won - for the report and logs."""
    cfg = cfg or load_config()
    leader = max(results, key=lambda r: r.validation.average_precision)
    se = _standard_error(leader, cfg)
    tied = _within_one_se(results, cfg)

    if selected.key == leader.key and len(tied) == 1:
        return (
            f"{selected.display_name} leads validation PR-AUC at "
            f"{selected.validation.average_precision:.4f}, clear of every other candidate "
            f"by more than one standard error ({se:.4f})."
        )
    names = ", ".join(r.display_name for r in tied if r.key != selected.key)
    return (
        f"{len(tied)} models tie within one standard error ({se:.4f}) of the best validation "
        f"PR-AUC ({leader.validation.average_precision:.4f}): {selected.display_name} "
        f"({selected.validation.average_precision:.4f}) alongside {names}. "
        f"{selected.display_name} is selected as the simplest of the tied set."
    )


def _tune(
    pipeline: Pipeline,
    grid: dict[str, list[Any]],
    X: pd.DataFrame,
    y: pd.Series,
    cfg: Config,
) -> tuple[Pipeline, dict[str, Any], float, float]:
    """Grid-search ``pipeline`` on the training partition.

    An empty grid still goes through ``GridSearchCV`` so that every candidate -
    the baseline included - gets a cross-validated score computed the same way.
    A special case for the dummy would make the comparison table's first row
    incomparable with the rest.
    """
    cv = StratifiedKFold(n_splits=cfg.training.cv_folds, shuffle=True, random_state=cfg.random_seed)
    search = GridSearchCV(
        pipeline,
        grid,
        scoring=cfg.training.scoring,
        cv=cv,
        n_jobs=cfg.training.n_jobs,
        refit=True,
        error_score="raise",
    )
    search.fit(X, y)
    index = search.best_index_
    return (
        search.best_estimator_,
        search.best_params_,
        float(search.cv_results_["mean_test_score"][index]),
        float(search.cv_results_["std_test_score"][index]),
    )


def _calibrate(
    pipeline: Pipeline, X: pd.DataFrame, y: pd.Series, cfg: Config
) -> tuple[Pipeline, str]:
    """Wrap the fitted classifier in cross-validated calibration.

    Fitted with ``cv`` on the *training* data rather than ``cv="prefit"`` on
    validation: prefit calibration would consume the validation set, which is
    still needed to judge the result and to choose the threshold.

    Isotonic regression needs a few hundred samples per fold to behave; below
    that it overfits badly, so small training sets fall back to Platt scaling.
    """
    method = cfg.training.calibration.get("method", "isotonic")
    folds = int(cfg.training.calibration.get("cv_folds", cfg.training.cv_folds))
    if method == "isotonic" and len(y) / folds < 300:
        logger.info("training set too small for isotonic calibration; using sigmoid")
        method = "sigmoid"

    classifier = CalibratedClassifierCV(
        pipeline.named_steps["classifier"], method=method, cv=folds, n_jobs=cfg.training.n_jobs
    )
    calibrated = Pipeline(
        [("preprocess", pipeline.named_steps["preprocess"]), ("classifier", classifier)]
    )
    calibrated.fit(X, y)
    return calibrated, method


def train_all(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_validation: pd.DataFrame,
    y_validation: pd.Series,
    cfg: Config | None = None,
) -> TrainingOutcome:
    """Tune, score and select a model. Never sees the test partition."""
    cfg = cfg or load_config()
    columns = list(X_train.columns)
    results: list[CandidateResult] = []
    fitted: dict[str, Pipeline] = {}

    for key, pipeline, grid in iter_candidates(columns, cfg):
        started = time.perf_counter()
        best, params, cv_score, cv_std = _tune(pipeline, grid, X_train, y_train, cfg)
        elapsed = time.perf_counter() - started

        metrics = evaluate(y_validation, best.predict_proba(X_validation)[:, 1])
        fitted[key] = best
        results.append(
            CandidateResult(
                key=key,
                display_name=DISPLAY_NAMES[key],
                best_params=params,
                cv_score=cv_score,
                cv_std=cv_std,
                validation=metrics,
                fit_seconds=elapsed,
            )
        )
        logger.info(
            "%-24s cv_pr_auc=%.4f +/-%.4f  val_pr_auc=%.4f  val_roc_auc=%.4f  %.1fs",
            key,
            cv_score,
            cv_std,
            metrics.average_precision,
            metrics.roc_auc,
            elapsed,
        )

    selected = select_model(results, cfg)
    reason = selection_reason(results, selected, cfg)
    model = fitted[selected.key]
    logger.info("selected %s - %s", selected.key, reason)

    # --- calibration: applied only if it measurably helps ---------------------
    uncalibrated_ece = expected_calibration_error(
        y_validation, model.predict_proba(X_validation)[:, 1]
    )
    calibrated_ece: float | None = None
    method: str | None = None
    applied = False

    if cfg.training.calibration_enabled:
        candidate, method = _calibrate(model, X_train, y_train, cfg)
        calibrated_ece = expected_calibration_error(
            y_validation, candidate.predict_proba(X_validation)[:, 1]
        )
        applied = calibrated_ece < uncalibrated_ece
        logger.info(
            "calibration (%s): ECE %.4f -> %.4f, %s",
            method,
            uncalibrated_ece,
            calibrated_ece,
            "applied" if applied else "rejected (did not improve)",
        )
        if applied:
            model = candidate
        else:
            method = None

    shipped_validation = evaluate(y_validation, model.predict_proba(X_validation)[:, 1])
    if applied:
        logger.info(
            "shipped model after calibration: val_pr_auc=%.4f brier=%.4f (candidate: %.4f / %.4f)",
            shipped_validation.average_precision,
            shipped_validation.brier,
            selected.validation.average_precision,
            selected.validation.brier,
        )

    return TrainingOutcome(
        results=results,
        selected_key=selected.key,
        model=model,
        feature_columns=columns,
        selection_reason=reason,
        shipped_validation=shipped_validation,
        n_train=len(y_train),
        n_validation=len(y_validation),
        uncalibrated_ece=uncalibrated_ece,
        calibrated_ece=calibrated_ece,
        calibration_applied=applied,
        calibration_method=method,
    )


def _jsonable(value: Any) -> Any:
    """Convert numpy scalars so ``json.dump`` does not choke on them."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_metadata(outcome: TrainingOutcome, cfg: Config) -> dict[str, Any]:
    """The sidecar that makes an artifact self-describing.

    Recording the library versions matters: a pickle written by one scikit-learn
    and read by another is the classic silent-breakage path, and the loader
    warns when they differ rather than letting predictions drift unnoticed.
    """
    selected = outcome.selected
    return _jsonable(
        {
            "model_key": outcome.selected_key,
            "display_name": selected.display_name,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "random_seed": cfg.random_seed,
            "feature_columns": outcome.feature_columns,
            "dropped_columns": list(cfg.features.drop_columns),
            "include_total_charges": cfg.features.include_total_charges,
            "data_sha256": cfg.dataset.sha256,
            "n_train": outcome.n_train,
            "n_validation": outcome.n_validation,
            "best_params": selected.best_params,
            "cv_metric": cfg.training.scoring,
            "cv_score": selected.cv_score,
            "cv_std": selected.cv_std,
            "calibrated": outcome.calibration_applied,
            "calibration_method": outcome.calibration_method,
            "uncalibrated_ece": outcome.uncalibrated_ece,
            "calibrated_ece": outcome.calibrated_ece,
            # Default operating point. `make evaluate` replaces it with the
            # business-optimal threshold chosen on validation.
            "threshold": DEFAULT_THRESHOLD,
            "threshold_source": "default (0.5); run `make evaluate` to tune it",
            "validation_metrics": outcome.shipped_validation.as_dict(),
            "candidate_validation_metrics": selected.validation.as_dict(),
            "sklearn_version": sklearn.__version__,
            "python_version": platform.python_version(),
        }
    )


def save_artifact(outcome: TrainingOutcome, directory: Path, cfg: Config | None = None) -> Path:
    """Write ``model.joblib`` and its metadata sidecar. Returns the model path."""
    cfg = cfg or load_config()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    model_path = directory / MODEL_FILENAME
    joblib.dump(outcome.model, model_path, compress=3)
    (directory / META_FILENAME).write_text(
        json.dumps(build_metadata(outcome, cfg), indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "saved %s (%.1f KB) and %s", MODEL_FILENAME, model_path.stat().st_size / 1024, META_FILENAME
    )
    return model_path


def load_artifact(directory: Path) -> tuple[Pipeline, dict[str, Any]]:
    """Load the trained pipeline and its metadata.

    Raises rather than degrading: a missing or unreadable artifact must surface
    as "no model available" everywhere, never as a default or placeholder
    prediction. Only the configured artifact directory is ever read - a
    user-supplied path is never passed to ``joblib.load``, since unpickling is
    equivalent to executing the file.
    """
    directory = Path(directory)
    model_path, meta_path = directory / MODEL_FILENAME, directory / META_FILENAME

    if not model_path.is_file() or not meta_path.is_file():
        raise ModelNotAvailableError(
            f"no trained model in {directory}. Run `make train` to create one."
        )
    try:
        model = joblib.load(model_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelNotAvailableError(
            f"the artifact in {directory} could not be loaded ({exc.__class__.__name__}). "
            "Retrain with `make train`."
        ) from exc

    if meta.get("sklearn_version") != sklearn.__version__:
        logger.warning(
            "artifact was trained with scikit-learn %s but %s is installed; "
            "predictions may differ. Retrain to be sure.",
            meta.get("sklearn_version"),
            sklearn.__version__,
        )
    return model, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and compare churn models.")
    parser.add_argument(
        "--report",
        action="store_true",
        default=True,
        help="write reports/model_comparison.md (default: on)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    cfg.paths.ensure()

    df, _ = load_clean(cfg)
    X, y = features_and_target(df, cfg)
    splits = make_splits(X, y, cfg)

    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    save_artifact(outcome, cfg.paths.artifacts_dir, cfg)

    comparison = outcome.comparison_frame()
    comparison.to_csv(cfg.paths.reports_dir / "model_comparison.csv", index=False)
    if args.report:
        from churnsense.evaluation.report import write_comparison_report

        write_comparison_report(outcome, cfg)

    print(comparison.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"\nSelected: {outcome.selected.display_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
