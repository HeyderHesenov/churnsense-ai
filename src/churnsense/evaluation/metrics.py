"""Classification metrics and calibration assessment.

Accuracy is computed and reported, but it is never the headline: on this
dataset a model that predicts "no churn" for everyone is 73.5% accurate and
commercially worthless. Average precision (PR-AUC) is the selection metric,
because it answers the question the retention team actually asks - of the
customers we flag, how many really were going to leave?

Calibration is treated as a first-class metric rather than a diagnostic.
The business simulation multiplies a predicted probability by a customer's
value, so a score that merely *ranks* correctly is not enough; the number has
to mean what it says.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    roc_auc_score,
)


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    """One evaluation at one threshold. Plain floats, so it serialises as-is."""

    threshold: float
    n: int
    positives: int
    tn: int
    fp: int
    fn: int
    tp: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    average_precision: float
    brier: float

    @property
    def flagged(self) -> int:
        """Customers the model would send to the retention team."""
        return self.tp + self.fp

    @property
    def confusion(self) -> np.ndarray:
        return np.array([[self.tn, self.fp], [self.fn, self.tp]])

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _validated(y_true: ArrayLike, y_proba: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y_true).ravel()
    p = np.asarray(y_proba, dtype=float).ravel()

    if y.shape[0] != p.shape[0]:
        raise ValueError(
            f"y_true and y_proba must have the same length, got {y.shape[0]} and {p.shape[0]}"
        )
    if y.size == 0:
        raise ValueError("cannot evaluate an empty set of predictions")
    if not np.isfinite(p).all() or p.min() < 0.0 or p.max() > 1.0:
        raise ValueError("y_proba must contain finite probabilities in [0, 1]")
    if len(np.unique(y)) < 2:
        raise ValueError(
            "y_true must contain both classes; ROC-AUC and average precision are "
            "undefined otherwise"
        )
    return y.astype(int), p


def evaluate(
    y_true: ArrayLike, y_proba: ArrayLike, threshold: float = 0.5
) -> ClassificationMetrics:
    """Score predicted probabilities at one decision threshold.

    Precision is defined as 0.0 when nothing is flagged. scikit-learn warns and
    returns 0 for that case; defining it explicitly keeps the threshold sweep
    free of warnings and makes the convention visible to a reader.
    """
    y, p = _validated(y_true, y_proba)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must lie in [0, 1], got {threshold}")

    predicted = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return ClassificationMetrics(
        threshold=float(threshold),
        n=int(y.size),
        positives=int(y.sum()),
        tn=int(tn),
        fp=int(fp),
        fn=int(fn),
        tp=int(tp),
        accuracy=float((tn + tp) / y.size),
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        roc_auc=float(roc_auc_score(y, p)),
        average_precision=float(average_precision_score(y, p)),
        brier=float(brier_score_loss(y, p)),
    )


def reliability_table(y_true: ArrayLike, y_proba: ArrayLike, n_bins: int = 10) -> pd.DataFrame:
    """Observed churn rate against mean predicted probability, per bin.

    Empty bins are dropped rather than returned as NaN rows: a reliability
    curve should not plot a point where no customer was predicted.
    """
    y, p = _validated(y_true, y_proba)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # `right=True` with a clip keeps p == 0.0 in the first bin instead of bin -1.
    index = np.clip(np.digitize(p, edges[1:-1], right=True), 0, n_bins - 1)

    frame = pd.DataFrame({"bin": index, "y": y, "p": p})
    table = (
        frame.groupby("bin", observed=True)
        .agg(mean_predicted=("p", "mean"), observed_rate=("y", "mean"), count=("y", "size"))
        .reset_index(drop=True)
    )
    table["gap"] = table["observed_rate"] - table["mean_predicted"]
    return table


def expected_calibration_error(y_true: ArrayLike, y_proba: ArrayLike, n_bins: int = 10) -> float:
    """Weighted mean absolute gap between predicted and observed rates.

    0 is perfect. Reported alongside the Brier score because Brier mixes
    calibration and discrimination into one number, while ECE isolates the
    part the business simulation actually depends on.
    """
    table = reliability_table(y_true, y_proba, n_bins)
    weights = table["count"] / table["count"].sum()
    return float((weights * table["gap"].abs()).sum())
