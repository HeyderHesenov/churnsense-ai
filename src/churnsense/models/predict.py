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

import csv
import io
import logging
from collections import Counter
from collections.abc import Iterable, Iterator
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

    def model_inputs(self, df: pd.DataFrame) -> pd.DataFrame:
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

        ``threshold`` overrides the artifact's operating point for a library
        caller's what-if analysis. It never changes the probabilities, only the
        decision drawn from them, and it must be a probability: NaN would flag
        nobody and a negative value everybody, both without a word.
        """
        cut = self.threshold if threshold is None else float(threshold)
        if not 0.0 <= cut <= 1.0:  # also false for NaN
            raise ValueError(f"threshold must lie in [0, 1], got {threshold!r}")
        # Already clamped by the artifact itself; see calibration_clamp.
        probabilities = self.model.predict_proba(self.model_inputs(df))[:, 1]

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


_UNREADABLE = "file could not be read as CSV. Expected a comma-separated file with a header row."
_CONTRACT_COLUMNS = frozenset(schema.RAW_COLUMNS)


def _has_content(record: list[str]) -> bool:
    """False for a blank or whitespace-only line, which is skipped, not read as a row."""
    return len(record) > 1 or bool(record and record[0].strip())


def _records(payload: bytes) -> Iterator[list[str]]:
    """The upload's non-blank CSV records, lazily.

    Byte-order marks are removed from every line before the csv module sees
    it. utf-8-sig drops one only at the very start of the stream; after a
    blank line it renamed the first column - or hid inside a quoted header
    name - and that column was dropped as unknown.
    """
    lines = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8", newline="")
    return filter(_has_content, csv.reader(line.replace("﻿", "") for line in lines))


def ignored_columns(payload: bytes) -> list[str]:
    """Header names in an upload that the data contract does not know.

    They are dropped on reading, so the dashboard uses this to say so rather
    than let a user's own identifier column vanish from the export unannounced.
    """
    header = next(_records(payload), [])
    return [name for name in header if name not in _CONTRACT_COLUMNS]


def _read_contract_columns(payload: bytes, cfg: Config, limit: int) -> pd.DataFrame:
    """Tokenise the upload once, with the csv module, keeping only contract columns.

    One tokenizer decides what the header and every row are. pandas never sees
    the untrusted header: its header handling is quadratic in the column
    count, and a 4.7 MB header of 560,000 empty columns held a CPU for more
    than five minutes. An earlier guard pre-checked widths with a second
    tokenizer, and every rule the two read differently - quoting, a BOM, which
    lines count as blank - was a way past it. The csv module is linear, and a
    column outside the raw contract is never built: the model could not use it.

    Every row must be as wide as the header or narrower (missing trailing
    fields read as empty). A wider row is refused wherever it is: pandas would
    have turned surplus fields in the first data row into an index and
    shifted every value in the file one column along, without a word.
    """
    records = _records(payload)
    header = next(records, None)
    if header is None:
        raise SchemaValidationError("file is empty")
    if len(header) == 1 and ";" in header[0]:
        raise SchemaValidationError(
            "file looks semicolon-separated; save it as comma-separated CSV (UTF-8)"
        )
    if len(header) > cfg.api.max_upload_columns:
        raise SchemaValidationError(
            f"file has {len(header):,} columns; the limit is {cfg.api.max_upload_columns:,}"
        )
    counts = Counter(header)
    if repeated := sorted(c for c in counts if counts[c] > 1 and c in _CONTRACT_COLUMNS):
        raise SchemaValidationError(f"column(s) appear more than once: {', '.join(repeated)}")

    keep = [(i, name) for i, name in enumerate(header) if name in _CONTRACT_COLUMNS]
    rows: list[list[str]] = []
    for number, record in enumerate(records, start=1):
        if len(record) > len(header):
            raise SchemaValidationError(
                f"data row {number:,} has {len(record):,} fields but the header has "
                f"{len(header):,}; check for an unquoted comma"
            )
        if number > limit:
            lines = max(payload.count(b"\n"), payload.count(b"\r"))
            raise SchemaValidationError(
                f"file has more than {limit:,} data rows (about {lines:,} lines); "
                f"split it into files of at most {limit:,} rows"
            )
        rows.append([record[i] if i < len(record) else "" for i, _ in keep])
    return pd.DataFrame(rows, columns=[name for _, name in keep], dtype=str)


def upload_rules(columns: Iterable[str]) -> list[str]:
    """What ``validate_upload`` holds a file to for a model scoring ``columns``,
    beyond the column list, the bounds and the categories.

    A value rule is given only under the condition ``schema._inconsistencies``
    checks it on, so a model scored without its columns is not described by it.
    The blank TotalCharges ``clean_frame`` reads as 0 depends on the file's
    tenure, scored or not.
    """
    scored = set(columns)
    labels = (schema.ID_COLUMN, schema.TARGET)
    unscored = [c for c in schema.RAW_COLUMNS if c not in scored and c not in labels]
    *named, last = ["the model's columns", schema.ID_COLUMN, *unscored, schema.TARGET]
    rules = [
        "Comma-separated UTF-8 with one header row; names match exactly, case included.",
        f"No column of the data contract appears twice: {', '.join(named)} and {last}. "
        "Any other column is ignored, however often it appears.",
        "No data row has more fields than the header; missing trailing fields read as blank.",
        "A file with any problem is not scored; the problems in its values are all listed.",
        "Every data row is scored in file order, duplicates included.",
        "Blank lines are skipped, and a refusal counts data rows from 1.",
        "A blank in a column the model scores is refused, never filled in"
        + (
            "; the one exception is a blank TotalCharges where the file's tenure is 0, read as 0."
            if "TotalCharges" in scored
            else "."
        ),
    ]
    if "InternetService" in scored and scored.intersection(schema.ADDON_COLUMNS):
        rules.append("Add-ons are 'No internet service' exactly when InternetService is 'No'.")
    if {"PhoneService", "MultipleLines"} <= scored:
        rules.append("MultipleLines is 'No phone service' exactly when PhoneService is 'No'.")
    if {"tenure", "TotalCharges"} <= scored:
        rules.append("TotalCharges is 0 exactly when tenure is 0.")
    return [
        *rules,
        f"{schema.ID_COLUMN} is optional and not used to score.",
        f"{schema.TARGET} is optional: each value Yes, No, 0 or 1, never blank; not used to score.",
    ]


def validate_upload(
    payload: bytes,
    cfg: Config | None = None,
    *,
    max_rows: int | None = None,
    columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Parse and validate an uploaded CSV before it reaches the model.

    Checks run cheapest-first: the size cap on the raw bytes, then - while the
    file is read, by ``_read_contract_columns`` - the column count, row widths
    and row count, then the values. Reader errors are caught and rewritten,
    because their text can carry internal state that should not reach a user.

    ``columns`` are the ones the file is scored on: pass the served model's
    ``feature_columns``, which ``upload_rules`` and every template describe.
    The config's are the default, and can drift from the artifact until the
    next training run.
    """
    cfg = cfg or load_config()
    limit = max_rows if max_rows is not None else cfg.api.max_batch_rows
    if limit < 1:
        raise ValueError(f"max_rows must be at least 1, got {limit}")

    if len(payload) > cfg.api.max_upload_bytes:
        raise SchemaValidationError(
            f"file is too large: {len(payload) / 1_048_576:.1f} MB exceeds the "
            f"{cfg.api.max_upload_bytes / 1_048_576:.0f} MB limit"
        )
    try:
        frame = _read_contract_columns(payload, cfg, limit)
    except (csv.Error, UnicodeDecodeError) as exc:
        logger.warning("rejected upload: %s: %s", exc.__class__.__name__, exc)
        if "field limit" in str(exc):
            raise SchemaValidationError(
                f"file has a field longer than {csv.field_size_limit():,} characters"
            ) from exc
        raise SchemaValidationError(_UNREADABLE) from exc

    # len(), not .empty: a file of only unknown columns has rows but no columns.
    if len(frame) == 0:
        raise SchemaValidationError("file contains a header but no data rows")

    required = (
        list(columns)
        if columns is not None
        else schema.model_input_columns(
            cfg.features.drop_columns, cfg.features.include_total_charges
        )
    )

    # Clean *before* validating values. The real IBM file stores a blank for
    # TotalCharges on zero-tenure customers, so validating the raw text would
    # reject the project's own dataset as containing non-numeric charges.
    # clean_frame handles each column only if present, so a missing one
    # surfaces from validate_frame as a SchemaValidationError (-> 422) rather
    # than as a KeyError (-> 500). No deduplication: every uploaded row is
    # scored and keeps its position in the file.
    from churnsense.data.loader import clean_frame

    return clean_frame(
        frame, required_columns=required, deduplicate=False, derive_total_charges=False
    )[0]
