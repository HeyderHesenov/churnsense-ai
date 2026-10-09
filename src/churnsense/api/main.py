"""FastAPI service for churn scoring.

Three principles shape this module.

**It never invents a prediction.** If the artifact is missing or unreadable,
every scoring endpoint answers 503 and says so. A placeholder probability
served with a 200 is worse than an outage, because nothing downstream can tell
the difference.

**It never leaks internals.** Unhandled exceptions become a generic 500; the
traceback goes to the log. Validation failures are returned in full, because
those describe the caller's input, not ours. No filesystem path appears in any
response body, and every error - ours or the framework's - has the one
``ErrorResponse`` shape the OpenAPI schema promises.

**It bounds what it reads.** A request body larger than the upload limit is
refused before anything buffers it, so the limit protects memory and disk
rather than only the CSV parser.

**It shares one prediction path with everything else.** Scoring goes through
``churnsense.models.predict``, the same module the dashboard and batch scoring
call, so the three cannot drift apart.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

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
from churnsense.hosts import is_allowed_host
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

#: Room for the multipart envelope (boundary lines, part headers) around a file
#: that is exactly at the upload limit, so the endpoint - not the body limit -
#: is what reports a file that is only slightly too large.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

#: Stable ``error`` codes for the HTTP errors this service can return. Written
#: out rather than derived from ``http.HTTPStatus``, whose phrase for 413
#: changed between Python 3.12 and 3.13 - a contract must not depend on that.
HTTP_ERROR_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    503: "model_unavailable",
}


#: Sent on every response. Scores describe customers: no MIME sniffing, and no
#: cache may keep them.
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


def _error_response(
    status_code: int,
    detail: str,
    *,
    error: str | None = None,
    problems: list[str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Every error the service sends, in the one documented ``ErrorResponse`` shape.

    The security headers are set here as well as by ``SecurityHeaders``: a 500
    is rendered by Starlette's outermost error middleware, outside every user
    middleware, so this is the only place that reaches it.
    """
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=error or HTTP_ERROR_CODES.get(status_code, "http_error"),
            detail=detail,
            problems=problems or [],
        ).model_dump(),
        headers={**SECURITY_HEADERS, **(headers or {})},
    )


class SecurityHeaders:
    """Add ``SECURITY_HEADERS`` to every HTTP response that does not set them itself."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class TrustedHosts:
    """Answer only requests whose Host header is allowed; refuse any other.

    The DNS-rebinding defence described in ``churnsense.hosts``. HTTP gets a
    400 in the ErrorResponse shape - Starlette's TrustedHostMiddleware would
    answer in plain text - and a WebSocket handshake is closed with 1008.
    Without an explicit list the configured ``api.allowed_hosts`` is used;
    the service cannot start without a readable config, so there is no
    fallback to reason about.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] | None = None) -> None:
        if isinstance(allowed_hosts, str):
            raise TypeError("allowed_hosts must be a collection of host names, not one string")
        self.app = app
        self._allowed = tuple(allowed_hosts) if allowed_hosts is not None else None
        self._refused = 0

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        if self._allowed is not None:
            return self._allowed
        return load_config().api.allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host = Headers(scope=scope).get("host")
        if is_allowed_host(host, self.allowed_hosts):
            await self.app(scope, receive, send)
            return

        # Logged on the 1st, 10th, 100th... refusal: a rebound page can loop
        # requests, and every one of them must not become a log line.
        self._refused += 1
        if math.log10(self._refused).is_integer():
            # %r: the header is the caller's text and must not forge log lines.
            logger.warning(
                "refused %d request(s) for unknown hosts; latest %r", self._refused, host
            )
        if scope["type"] == "websocket":
            await WebSocketClose(code=1008)(scope, receive, send)
        else:
            await _error_response(400, "Invalid host header", error="invalid_host")(
                scope, receive, send
            )


class BodySizeLimit:
    """Refuse request bodies over ``max_bytes`` before anything buffers them.

    Starlette spools a multipart upload to a temporary file, and reads a JSON
    body into memory, before the endpoint runs - so a size check inside the
    endpoint only happens after the disk or memory is already spent. This
    sits in front of both. A declared ``Content-Length`` over the limit is
    refused unread; a body without one (chunked) is counted as it streams and
    cut off the moment it crosses the limit.

    Without an explicit ``max_bytes`` the limit is read from the config - the
    same cached value ``predict_batch`` reads, so the two can never disagree -
    and only when a request actually carries a body: a GET of /docs works, and
    importing this module works, without a config file.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        if self._max_bytes is not None:
            return self._max_bytes
        return load_config().api.max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    def _too_large(self) -> str:
        return f"Request body exceeds the {self.max_bytes / 1_048_576:.0f} MB limit."

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # isascii: str.isdigit also accepts digits such as '²' that int() rejects.
        # Past 18 digits a length exceeds any limit, and int() refuses a string
        # past 4,300 digits outright.
        declared = Headers(scope=scope).get("content-length", "")
        if (
            declared.isascii()
            and declared.isdigit()
            and (len(declared) > 18 or int(declared) > self.max_bytes)
        ):
            await _error_response(413, self._too_large())(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request" and (body := message.get("body", b"")):
                received += len(body)
                if received > self.max_bytes:
                    # FastAPI re-raises an HTTPException met while reading the
                    # body, so this reaches the client as an ordinary 413.
                    raise StarletteHTTPException(status_code=413, detail=self._too_large())
            return message

        await self.app(scope, counting_receive, send)


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
# The last added is the outermost: security headers, then the host check (so a
# rebound request is refused before anything else runs), then the body limit.
app.add_middleware(BodySizeLimit)
app.add_middleware(TrustedHosts)
app.add_middleware(SecurityHeaders)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """413, 503 and the router's own 404/405 in the documented ErrorResponse shape."""
    return _error_response(exc.status_code, str(exc.detail), headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field-level problems; they describe the caller's input, not ours."""
    problems = [
        f"{'.'.join(str(p) for p in error['loc'][1:]) or 'body'}: {error['msg']}"
        for error in exc.errors()
    ]
    return _error_response(
        422,
        "The request body did not match the expected schema.",
        error="validation_error",
        problems=problems,
    )


@app.exception_handler(SchemaValidationError)
async def _schema_handler(request: Request, exc: SchemaValidationError) -> JSONResponse:
    return _error_response(422, str(exc), error="invalid_input", problems=exc.problems)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, return none of it."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return _error_response(
        500,
        "The request could not be processed. The incident has been logged.",
        error="internal_error",
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
    responses={
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
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

    ``BodySizeLimit`` has already refused any request body much larger than
    the limit, so the file here is at most a few KB over it; reading one byte
    past the limit is enough to tell, and the CSV parser never sees it. A
    plain ``def`` rather than ``async def``: parsing and scoring are CPU-bound,
    so FastAPI runs them in its threadpool instead of on the event loop.
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
