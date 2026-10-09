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
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.evaluation.threshold import assign_risk_bands
from churnsense.exceptions import SchemaValidationError
from churnsense.models.calibration_clamp import ProbabilityClamp
from churnsense.models.train import load_artifact

logger = logging.getLogger(__name__)


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
    """A loaded model plus the metadata needed to serve it consistently."""

    model: ProbabilityClamp
    meta: dict[str, Any]
    source: Path
    config: Config

    @property
    def epsilon(self) -> float:
        """How far from 0 and 1 the served probabilities can get.

        Reported, not applied: the clamp lives inside the fitted artifact
        (``churnsense.models.calibration_clamp``) so that every consumer -
        this class, the evaluation report, SHAP - sees the same function.
        """
        return float(self.meta["probability_epsilon"])

    @property
    def threshold(self) -> float:
        return float(self.meta["threshold"])

    @property
    def feature_columns(self) -> list[str]:
        return list(self.meta["feature_columns"])

    @property
    def model_key(self) -> str:
        return str(self.meta["model_key"])

    def _validated(self, df: pd.DataFrame) -> pd.DataFrame:
        """Check ``df`` against the column contract and select the trained columns.

        Validation lives here, on the one path every consumer shares, so the
        dashboard form and library callers get the same rules the API enforces:
        a missing column, an out-of-range number or an unknown category is an
        error, never a silently encoded guess. Extra columns are dropped rather
        than rejected - callers legitimately carry ``customerID`` and the
        historical label alongside the features.

        Values are then coerced the way validation read them - categories as
        stripped text, numerics as numbers - so what was checked is what is
        scored. Without it a plain ``read_csv`` frame, whose ``SeniorCitizen``
        is an integer, passed validation and then crashed inside the encoder.
        """
        schema.validate_frame(df, columns=self.feature_columns)
        features = df.loc[:, self.feature_columns].copy()
        for column in features.columns:
            if column in schema.ALLOWED_CATEGORIES:
                features[column] = features[column].astype(str).str.strip()
            else:
                features[column] = pd.to_numeric(features[column])
        return features

    def predict_frame(self, df: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
        """Score a frame. Returns probability, risk band and the flag decision.

        ``threshold`` overrides the artifact's operating point for what-if
        analysis in the dashboard. It never changes the probabilities, only the
        decision drawn from them.
        """
        cut = self.threshold if threshold is None else float(threshold)
        # Already clamped by the artifact itself; see calibration_clamp.
        probabilities = self.model.predict_proba(self._validated(df))[:, 1]

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
    # Clean *before* validating values. The real IBM file stores a blank for
    # TotalCharges on zero-tenure customers, so validating the raw text would
    # reject the project's own dataset as containing non-numeric charges.
    # clean_frame handles each column only if present, so a missing one
    # surfaces from validate_frame as a SchemaValidationError (-> 422) rather
    # than as a KeyError (-> 500). No deduplication: every uploaded row is
    # scored and keeps its position in the file.
    from churnsense.data.loader import clean_frame

    return clean_frame(frame, required_columns=required, deduplicate=False)[0]
