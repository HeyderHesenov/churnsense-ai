"""FastAPI service for churn scoring.

Three principles shape this module.

**It never invents a prediction.** If the artifact is missing or unreadable,
every scoring endpoint answers 503 and says so. A placeholder probability
served with a 200 is worse than an outage, because nothing downstream can tell
the difference.

**It never leaks internals.** Unhandled exceptions become a generic 500; the
traceback goes to the log. Validation failures are returned in full, because
those describe the caller's input, not ours. No filesystem path appears in any
response body.

**It shares one prediction path with everything else.** Scoring goes through
``churnsense.models.predict``, the same module the dashboard and batch scoring
call, so the three cannot drift apart.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from churnsense.api.schemas import (
    BatchPredictionRow,
    BatchResponse,
    CustomerFeatures,
    ErrorResponse,
    HealthResponse,
    ModelInfoResponse,
    PredictionResponse,
)
from churnsense.config import load_config
from churnsense.exceptions import ModelNotAvailableError, SchemaValidationError
from churnsense.logging_setup import configure_logging
from churnsense.models.predict import Predictor, load_predictor, validate_upload

logger = logging.getLogger(__name__)

#: Module-level so tests can point the service at a temporary artifact
#: directory. Deliberately never taken from a request: unpickling is
#: equivalent to code execution, so the path must not be caller-controlled.
ARTIFACTS_DIR: Path | None = None

LIMITATIONS = [
    "Trained on the public IBM Telco sample, not on live customer data.",
    "Predicts association with past churn; it is not a causal model and cannot "
    "say what a retention offer would do to an individual.",
    "Monetary figures anywhere in this project are simulations under stated "
    "assumptions, not measured business results.",
    "No drift monitoring. Performance will decay as pricing and product mix change.",
]


@lru_cache(maxsize=1)
def get_predictor() -> Predictor:
    """The loaded model, cached for the process lifetime."""
    return load_predictor(ARTIFACTS_DIR)


def _require_predictor() -> Predictor:
    try:
        return get_predictor()
    except ModelNotAvailableError as exc:
        logger.error("scoring request refused: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="No trained model is available. Train one with `make train`.",
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model at startup so the first request is not the slow one."""
    configure_logging()
    try:
        predictor = get_predictor()
        logger.info(
            "service ready model=%s threshold=%.2f", predictor.model_key, predictor.threshold
        )
    except ModelNotAvailableError as exc:
        # Not fatal: /health must stay reachable so an operator can see why.
        logger.error("starting without a model: %s", exc)
    yield


app = FastAPI(
    title="ChurnSense AI",
    version="0.1.0",
    summary="Churn risk scoring for customer retention.",
    description=(
        "Decision-support scoring, not a decision system. Probabilities express "
        "similarity to customers who churned in a historical snapshot; they are "
        "not statements about an individual's intent and carry no causal claim."
    ),
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field-level problems; they describe the caller's input, not ours."""
    problems = [
        f"{'.'.join(str(p) for p in error['loc'][1:]) or 'body'}: {error['msg']}"
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="validation_error",
            detail="The request body did not match the expected schema.",
            problems=problems,
        ).model_dump(),
    )


@app.exception_handler(SchemaValidationError)
async def _schema_handler(request: Request, exc: SchemaValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="invalid_input", detail=str(exc), problems=exc.problems
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, return none of it."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_error",
            detail="The request could not be processed. The incident has been logged.",
        ).model_dump(),
    )


@app.get("/health", response_model=HealthResponse, tags=["service"])
def health() -> HealthResponse:
    """Liveness plus whether a model is actually loaded.

    Returns 200 either way on purpose: a health endpoint that fails when the
    model is missing tells an operator less than one that reports *why*.
    """
    try:
        get_predictor()
    except ModelNotAvailableError:
        return HealthResponse(
            status="degraded",
            model_loaded=False,
            detail="No trained model artifact found. Run `make train`.",
        )
    return HealthResponse(status="ok", model_loaded=True)


@app.get("/model-info", response_model=ModelInfoResponse, tags=["service"])
def model_info() -> ModelInfoResponse:
    """What is being served, and what it should not be trusted to do."""
    meta = _require_predictor().meta
    return ModelInfoResponse(
        model_key=meta["model_key"],
        display_name=meta["display_name"],
        trained_at=meta["trained_at"],
        threshold=meta["threshold"],
        threshold_source=meta["threshold_source"],
        calibrated=meta["calibrated"],
        calibration_method=meta.get("calibration_method"),
        feature_columns=meta["feature_columns"],
        dropped_columns=meta["dropped_columns"],
        n_train=meta["n_train"],
        n_validation=meta["n_validation"],
        validation_metrics=meta["validation_metrics"],
        test_metrics=meta.get("test_metrics"),
        sklearn_version=meta["sklearn_version"],
        limitations=LIMITATIONS,
    )


@app.post(
    "/predict",
    response_model=PredictionResponse,
    tags=["scoring"],
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def predict(customer: CustomerFeatures) -> PredictionResponse:
    """Score one customer."""
    predictor = _require_predictor()
    result = predictor.predict_one(customer.model_dump())
    return PredictionResponse(
        churn_probability=result.churn_probability,
        risk_band=result.risk_band,
        flagged=result.flagged,
        threshold=result.threshold,
        model_key=predictor.model_key,
    )


@app.post(
    "/predict/batch",
    response_model=BatchResponse,
    tags=["scoring"],
    responses={
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def predict_batch(
    file: UploadFile = File(..., description="CSV with the feature columns"),
) -> BatchResponse:
    """Score a CSV upload.

    At most one byte past the size limit is read, so an oversized upload is
    refused without being held in memory or handed to the CSV parser. A plain
    ``def`` rather than ``async def``: parsing and scoring are CPU-bound, so
    FastAPI runs them in its threadpool instead of on the event loop.
    """
    cfg = load_config()
    predictor = _require_predictor()

    payload = file.file.read(cfg.api.max_upload_bytes + 1)
    if len(payload) > cfg.api.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {cfg.api.max_upload_bytes / 1_048_576:.0f} MB limit.",
        )

    frame = validate_upload(payload, cfg)  # SchemaValidationError -> 422 via the handler
    scored = predictor.predict_frame(frame)

    return BatchResponse(
        n_scored=len(scored),
        threshold=predictor.threshold,
        model_key=predictor.model_key,
        predictions=[
            BatchPredictionRow(
                # The frame keeps read_csv's index, so this is the row's
                # position in the uploaded file, not in some filtered copy.
                row=int(index),
                churn_probability=float(row.churn_probability),
                risk_band=str(row.risk_band),
                flagged=bool(row.flagged),
                threshold=predictor.threshold,
                model_key=predictor.model_key,
            )
            for index, row in zip(scored.index, scored.itertuples(index=False), strict=True)
        ],
    )
