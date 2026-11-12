"""FastAPI service for churn scoring, and the server of the dashboard.

Four principles shape this package.

**It never invents a prediction.** If the artifact is missing or unreadable,
every scoring endpoint answers 503 and says so. A placeholder probability
served with a 200 is worse than an outage, because nothing downstream can tell
the difference.

**It never leaks internals.** Unhandled exceptions become a generic 500; the
traceback goes to the log. Validation failures are returned in full, because
those describe the caller's input, not ours. No filesystem path appears in any
response body, and every error - ours or the framework's - has the one
``ErrorResponse`` shape the OpenAPI schema promises.

**It bounds what it reads and whom it answers.** The layers in
``churnsense.api.middleware`` refuse other machines, other sites, oversized
bodies and runaway loops before any route runs.

**It shares one prediction path with everything else.** Scoring goes through
``churnsense.models.predict``, the same module the Streamlit dashboard and
batch scoring call, so they cannot drift apart.

The scoring endpoints answer on their original paths and, like every
dashboard endpoint, under ``/api``; the built front end is served from ``/``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from churnsense.api import spa
from churnsense.api.dashboard import recorded_evaluation
from churnsense.api.dashboard import router as dashboard_router
from churnsense.api.middleware import (
    BodySizeLimit,
    LoopbackOnly,
    RateLimit,
    SecurityHeaders,
    TrustedHosts,
    error_response,
)
from churnsense.api.schemas import (
    ASSUMPTION_BOUNDS,
    BatchPredictionRow,
    BatchResponse,
    BusinessAssumptions,
    CustomerFeatures,
    DataSource,
    ErrorResponse,
    HealthResponse,
    HoldoutSummary,
    ModelInfoResponse,
    NumericBounds,
    PredictionResponse,
    Provenance,
    ReadinessResponse,
    RiskBand,
    UploadContract,
    UploadLimits,
)
from churnsense.api.state import (
    Dashboard,
    batch_slot,
    get_predictor,
    optional_dashboard,
    readiness,
    require_predictor,
    warm_up,
)
from churnsense.api.uploads import read_upload
from churnsense.config import load_config
from churnsense.exceptions import ModelNotAvailableError, SchemaValidationError
from churnsense.logging_setup import configure_logging
from churnsense.models.predict import upload_rules

logger = logging.getLogger(__name__)

LIMITATIONS = [
    "Trained on the public IBM Telco sample, not on live customer data.",
    "Predicts association with past churn; it is not a causal model and cannot "
    "say what a retention offer would do to an individual.",
    "Monetary figures anywhere in this project are simulations under stated "
    "assumptions, not measured business results.",
    "No drift monitoring. Performance will decay as pricing and product mix change.",
]

_SCORING_ERRORS: dict[int | str, dict] = {
    status: {"model": ErrorResponse} for status in (403, 413, 422, 503)
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model and build the book at startup, so the first visitor is not the slow one."""
    configure_logging()
    try:
        predictor = get_predictor()
    except ModelNotAvailableError as exc:
        # Not fatal: /health must stay reachable so an operator can see why.
        logger.error("starting without a model: %s", exc)
    else:
        logger.info(
            "model loaded model=%s threshold=%.2f", predictor.model_key, predictor.threshold
        )
        warm_up()
        logger.info("service ready=%s", all(readiness().values()))
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
# The last added is the outermost; churnsense.api.middleware explains the order.
app.add_middleware(RateLimit)
app.add_middleware(BodySizeLimit)
app.add_middleware(TrustedHosts)
app.add_middleware(LoopbackOnly)
app.add_middleware(SecurityHeaders)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """413, 503 and the router's own 404/405 in the documented ErrorResponse shape.

    An exception that names its own ``error`` code (a 503 for missing data
    rather than a missing model, say) keeps it. A browser navigating to a page
    address the app does not have gets the app shell instead (``spa.not_found_page``).
    """
    if (page := spa.not_found_page(request, exc)) is not None:
        return page
    return error_response(
        exc.status_code, str(exc.detail), error=getattr(exc, "error", None), headers=exc.headers
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field-level problems; they describe the caller's input, not ours."""
    errors = exc.errors()
    problems = [
        f"{'.'.join(str(p) for p in error['loc'][1:]) or 'body'}: {error['msg']}"
        for error in errors
    ]
    in_body = any(tuple(error["loc"][:1]) == ("body",) for error in errors)
    detail = (
        "The request body did not match the expected schema."
        if in_body
        else "The request parameters did not match the expected schema."
    )
    return error_response(422, detail, error="validation_error", problems=problems)


@app.exception_handler(SchemaValidationError)
async def _schema_handler(request: Request, exc: SchemaValidationError) -> JSONResponse:
    """A refusal of the whole file (empty, too wide, too long) has no per-column
    problems; its one reason is listed, so a page that shows problems shows it."""
    return error_response(422, str(exc), error="invalid_input", problems=exc.problems or [str(exc)])


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, return none of it."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return error_response(
        500,
        "The request could not be processed. The incident has been logged.",
        error="internal_error",
    )


# --- the scoring service: on its original paths and under /api ------------------------

service = APIRouter()


@service.get("/health", response_model=HealthResponse, tags=["service"])
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


@service.get(
    "/ready",
    response_model=ReadinessResponse,
    tags=["service"],
    responses={503: {"model": ReadinessResponse}},
)
def ready() -> JSONResponse:
    """Readiness: 200 only when the model, the book and the built front end are all in place.

    ``/health`` is liveness and stays 200 while the process runs; this is what a
    container health check or a proxy should poll. It reads only what is
    already in memory - nothing is loaded, read or scored - and names no reason
    or path: the log says why.
    """
    checks = {**readiness(), "frontend": spa.is_built()}
    ok = all(checks.values())
    body = ReadinessResponse(status="ready" if ok else "not_ready", **checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)


@service.get(
    "/model-info",
    response_model=ModelInfoResponse,
    tags=["service"],
    responses={503: {"model": ErrorResponse}},
)
def model_info(
    dashboard: Annotated[Dashboard | None, Depends(optional_dashboard)],
) -> ModelInfoResponse:
    """What is being served, where it came from, and what it should not be trusted to do."""
    predictor = require_predictor()
    meta, cfg = predictor.meta, predictor.config
    record = recorded_evaluation(meta.get("model_sha256"), meta["threshold"])
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
        test_metrics=None if record is None else record.metrics.model_dump(),
        sklearn_version=meta["sklearn_version"],
        limitations=LIMITATIONS,
        model_sha256=meta.get("model_sha256"),
        probability_epsilon=meta.get("probability_epsilon"),
        risk_bands=[RiskBand(name=b.name, min=b.min, max=b.max) for b in cfg.risk_bands],
        business_defaults=BusinessAssumptions.model_validate(cfg.business, from_attributes=True),
        limits=UploadLimits(
            max_batch_rows=cfg.api.max_batch_rows,
            max_upload_bytes=cfg.api.max_upload_bytes,
            max_upload_columns=cfg.api.max_upload_columns,
        ),
        numeric_bounds=NumericBounds.of_contract(),
        assumption_bounds=ASSUMPTION_BOUNDS,
        upload_contract=UploadContract.of_served(
            meta["feature_columns"], upload_rules(meta["feature_columns"])
        ),
        provenance=None
        if dashboard is None
        else Provenance(
            verified=dashboard.evidence.withheld is None,
            data_sha256=meta.get("data_sha256"),
            split_digest=meta.get("split_digest"),
            withheld_reason=dashboard.evidence.withheld,
        ),
        test_evaluation=None
        if record is None
        else HoldoutSummary(
            evaluated_at=record.evaluated_at,
            precision=record.metrics.precision,
            recall=record.metrics.recall,
        ),
        data_source=None
        if dashboard is None
        else DataSource.of(dashboard.synthetic, len(dashboard.book.frame)),
    )


@service.post(
    "/predict", response_model=PredictionResponse, tags=["scoring"], responses=_SCORING_ERRORS
)
def predict(customer: CustomerFeatures) -> PredictionResponse:
    """Score one customer."""
    predictor = require_predictor()
    result = predictor.predict_one(customer.model_dump())
    return PredictionResponse(
        churn_probability=result.churn_probability,
        risk_band=result.risk_band,
        flagged=result.flagged,
        threshold=result.threshold,
        model_key=predictor.model_key,
    )


@service.post(
    "/predict/batch",
    response_model=BatchResponse,
    tags=["scoring"],
    responses={429: {"model": ErrorResponse}, **_SCORING_ERRORS},
)
def predict_batch(
    file: Annotated[UploadFile, File(description="CSV with the feature columns")],
) -> BatchResponse:
    """Score a CSV upload.

    A plain ``def`` rather than ``async def``: parsing and scoring are
    CPU-bound, so FastAPI runs them in its threadpool instead of on the event
    loop. At most ``state.BATCH_SLOTS`` uploads are scored at once; another
    gets 429 at once.
    """
    predictor = require_predictor()
    with batch_slot():
        _, frame = read_upload(file, load_config(), predictor.feature_columns)
        scored = predictor.predict_frame(frame)

    return BatchResponse(
        n_scored=len(scored),
        threshold=predictor.threshold,
        model_key=predictor.model_key,
        predictions=[
            BatchPredictionRow(
                # The frame's index is the row's 0-based position among the
                # file's data rows (blank lines skipped), not in a filtered copy.
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


app.include_router(service)
app.include_router(service, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
# Last, so a page route can never shadow an API path.
spa.install(app)
