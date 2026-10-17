"""Train every candidate, select one, calibrate it, fix its operating point.

Run as ``make train`` / ``python -m churnsense.models.train``.

Who decides what:

* **training partition, cross-validated** - hyper-parameters, and the model
  itself (the one-standard-error rule on CV scores);
* **validation partition** - whether calibration is kept, and the decision
  threshold; its scores are also reported as evidence beside the CV ones.

**The test partition is not a parameter of this module.** ``train_all`` takes
training and validation data only, so there is no code path by which model
selection, hyper-parameter tuning, calibration or the threshold could see the
held-out set. The test suite asserts the signature to keep it that way.

The shipped artifact is fitted on the training partition alone. Refitting on
train+validation would use a third more data, but the calibration map and the
decision threshold were chosen on validation, and an estimator that has seen
the data its own operating point was tuned on cannot be honestly described as
held out.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.data.download import sha256_of
from churnsense.data.loader import features_and_target, load_clean
from churnsense.data.split import make_splits
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
)
from churnsense.evaluation.threshold import (
    ThresholdScenario,
    recommend_threshold_from,
    sweep_thresholds,
)
from churnsense.exceptions import ModelNotAvailableError, ModelSelectionError
from churnsense.logging_setup import configure_logging
from churnsense.models.calibration_clamp import ProbabilityClamp, epsilon_for
from churnsense.models.registry import (
    BASELINE_KEY,
    COMPLEXITY_ORDER,
    DISPLAY_NAMES,
    build_candidate,
    iter_candidates,
)

logger = logging.getLogger(__name__)

MODEL_FILENAME = "model.joblib"
META_FILENAME = "model_meta.json"
THRESHOLD_SOURCE = (
    "highest threshold whose simulated net benefit on the validation partition is "
    "within one retention offer's cost of the best, under the business assumptions "
    "recorded in business_assumptions"
)


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """One model's cross-validated score and its validation performance."""

    key: str
    display_name: str
    best_params: dict[str, Any]
    cv_score: float
    cv_std: float
    cv_folds: int
    validation: ClassificationMetrics
    #: Wall-clock tuning time. Logged, never reported: it varies by machine and run.
    fit_seconds: float

    @property
    def cv_standard_error(self) -> float:
        """Standard error of ``cv_score``: the fold-to-fold spread over sqrt(k)."""
        return self.cv_std / math.sqrt(self.cv_folds)


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """The one-standard-error rule, applied once.

    The selected model, the sentence explaining it and the comparison report
    all read this one object, so they cannot disagree about the leader, the
    cutoff or the eligible set.
    """

    leader: CandidateResult
    cutoff: float
    eligible: tuple[CandidateResult, ...]
    selected: CandidateResult

    @property
    def standard_error(self) -> float:
        return self.leader.cv_standard_error

    @property
    def reason(self) -> str:
        """Why ``selected`` won, in one or two sentences - for the report and logs."""
        leader, selected = self.leader, self.selected
        header = (
            f"{leader.display_name} leads {leader.cv_folds}-fold cross-validation at "
            f"{leader.cv_score:.4f}; one standard error ({self.standard_error:.4f}) "
            f"below it puts the cutoff at {self.cutoff:.4f}."
        )
        if len(self.eligible) == 1:
            return (
                f"{header} No other candidate reaches it, so {selected.display_name} is selected."
            )
        names = ", ".join(f"{r.display_name} ({r.cv_score:.4f})" for r in self.eligible)
        return (
            f"{header} {len(self.eligible)} candidates reach it: {names}. "
            f"{selected.display_name} is the simplest of them and is selected."
        )


def _check_results(results: list[CandidateResult]) -> None:
    """Refuse inputs the rule cannot be applied to honestly."""
    if not results:
        raise ValueError("cannot select a model from an empty result list")
    keys = [r.key for r in results]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate candidate keys: {keys}")
    for r in results:
        if r.key not in COMPLEXITY_ORDER:
            raise ValueError(f"'{r.key}' has no place in COMPLEXITY_ORDER, so no tie can be broken")
        if r.cv_folds < 2:
            raise ValueError(f"{r.key}: a standard error needs at least 2 folds, got {r.cv_folds}")
        if not (math.isfinite(r.cv_score) and math.isfinite(r.cv_std)) or r.cv_std < 0:
            raise ValueError(f"{r.key}: invalid CV result (score {r.cv_score}, std {r.cv_std})")


def one_se_selection(results: list[CandidateResult]) -> ModelSelection:
    """Pick a model by the one-standard-error rule on cross-validated scores.

    Taking the plain argmax is tempting and wrong when the field is tightly
    packed: on the real dataset the three real models sit within about 0.005
    of each other in cross-validation, against a standard error of about
    0.007, so the "winner" is decided by sampling noise.

    The classic remedy (Breiman et al.; Hastie, Tibshirani and Friedman) treats
    every model within one standard error of the leader as tied and breaks the
    tie on simplicity. Every quantity comes from the same place - the shared
    cross-validation folds on the training partition: the leader is the best
    mean CV score, the standard error is that model's fold standard deviation
    over sqrt(k), and the cutoff is applied to the other models' mean CV
    scores. Validation scores are reported beside them as separate evidence;
    they play no part in the rule.

    The baseline enjoys no exemption: it is the simplest candidate, so if it
    reaches the eligible set nothing else is meaningfully better than
    predicting the prior. That is a broken pipeline, and it raises
    ``ModelSelectionError`` rather than shipping quietly.
    """
    _check_results(results)
    rank = {key: i for i, key in enumerate(COMPLEXITY_ORDER)}
    # Exact ties on the score go to the simpler model, so the leader - and the
    # standard error taken from it - never depends on the input order.
    leader = max(results, key=lambda r: (r.cv_score, -rank[r.key]))
    cutoff = leader.cv_score - leader.cv_standard_error
    eligible = tuple(
        sorted(
            (r for r in results if r.cv_score >= cutoff),
            key=lambda r: (-r.cv_score, rank[r.key]),
        )
    )
    selected = min(eligible, key=lambda r: rank[r.key])
    if selected.key == BASELINE_KEY:
        raise ModelSelectionError(
            "no candidate beats the baseline by more than one standard error of "
            f"cross-validation ({leader.display_name} {leader.cv_score:.4f}, cutoff "
            f"{cutoff:.4f}); refusing to ship a model that learned nothing"
        )
    return ModelSelection(leader=leader, cutoff=cutoff, eligible=eligible, selected=selected)


def select_model(results: list[CandidateResult]) -> CandidateResult:
    """The candidate the one-standard-error rule selects."""
    return one_se_selection(results).selected


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    """Everything one training run produced, ready to persist or report."""

    results: list[CandidateResult]
    selection: ModelSelection
    model: ProbabilityClamp
    feature_columns: list[str]
    n_train: int
    n_validation: int
    # Scored on the pipeline that is actually persisted. Calibration changes
    # the probabilities, so the selected candidate's own pre-calibration
    # metrics would describe a model that never ships.
    shipped_validation: ClassificationMetrics
    uncalibrated_ece: float
    calibrated_ece: float | None
    calibration_applied: bool
    calibration_method: str | None
    # The operating point, chosen on validation with the shipped model, so the
    # artifact leaves `make train` ready to serve - no test data involved.
    threshold_sweep: pd.DataFrame
    operating_point: ThresholdScenario

    @property
    def selected(self) -> CandidateResult:
        return self.selection.selected

    @property
    def selected_key(self) -> str:
        return self.selection.selected.key

    @property
    def selection_reason(self) -> str:
        return self.selection.reason

    def comparison_frame(self) -> pd.DataFrame:
        """Model comparison as a table, ordered by the cross-validated selection score.

        It feeds the tracked reports, so it holds only what a re-run reproduces.
        """
        eligible = {r.key for r in self.selection.eligible}
        rows = [
            {
                "model": r.display_name,
                "cv_pr_auc": r.cv_score,
                "cv_std": r.cv_std,
                "cv_se": r.cv_standard_error,
                "within_one_se": r.key in eligible,
                "val_pr_auc": r.validation.average_precision,
                "val_roc_auc": r.validation.roc_auc,
                "val_precision": r.validation.precision,
                "val_recall": r.validation.recall,
                "val_f1": r.validation.f1,
                "val_brier": r.validation.brier,
                "selected": r.key == self.selected_key,
            }
            for r in self.results
        ]
        return pd.DataFrame(rows).sort_values("cv_pr_auc", ascending=False).reset_index(drop=True)


def _folds(cfg: Config) -> StratifiedKFold:
    """The cross-validation splitter every training-partition score uses."""
    return StratifiedKFold(
        n_splits=cfg.training.cv_folds, shuffle=True, random_state=cfg.random_seed
    )


def _tune(
    pipeline: Pipeline,
    grid: dict[str, list[Any]],
    X: pd.DataFrame,
    y: pd.Series,
    cfg: Config,
) -> tuple[Pipeline, dict[str, Any], float, float, int]:
    """Grid-search ``pipeline`` on the training partition.

    An empty grid still goes through ``GridSearchCV`` so that every candidate -
    the baseline included - gets a cross-validated score computed the same way.
    A special case for the dummy would make the comparison table's first row
    incomparable with the rest.
    """
    search = GridSearchCV(
        pipeline,
        grid,
        scoring=cfg.training.scoring,
        cv=_folds(cfg),
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
        search.n_splits_,
    )


def _calibrate(
    pipeline: Pipeline, X: pd.DataFrame, y: pd.Series, cfg: Config
) -> tuple[CalibratedClassifierCV, str]:
    """Calibrate the tuned pipeline with cross-validation on the training data.

    The *whole* pipeline is calibrated, so every calibration fold refits the
    preprocessor on that fold's own training rows - the same discipline as the
    grid search. An earlier version put the preprocessor already fitted on all
    of train in front of a calibrated classifier, which let each fold's
    held-out rows shape the scaler that fold was calibrated through.

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

    calibrated = CalibratedClassifierCV(
        pipeline, method=method, cv=folds, n_jobs=cfg.training.n_jobs
    )
    return calibrated.fit(X, y), method


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
        best, params, cv_score, cv_std, folds = _tune(pipeline, grid, X_train, y_train, cfg)
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
                cv_folds=folds,
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

    selection = one_se_selection(results)
    selected = selection.selected
    model: Pipeline | CalibratedClassifierCV = fitted[selected.key]
    logger.info("selected %s - %s", selected.key, selection.reason)

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

    # Clamp before anything measures or persists the model, so the metrics in
    # model_meta.json, the evaluation report, SHAP and the API all describe the
    # one function that is actually served. Applied after the calibration
    # comparison above, which must see raw probabilities to judge ECE honestly.
    shipped = ProbabilityClamp(model, epsilon=epsilon_for(len(y_train)))

    validation_proba = shipped.predict_proba(X_validation)[:, 1]
    shipped_validation = evaluate(y_validation, validation_proba)
    if applied:
        logger.info(
            "shipped model after calibration: val_pr_auc=%.4f brier=%.4f (candidate: %.4f / %.4f)",
            shipped_validation.average_precision,
            shipped_validation.brier,
            selected.validation.average_precision,
            selected.validation.brier,
        )

    # The operating point: a business decision, made on validation with the
    # model that ships, so the threshold and the probabilities it cuts belong
    # to the same function. One sweep feeds both the choice and the report.
    sweep = sweep_thresholds(
        y_validation, validation_proba, X_validation["MonthlyCharges"], cfg.business
    )
    operating_point = recommend_threshold_from(sweep, cfg.business)
    logger.info(
        "threshold %.2f chosen on validation (simulated net benefit %s%.0f)",
        operating_point.threshold,
        cfg.business.currency,
        operating_point.net_benefit,
    )

    return TrainingOutcome(
        results=results,
        selection=selection,
        model=shipped,
        feature_columns=columns,
        shipped_validation=shipped_validation,
        n_train=len(y_train),
        n_validation=len(y_validation),
        uncalibrated_ece=uncalibrated_ece,
        calibrated_ece=calibrated_ece,
        calibration_applied=applied,
        calibration_method=method,
        threshold_sweep=sweep,
        operating_point=operating_point,
    )


def feature_ablation(
    frame: pd.DataFrame, y: pd.Series, selected: CandidateResult, cfg: Config | None = None
) -> pd.DataFrame:
    """What each configured feature decision costs, measured rather than asserted.

    The selected model family, with its tuned hyper-parameters, is re-scored
    with each decision in ``features`` reversed: ``gender`` put back in, and
    ``TotalCharges`` flipped. Cross-validated on the training partition with
    the grid search's own folds, so no validation or test data is spent on it.

    ``frame`` must carry every candidate column, including the ones the
    shipped model drops - the clean training rows, not the model matrix.
    """
    cfg = cfg or load_config()
    drops, include = cfg.features.drop_columns, cfg.features.include_total_charges
    variants = {"as configured": schema.model_input_columns(drops, include)}
    if "gender" in drops:
        kept = tuple(c for c in drops if c != "gender")
        variants["with gender"] = schema.model_input_columns(kept, include)
    variants[f"{'without' if include else 'with'} TotalCharges"] = schema.model_input_columns(
        drops, not include
    )

    rows = []
    for name, columns in variants.items():
        pipeline = build_candidate(selected.key, columns, seed=cfg.random_seed)
        scores = cross_val_score(
            pipeline.set_params(**selected.best_params),
            frame[columns],
            y,
            cv=_folds(cfg),
            scoring=cfg.training.scoring,
            n_jobs=cfg.training.n_jobs,
        )
        rows.append(
            {
                "variant": name,
                "features": len(columns),
                "cv_score": float(scores.mean()),
                "cv_std": float(scores.std()),
            }
        )
    table = pd.DataFrame(rows)
    table["change"] = table["cv_score"] - table.loc[0, "cv_score"]
    return table


def _jsonable(value: Any) -> Any:
    """Convert numpy scalars so ``json.dump`` does not choke on them."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_metadata(
    outcome: TrainingOutcome,
    cfg: Config,
    *,
    data_sha256: str | None = None,
    split_digest: str | None = None,
) -> dict[str, Any]:
    """The sidecar that makes an artifact self-describing.

    Recording the library versions matters: a pickle written by one scikit-learn
    and read by another is the classic silent-breakage path, and the loader
    warns when they differ rather than letting predictions drift unnoticed.

    ``data_sha256`` is the hash of the file actually read (verified against the
    pin by ``load_clean``), and ``split_digest`` fingerprints the partition the
    model was trained and tuned on; both are ``None`` for in-memory data.
    """
    selected, selection = outcome.selected, outcome.selection
    return _jsonable(
        {
            "model_key": outcome.selected_key,
            "display_name": selected.display_name,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "random_seed": cfg.random_seed,
            "feature_columns": outcome.feature_columns,
            "dropped_columns": list(cfg.features.drop_columns),
            "include_total_charges": cfg.features.include_total_charges,
            "data_sha256": data_sha256,
            "split_digest": split_digest,
            "n_train": outcome.n_train,
            "n_validation": outcome.n_validation,
            "best_params": selected.best_params,
            "cv_metric": cfg.training.scoring,
            "cv_score": selected.cv_score,
            "cv_std": selected.cv_std,
            "cv_folds": selected.cv_folds,
            "selection": {
                "rule": "one standard error on the cross-validated score",
                "leader": selection.leader.key,
                "standard_error": selection.standard_error,
                "cutoff": selection.cutoff,
                "eligible": [r.key for r in selection.eligible],
            },
            "calibrated": outcome.calibration_applied,
            "calibration_method": outcome.calibration_method,
            "uncalibrated_ece": outcome.uncalibrated_ece,
            "calibrated_ece": outcome.calibrated_ece,
            "probability_epsilon": epsilon_for(outcome.n_train),
            "threshold": outcome.operating_point.threshold,
            "threshold_source": THRESHOLD_SOURCE,
            "business_assumptions": asdict(cfg.business),
            "validation_metrics": outcome.shipped_validation.as_dict(),
            "candidate_validation_metrics": selected.validation.as_dict(),
            "sklearn_version": sklearn.__version__,
            "python_version": platform.python_version(),
        }
    )


def save_artifact(
    outcome: TrainingOutcome,
    directory: Path,
    cfg: Config | None = None,
    *,
    data_sha256: str | None = None,
    split_digest: str | None = None,
) -> Path:
    """Write ``model.joblib`` and its metadata sidecar. Returns the model path.

    The sidecar records the model file's own SHA-256, so every number later
    attached to this artifact - including a final evaluation - names the exact
    bytes it describes, and the loader refuses a model the metadata does not.
    """
    cfg = cfg or load_config()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    model_path = directory / MODEL_FILENAME
    joblib.dump(outcome.model, model_path, compress=3)
    meta = build_metadata(outcome, cfg, data_sha256=data_sha256, split_digest=split_digest)
    meta["model_sha256"] = sha256_of(model_path)
    (directory / META_FILENAME).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "saved %s (%.1f KB) and %s", MODEL_FILENAME, model_path.stat().st_size / 1024, META_FILENAME
    )
    return model_path


#: Sidecar keys read on every load or request - by serving, the dashboard's
#: sidebar and ``GET /model-info``. Checking them here turns a missing one into
#: "no model available" (503) instead of a KeyError deep in a request (500).
#: ``build_metadata`` writes every one, none of them null, so an absent or
#: null key means a stale or edited file.
REQUIRED_METADATA: tuple[str, ...] = (
    "model_key",
    "display_name",
    "trained_at",
    "threshold",
    "threshold_source",
    "calibrated",
    "feature_columns",
    "dropped_columns",
    "n_train",
    "n_validation",
    "probability_epsilon",
    "validation_metrics",
    "sklearn_version",
    "model_sha256",
)


#: The type ``build_metadata`` writes for each value, which is the type its
#: readers assume. ``trained_at: 5``, for example, used to load; then
#: /model-info returned 500 and the sidebar raised. The threshold and the
#: column lists need more than a type and are checked on their own.
_METADATA_TYPES: dict[str, tuple[type, ...]] = {
    "model_key": (str,),
    "display_name": (str,),
    "trained_at": (str,),
    "threshold_source": (str,),
    "sklearn_version": (str,),
    "model_sha256": (str,),
    "calibrated": (bool,),
    "n_train": (int,),
    "n_validation": (int,),
    "probability_epsilon": (int, float),
    "validation_metrics": (dict,),
    # Null when calibration was not kept, or before a final evaluation.
    "calibration_method": (str, type(None)),
    "test_metrics": (dict, type(None)),
}


def _invalid_metadata(meta: dict[str, Any]) -> list[str]:
    """What in a complete sidecar no model could be served with."""
    problems = []
    for key, types in _METADATA_TYPES.items():
        value = meta.get(key)
        # A bool is an int to Python, but neither a count nor a rate.
        if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
            expected = " or ".join("null" if t is type(None) else t.__name__ for t in types)
            problems.append(f"{key} must be {expected}, got {type(value).__name__}")
    # build_metadata records a method exactly when calibration was kept, and
    # the performance page names it whenever calibrated is true.
    if meta["calibrated"] is True and meta.get("calibration_method") is None:
        problems.append("calibration_method must be recorded when calibrated is true")
    threshold = meta["threshold"]
    # A bool is an int but not a probability; written this way round, NaN fails too.
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not 0.0 <= threshold <= 1.0
    ):
        problems.append(f"threshold must be a number in [0, 1], got {threshold!r}")
    columns = meta["feature_columns"]
    # Outside the contract, every request would be refused as "missing columns".
    if not (
        isinstance(columns, list)
        and columns
        and all(isinstance(c, str) for c in columns)
        and set(columns) <= set(schema.model_input_columns())
    ):
        problems.append("feature_columns must be a non-empty list of data-contract columns")
    dropped = meta["dropped_columns"]
    if not (isinstance(dropped, list) and all(isinstance(c, str) for c in dropped)):
        problems.append("dropped_columns must be a list of column names")
    return problems


def load_artifact(directory: Path) -> tuple[ProbabilityClamp, dict[str, Any]]:
    """Load the trained model and its metadata.

    Raises rather than degrading: a missing or unreadable artifact must surface
    as "no model available" everywhere, never as a default or placeholder
    prediction. Only the configured artifact directory is ever read - a
    user-supplied path is never passed to ``joblib.load``, since unpickling is
    equivalent to executing the file.

    The metadata is read and checked first, and the model is unpickled only
    when the sidecar is complete, its values can be served, and the file's
    bytes match the hash it records: otherwise its threshold and metrics would
    describe a different model, or none. The hash proves the two files belong
    together, not where they came from: whoever can replace the pickle can
    rewrite the sidecar too.
    """
    directory = Path(directory)
    model_path, meta_path = directory / MODEL_FILENAME, directory / META_FILENAME

    if not model_path.is_file() or not meta_path.is_file():
        raise ModelNotAvailableError(
            f"no trained model in {directory}. Run `make train` to create one."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelNotAvailableError(
            f"the metadata in {directory} could not be read ({exc.__class__.__name__}). "
            "Retrain with `make train`."
        ) from exc
    if not isinstance(meta, dict):
        raise ModelNotAvailableError(
            f"the metadata in {directory} is not a JSON object. Retrain with `make train`."
        )
    if absent := [key for key in REQUIRED_METADATA if meta.get(key) is None]:
        raise ModelNotAvailableError(
            f"the artifact in {directory} has incomplete metadata (missing or null "
            f"{', '.join(absent)}). Retrain with `make train`."
        )
    if problems := _invalid_metadata(meta):
        raise ModelNotAvailableError(
            f"the metadata in {directory} cannot be served: {'; '.join(problems)}. "
            "Retrain with `make train`."
        )
    try:
        digest = sha256_of(model_path)
    except OSError as exc:
        raise ModelNotAvailableError(
            f"{MODEL_FILENAME} in {directory} could not be read ({exc.__class__.__name__}). "
            "Check its permissions, or retrain with `make train`."
        ) from exc
    if digest != meta["model_sha256"]:
        raise ModelNotAvailableError(
            f"{MODEL_FILENAME} in {directory} does not match the model_sha256 its metadata "
            "records. Retrain with `make train`."
        )
    try:
        model = joblib.load(model_path)
    except Exception as exc:
        raise ModelNotAvailableError(
            f"the artifact in {directory} could not be loaded ({exc.__class__.__name__}). "
            "Retrain with `make train`."
        ) from exc

    if meta["sklearn_version"] != sklearn.__version__:
        logger.warning(
            "artifact was trained with scikit-learn %s but %s is installed; "
            "predictions may differ. Retrain to be sure.",
            meta["sklearn_version"],
            sklearn.__version__,
        )
    return model, meta


#: Who the validation breakdown is reported for. ``gender`` is not a model
#: input; it is here precisely because excluding a column does not prove the
#: model treats the groups alike.
VALIDATION_SEGMENTS: tuple[str, ...] = ("Contract", "InternetService", "SeniorCitizen", "gender")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and compare churn models.")
    # BooleanOptionalAction, not store_true: `action="store_true", default=True`
    # is unsatisfiable - the flag reads True with or without it, so the `if`
    # below was permanently taken and the CLI advertised a toggle that did
    # not exist.
    parser.add_argument(
        "--report",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="write reports/model_comparison.md",
    )
    args = parser.parse_args(argv)
    configure_logging()

    cfg = load_config()
    cfg.paths.ensure()

    df, cleaning = load_clean(cfg)
    X, y = features_and_target(df, cfg)
    splits = make_splits(X, y, cfg)

    try:
        outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    except ModelSelectionError as exc:
        logger.error("%s", exc)
        return 1
    save_artifact(
        outcome,
        cfg.paths.artifacts_dir,
        cfg,
        data_sha256=cleaning.source_sha256,
        split_digest=splits.digest(),
    )

    # Everything below is validation-derived: the sweep the threshold came
    # from, its figure, and the per-segment view of the chosen operating point.
    from churnsense.evaluation import figures
    from churnsense.evaluation.metrics import segment_table
    from churnsense.evaluation.threshold import indifference_band, threshold_sensitivity

    reports, threshold = cfg.paths.reports_dir, outcome.operating_point.threshold
    comparison = outcome.comparison_frame()
    comparison.to_csv(reports / "model_comparison.csv", index=False)
    outcome.threshold_sweep.to_csv(reports / "threshold_sweep.csv", index=False)
    figures.threshold_economics(
        outcome.threshold_sweep,
        threshold,
        cfg.business.currency,
        cfg.paths.figures_dir,
        band=indifference_band(outcome.threshold_sweep, cfg.business),
    )
    if args.report:
        from churnsense.evaluation.report import write_comparison_report

        validation_proba = outcome.model.predict_proba(splits.X_val)[:, 1]
        write_comparison_report(
            outcome,
            cfg,
            ablation=feature_ablation(
                df.loc[splits.X_train.index], splits.y_train, outcome.selected, cfg
            ),
            segments=segment_table(
                splits.y_val,
                validation_proba,
                df.loc[splits.X_val.index],
                VALIDATION_SEGMENTS,
                threshold,
            ),
            sensitivity=threshold_sensitivity(
                splits.y_val, validation_proba, splits.X_val["MonthlyCharges"], cfg.business
            ),
        )

    print(comparison.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"\nSelected: {outcome.selected.display_name}  threshold {threshold:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
