"""The probability clamp belongs to the model, not to one consumer of it.

Isotonic calibration saturates: on the real dataset its terminal bins assign
exactly 0.000 to 210 customers and exactly 1.000 to four. Nothing fitted on
4,225 rows can claim that, and a probability of exactly 1 has infinite
log-odds.

The clamp originally lived in ``Predictor``, which meant every *other*
consumer - the evaluation report, the published test metrics, SHAP - saw a
different function than the one served. The visible symptom was the
explainability page showing "0.9999" in its customer dropdown and "100.0%"
in the KPI tile beside it, for the same customer.

So the clamp is part of the fitted artifact. These tests pin that.
"""

from __future__ import annotations

import numpy as np
import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.models.calibration_clamp import ProbabilityClamp, epsilon_for


class _Saturating:
    """Returns exactly 0 and 1, like isotonic's terminal bins."""

    classes_ = np.array([0, 1])

    def predict_proba(self, X) -> np.ndarray:
        positives = np.linspace(0.0, 1.0, len(X))
        return np.column_stack([1.0 - positives, positives])


def test_the_clamp_bounds_both_ends():
    clamped = ProbabilityClamp(_Saturating(), epsilon=0.01)
    positives = clamped.predict_proba(np.zeros((11, 3)))[:, 1]
    assert positives.min() == pytest.approx(0.01)
    assert positives.max() == pytest.approx(0.99)


def test_the_two_columns_still_sum_to_one():
    """A malformed probability pair breaks any downstream sklearn metric."""
    proba = ProbabilityClamp(_Saturating(), epsilon=0.01).predict_proba(np.zeros((11, 3)))
    np.testing.assert_allclose(proba.sum(axis=1), 1.0)


def test_values_inside_the_bounds_pass_through_untouched():
    inner = _Saturating()
    clamped = ProbabilityClamp(inner, epsilon=0.01)
    raw = inner.predict_proba(np.zeros((11, 3)))[:, 1]
    served = clamped.predict_proba(np.zeros((11, 3)))[:, 1]
    interior = (raw > 0.01) & (raw < 0.99)
    np.testing.assert_allclose(served[interior], raw[interior])


def test_ordering_is_preserved():
    inner = _Saturating()
    raw = inner.predict_proba(np.zeros((21, 3)))[:, 1]
    served = ProbabilityClamp(inner, epsilon=0.01).predict_proba(np.zeros((21, 3)))[:, 1]
    assert (np.argsort(raw, kind="stable") == np.argsort(served, kind="stable")).all()


def test_epsilon_must_leave_room_for_a_probability():
    for bad in (-0.1, 0.5, 0.9):
        with pytest.raises(ValueError, match="epsilon"):
            ProbabilityClamp(_Saturating(), epsilon=bad)


def test_the_clamp_wraps_a_whole_fitted_pipeline(clean_frame, cfg: Config):
    """The shipped model is the clamp around the entire fitted pipeline, so it
    takes raw feature frames and never serves certainty."""
    from churnsense.models.registry import build_candidate

    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    fitted = build_candidate("logistic_regression", list(X.columns), seed=0).fit(
        splits.X_train, splits.y_train
    )
    wrapped = ProbabilityClamp(fitted, epsilon=epsilon_for(len(splits.y_train)))

    proba = wrapped.predict_proba(splits.X_test)[:, 1]
    assert (proba > 0.0).all()
    assert (proba < 1.0).all()
    np.testing.assert_array_equal(wrapped.classes_, fitted.classes_)


def test_epsilon_is_derived_from_the_calibration_sample():
    assert epsilon_for(4225) == pytest.approx(1.0 / (2 * 4225))
    assert epsilon_for(1) < 0.5, "must stay a valid probability bound even at n=1"
