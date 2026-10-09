"""Keep served probabilities away from 0 and 1.

Isotonic calibration is a step function. Its terminal bins carry the empirical
rate of those bins exactly, so on this dataset it assigns **0.000 to 210
customers and 1.000 to four** (counted over all 7,043). Neither is a claim a
sample of 4,225 training rows can support, and a probability of exactly 1 has
infinite log-odds, which degenerates any expected-value or log-loss
arithmetic downstream.

**This lives in the artifact, not in a consumer.** An earlier version clamped
inside ``Predictor``, which meant the evaluation report, the published test
metrics and SHAP all saw a different function than the one actually served.
The visible symptom was the explainability page printing "0.9999" in its
customer dropdown and "100.0%" in the KPI tile beside it, for one customer.
The clamp wraps the whole shipped model - pipeline, calibration and all - so
every consumer that calls ``model.predict_proba`` sees the same function by
construction, whatever is inside.

The bound is a property of the training run, so it is computed once at
persist time and travels inside the pickle.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin


def epsilon_for(n_calibration: int) -> float:
    """The finest rate a sample of ``n_calibration`` rows can express.

    ``1 / (2n)`` rather than a round number chosen by hand. (Each
    cross-validated calibrator sees about 4/5 of those rows, so the true
    resolution is slightly coarser; the bound is deliberately on the
    conservative side.) Capped below 0.5 so it remains a valid probability
    bound even for an absurdly small sample.
    """
    return min(1.0 / (2 * max(int(n_calibration), 1)), 0.49)


class ProbabilityClamp(BaseEstimator, ClassifierMixin):
    """A fitted classifier whose probabilities are clipped into ``[eps, 1-eps]``.

    Clipping is monotone, so the model's ranking - the thing it is actually
    good at - is untouched. Only the claim the number makes changes.
    """

    def __init__(self, estimator, epsilon: float) -> None:
        if not 0.0 < epsilon < 0.5:
            raise ValueError(f"epsilon must lie strictly in (0, 0.5), got {epsilon}")
        self.estimator = estimator
        self.epsilon = epsilon

    @property
    def classes_(self):
        return self.estimator.classes_

    def __sklearn_is_fitted__(self) -> bool:
        """Always fitted: this only ever wraps an already-fitted estimator.

        sklearn's `check_is_fitted` scans the instance `__dict__` for
        trailing-underscore attributes, and `classes_` here is a property, so
        without this any sklearn check of the wrapper raises `NotFittedError`
        on a perfectly usable model.
        """
        return True

    def fit(self, X, y=None):
        """Present for sklearn's interface; the wrapped estimator is already fitted."""
        self.estimator.fit(X, y)
        return self

    def predict_proba(self, X) -> np.ndarray:
        positives = np.clip(self.estimator.predict_proba(X)[:, 1], self.epsilon, 1.0 - self.epsilon)
        return np.column_stack([1.0 - positives, positives])

    def predict(self, X) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)
