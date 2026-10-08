"""The single inference path.

Every consumer - the Streamlit dashboard, the FastAPI service and batch CSV
scoring - goes through this module. That is what makes "the dashboard and the
API agree" a structural property rather than a hope: there is one place where
a probability is produced, one place where the threshold is applied, and one
place where risk bands are assigned.

``predict_one`` is deliberately implemented in terms of ``predict_frame``
rather than beside it. A separate single-row code path is exactly how a form
and a batch job start disagreeing in the third decimal place.

If no usable artifact exists, loading raises. Nothing here ever substitutes a
default, a prior, or a placeholder - a wrong prediction presented confidently
is worse than an honest outage.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.evaluation.threshold import assign_risk_bands
from churnsense.exceptions import SchemaValidationError
from churnsense.logging_setup import get_logger
from churnsense.models.train import load_artifact

logger = get_logger(__name__)

PREDICTION_COLUMNS = ("churn_probability", "risk_band", "flagged")


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """One customer's scored outcome."""

    churn_probability: float
    risk_band: str
    flagged: bool
    threshold: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "churn_probability": self.churn_probability,
            "risk_band": self.risk_band,
            "flagged": self.flagged,
            "threshold": self.threshold,
        }


@dataclass(frozen=True, slots=True)
class Predictor:
    """A loaded pipeline plus the metadata needed to serve it consistently."""

    model: Pipeline
    meta: dict[str, Any]
    source: Path
    config: Config

    @property
    def epsilon(self) -> float:
        """How far from 0 and 1 a served probability is allowed to get.

        Isotonic calibration is a step function fitted to the training data, so
        its terminal bins carry the empirical rate of those bins exactly - on
        this dataset that is 0.000 for 210 customers and 1.000 for five. Those
        are artefacts of the method, not claims that an outcome is certain, and
        a probability of exactly 1 has infinite log-odds, which degenerates any
        expected-value or log-loss arithmetic downstream.

        The bound is the finest rate the calibration sample could express,
        1 / (2n), rather than a round number picked by hand. It is far smaller
        than any usable decision threshold, so it changes no decision - only
        the claim the number makes.
        """
        return 1.0 / (2 * int(self.meta["n_train"]))

    @property
    def threshold(self) -> float:
        return float(self.meta["threshold"])

    @property
    def feature_columns(self) -> list[str]:
        return list(self.meta["feature_columns"])

    @property
    def model_key(self) -> str:
        return str(self.meta["model_key"])

    def _aligned(self, df: pd.DataFrame) -> pd.DataFrame:
        """Select and order the trained feature columns, failing clearly if absent.

        Extra columns are dropped rather than rejected: callers legitimately
        carry ``customerID`` and the historical label alongside the features,
        and refusing those would make the obvious usage an error.
        """
        if df.empty:
            raise SchemaValidationError("input contains no rows")
        missing = [c for c in self.feature_columns if c not in df.columns]
        if missing:
            raise SchemaValidationError("input is missing required feature column(s)", missing)
        return df.loc[:, self.feature_columns]

    def predict_frame(self, df: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
        """Score a frame. Returns probability, risk band and the flag decision.

        ``threshold`` overrides the artifact's operating point for what-if
        analysis in the dashboard. It never changes the probabilities, only the
        decision drawn from them.
        """
        cut = self.threshold if threshold is None else float(threshold)
        raw = self.model.predict_proba(self._aligned(df))[:, 1]
        # Clipping is monotone, so the model's ranking is untouched.
        probabilities = np.clip(raw, self.epsilon, 1.0 - self.epsilon)

        bands = assign_risk_bands(probabilities, self.config)
        return pd.DataFrame(
            {
                "churn_probability": probabilities,
                "risk_band": bands.to_numpy(),
                "flagged": probabilities >= cut,
            },
            index=df.index,
        )

    def predict_one(
        self, record: dict[str, Any], threshold: float | None = None
    ) -> PredictionResult:
        """Score a single record, by the same path as a batch of one."""
        row = self.predict_frame(pd.DataFrame([record]), threshold).iloc[0]
        return PredictionResult(
            churn_probability=float(row["churn_probability"]),
            risk_band=str(row["risk_band"]),
            flagged=bool(row["flagged"]),
            threshold=self.threshold if threshold is None else float(threshold),
        )

    def unseen_categories(self, df: pd.DataFrame) -> dict[str, list[str]]:
        """Category values the model never saw in training.

        Reported rather than rejected: the encoder handles them, but a caller
        deserves to know its prediction rests on an unfamiliar input.
        """
        found: dict[str, list[str]] = {}
        for column, allowed in schema.ALLOWED_CATEGORIES.items():
            if column not in df.columns or column not in self.feature_columns:
                continue
            unexpected = sorted(set(df[column].astype(str)) - set(allowed))
            if unexpected:
                found[column] = unexpected
        return found


@lru_cache(maxsize=4)
def load_predictor(artifacts_dir: Path | str | None = None) -> Predictor:
    """Load the predictor once per artifact directory.

    Cached because Streamlit reruns the whole script on every interaction and
    FastAPI serves many requests per process; re-reading a pickle each time
    would dominate the latency of an otherwise instant model.
    """
    cfg = load_config()
    directory = Path(artifacts_dir) if artifacts_dir else cfg.paths.artifacts_dir
    model, meta = load_artifact(directory)
    logger.info("loaded predictor model=%s threshold=%.2f", meta["model_key"], meta["threshold"])
    return Predictor(model=model, meta=meta, source=directory, config=cfg)


def validate_upload(
    payload: bytes,
    cfg: Config | None = None,
    *,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Parse and validate an uploaded CSV before it reaches the model.

    Checks run cheapest-first: the size cap is enforced on the raw bytes, so a
    hostile 2 GB file is rejected without ever being parsed. Parser errors are
    caught and rewritten, because pandas error text can contain file paths and
    internal state that should not be shown to a user.
    """
    cfg = cfg or load_config()
    limit = max_rows if max_rows is not None else cfg.api.max_batch_rows

    if len(payload) > cfg.api.max_upload_bytes:
        raise SchemaValidationError(
            f"file is too large: {len(payload) / 1_048_576:.1f} MB exceeds the "
            f"{cfg.api.max_upload_bytes / 1_048_576:.0f} MB limit"
        )
    if not payload.strip():
        raise SchemaValidationError("file is empty")

    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, na_values=[])
    except Exception as exc:
        logger.warning("rejected upload: %s: %s", exc.__class__.__name__, exc)
        raise SchemaValidationError(
            "file could not be read as CSV. Expected a comma-separated file with a header row."
        ) from exc

    if frame.empty:
        raise SchemaValidationError("file contains a header but no data rows")
    if len(frame) > limit:
        raise SchemaValidationError(f"file has {len(frame):,} rows; the limit is {limit:,}")

    required = schema.model_input_columns(
        cfg.features.drop_columns, cfg.features.include_total_charges
    )
    # Column presence first: cleaning reads tenure, MonthlyCharges and
    # TotalCharges directly and would raise a KeyError rather than a useful
    # message if they were absent.
    if missing := [c for c in required if c not in frame.columns]:
        raise SchemaValidationError("file is missing required column(s)", missing)

    # Clean *before* validating values. The real IBM file stores a blank for
    # TotalCharges on zero-tenure customers, so validating the raw text would
    # reject the project's own dataset as containing non-numeric charges.
    from churnsense.data.loader import clean_frame

    return clean_frame(frame, required_columns=required)[0]
