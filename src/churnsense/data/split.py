"""Stratified train / validation / test partitioning.

The split protocol is the spine of this project's leakage story:

* **train**      - fits the preprocessor and the estimators, and runs the
  cross-validation that tunes hyper-parameters and selects the model.
* **validation** - decides whether calibration is kept and where the decision
  threshold sits, and is reported as evidence beside the CV scores.
* **test**       - read only by the final evaluation,
  ``churnsense.evaluation.report``, which refuses to run without an explicit
  confirmation. Nothing selects on it. It is not untouched: its history is in
  docs/MODEL_CARD.md, "Evaluation history".

The shipped artifact is fitted on **train only**. Refitting on train+validation
would use ~33% more data, but the threshold and calibration were tuned on
validation, so a refit estimator would have seen the data its own operating
point was chosen on. With 4,225 training rows for 30-odd one-hot features that
trade is not worth an unexplainable threshold, and the choice is recorded in
docs/PROJECT_WALKTHROUGH.md.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import pandas as pd
from sklearn.model_selection import train_test_split

from churnsense.config import Config, load_config
from churnsense.exceptions import DataError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DataSplits:
    """The three partitions, kept together so they cannot be mixed up."""

    X_train: pd.DataFrame
    y_train: pd.Series
    X_val: pd.DataFrame
    y_val: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series

    def summary(self) -> dict[str, dict[str, float | int]]:
        """Per-partition size and positive rate, for the report and the logs."""
        return {
            name: {
                "n": len(y),
                "share": round(len(y) / self.n_total, 4),
                "positives": int(y.sum()),
                "positive_rate": round(float(y.mean()), 4),
            }
            for name, y in (
                ("train", self.y_train),
                ("validation", self.y_val),
                ("test", self.y_test),
            )
        }

    @property
    def n_total(self) -> int:
        return len(self.y_train) + len(self.y_val) + len(self.y_test)

    def digest(self) -> str:
        """Fingerprint of which rows went where.

        Recorded in the model metadata at training time and compared before
        anything reads the partitions again: the split is rebuilt from config on
        every run, and a changed seed or size would otherwise let training rows
        into "validation" or "test" without a word.
        """
        sha = hashlib.sha256()
        for part in (self.X_train, self.X_val, self.X_test):
            sha.update(pd.util.hash_pandas_object(part.index, index=False).to_numpy().tobytes())
            sha.update(b"|")
        return sha.hexdigest()


def check_split(splits: DataSplits, meta: dict) -> None:
    """Refuse partitions that differ from the ones the artifact was trained on."""
    recorded = meta.get("split_digest")
    if recorded is None:
        raise DataError(
            "the model metadata records no split digest, so the partitions cannot be "
            "matched to the ones it was trained on. Retrain with `make train`."
        )
    if recorded != splits.digest():
        raise DataError(
            "the train/validation/test split differs from the one the model was trained "
            "on (seed, split sizes or data changed). Retrain with `make train`."
        )


def make_splits(
    X: pd.DataFrame, y: pd.Series, cfg: Config | None = None, *, log: bool = True
) -> DataSplits:
    """Split into train/validation/test with a reproducible seed.

    ``validation_size`` is a fraction of the *whole* dataset, not of the
    post-test remainder, so the configured 0.2/0.2 really does produce 60/20/20.
    The log gives each partition's size, and the positive rate of training
    and validation only: nothing outside the final evaluation computes a
    figure from the test labels. ``log=False`` silences it, for callers that
    only need to know which row went where.
    """
    cfg = cfg or load_config()
    seed, sp = cfg.random_seed, cfg.split
    stratify_full = y if sp.stratify else None

    X_pool, X_test, y_pool, y_test = train_test_split(
        X, y, test_size=sp.test_size, random_state=seed, stratify=stratify_full
    )
    # Rescale so the validation slice is the requested share of the full data.
    val_share_of_pool = sp.validation_size / (1.0 - sp.test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_pool,
        y_pool,
        test_size=val_share_of_pool,
        random_state=seed,
        stratify=y_pool if sp.stratify else None,
    )

    splits = DataSplits(X_train, y_train, X_val, y_val, X_test, y_test)
    for name, stats in splits.summary().items() if log else ():
        rate = "" if name == "test" else f" positive_rate={stats['positive_rate']:.4f}"
        logger.info("split %-10s n=%-5d share=%.3f%s", name, stats["n"], stats["share"], rate)
    return splits
