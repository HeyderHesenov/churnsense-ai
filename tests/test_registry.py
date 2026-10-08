"""Tests for the model registry: the catalogue of candidates to compare."""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.models.registry import (
    MODEL_KEYS,
    build_candidate,
    candidate_grid,
    iter_candidates,
)


def test_registry_covers_the_four_required_families():
    assert set(MODEL_KEYS) == {
        "dummy",
        "logistic_regression",
        "random_forest",
        "hist_gradient_boosting",
    }


def test_dummy_is_present_as_the_floor():
    """Every comparison needs a do-nothing baseline to be measured against."""
    pipeline = build_candidate("dummy", ["tenure"], seed=0)
    assert isinstance(pipeline.named_steps["classifier"], DummyClassifier)


@pytest.mark.parametrize("key", ["logistic_regression", "random_forest", "hist_gradient_boosting"])
def test_every_candidate_is_a_pipeline_with_preprocessing(key: str):
    """Preprocessing must be inside the estimator, never a separate prior step."""
    pipeline = build_candidate(key, ["tenure", "Contract"], seed=0)
    assert isinstance(pipeline, Pipeline)
    assert list(pipeline.named_steps) == ["preprocess", "classifier"]


def _numeric_steps(pipeline: Pipeline) -> list[str]:
    """Names of the numeric sub-pipeline's steps, read off the unfitted spec."""
    transformers = pipeline.named_steps["preprocess"].transformers
    numeric = next(trans for name, trans, _ in transformers if name == "num")
    return list(numeric.named_steps)


@pytest.mark.parametrize(
    ("key", "expect_scaling"),
    [
        ("logistic_regression", True),
        ("random_forest", False),
        ("hist_gradient_boosting", False),
    ],
)
def test_only_the_linear_model_scales_its_numerics(key: str, expect_scaling: bool):
    """Scaling a tree fits parameters that change no prediction."""
    steps = _numeric_steps(build_candidate(key, ["tenure", "Contract"], seed=0))
    assert ("scale" in steps) is expect_scaling
    assert "impute" in steps, "the serving-time NaN safety net must be present for every model"


def test_unknown_key_is_rejected_with_the_valid_options():
    with pytest.raises(KeyError) as excinfo:
        build_candidate("xgboost_9000", ["tenure"], seed=0)
    assert "logistic_regression" in str(excinfo.value)


def test_candidates_are_seeded_reproducibly(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    columns = list(X.columns)
    first = build_candidate("random_forest", columns, seed=7).fit(X, y)
    second = build_candidate("random_forest", columns, seed=7).fit(X, y)
    np.testing.assert_allclose(first.predict_proba(X)[:, 1], second.predict_proba(X)[:, 1])


def test_different_seeds_are_actually_different(clean_frame, cfg: Config):
    """Guards against a seed that is accepted and then silently ignored."""
    X, y = features_and_target(clean_frame, cfg)
    columns = list(X.columns)
    a = build_candidate("random_forest", columns, seed=1).fit(X, y).predict_proba(X)[:, 1]
    b = build_candidate("random_forest", columns, seed=999).fit(X, y).predict_proba(X)[:, 1]
    assert not np.allclose(a, b)


def test_imbalance_is_handled_by_weighting_not_resampling(clean_frame, cfg: Config):
    """class_weight is the documented choice; no resampler may appear."""
    columns = list(features_and_target(clean_frame, cfg)[0].columns)
    for key in ("logistic_regression", "random_forest"):
        classifier = build_candidate(key, columns, seed=0).named_steps["classifier"]
        assert classifier.class_weight is not None
    for key in MODEL_KEYS:
        assert list(build_candidate(key, columns, seed=0).named_steps) == [
            "preprocess",
            "classifier",
        ]


def test_every_candidate_fits_and_predicts_probabilities(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    for key in MODEL_KEYS:
        proba = build_candidate(key, list(X.columns), seed=0).fit(X, y).predict_proba(X)[:, 1]
        assert proba.shape == (len(X),)
        assert ((proba >= 0) & (proba <= 1)).all(), key


def test_candidate_grid_prefixes_every_parameter_for_the_pipeline(cfg: Config):
    """A grid key without the `classifier__` prefix silently tunes nothing."""
    for key in MODEL_KEYS:
        grid = candidate_grid(key, cfg)
        assert all(name.startswith("classifier__") for name in grid), key


def test_candidate_grid_parameters_are_real_estimator_parameters(cfg: Config):
    for key in MODEL_KEYS:
        pipeline = build_candidate(key, ["tenure"], seed=0)
        valid = set(pipeline.get_params())
        assert set(candidate_grid(key, cfg)) <= valid, key


def test_dummy_has_an_empty_grid(cfg: Config):
    assert candidate_grid("dummy", cfg) == {}


def test_iter_candidates_yields_every_model_once(cfg: Config):
    keys = [key for key, _, _ in iter_candidates(["tenure"], cfg)]
    assert keys == list(MODEL_KEYS)


def test_candidates_are_clonable(cfg: Config):
    """GridSearchCV clones estimators; a non-clonable pipeline breaks tuning."""
    for key in MODEL_KEYS:
        assert clone(build_candidate(key, ["tenure"], seed=0)) is not None
