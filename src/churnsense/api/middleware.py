"""The ASGI layers every request passes before it reaches a route.

Order matters, and ``churnsense.api.main`` installs them outermost first:

1. ``SecurityHeaders`` - every response, refusals included, carries the
   browser-hardening headers and the Content-Security-Policy.
2. ``LoopbackOnly`` - the service has no authentication, so it answers only
   peers on this machine, whatever address uvicorn was told to bind.
3. ``TrustedHosts`` - the Host allowlist (DNS rebinding), the Origin
   allowlist, and the same-origin rule for anything that changes state.
4. ``BodySizeLimit`` - a body over the upload limit is refused before
   anything buffers it.
5. ``RateLimit`` - per-client budgets for the endpoints that cost real CPU.
   Innermost, so a request refused by any check above spends no token.

Every refusal has the documented ``ErrorResponse`` shape.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from churnsense.api.schemas import ErrorResponse
from churnsense.config import RateLimit as RateLimitRule
from churnsense.config import load_config
from churnsense.hosts import is_allowed_host, is_allowed_origin, is_same_origin

logger = logging.getLogger(__name__)

#: Room for the multipart envelope (boundary lines, part headers) around a file
#: that is exactly at the upload limit, so the endpoint - not the body limit -
#: is what reports a file that is only slightly too large.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

#: Stable ``error`` codes for the HTTP errors this service can return. Written
#: out rather than derived from ``http.HTTPStatus``, whose phrase for 413
#: changed between Python 3.12 and 3.13 - a contract must not depend on that.
HTTP_ERROR_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limited",
    503: "model_unavailable",
}

#: The page may load code, styles, fonts and data from this origin only: no
#: inline script or style, no third-party request, no framing.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'"
)

#: FastAPI's interactive docs are built from a CDN bundle and an inline
#: bootstrap script, so they get a policy of their own that allows exactly
#: those sources - on these two pages and nowhere else.
DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com "
    "https://cdn.redoc.ly; connect-src 'self'; worker-src blob:; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

#: Sent on every response. Scores describe customers: no MIME sniffing, no
#: cache may keep them, and the page is locked down to its own origin.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
HSTS = "max-age=31536000"

#: Methods a browser may send cross-site without changing anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def error_response(
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


class _RefusalLog:
    """Logs the 1st, 10th, 100th... refusal: a hostile page can loop requests,
    and every one of them must not become a log line."""

    def __init__(self, what: str) -> None:
        self.what = what
        self.count = 0

    def record(self, reason: str, offending: object) -> None:
        self.count += 1
        if math.log10(self.count).is_integer():
            # %r: the value is the caller's text and must not forge log lines.
            logger.warning("refused %d %s; latest %s %r", self.count, self.what, reason, offending)


async def _refuse(
    scope: Scope, receive: Receive, send: Send, status: int, detail: str, error: str
) -> None:
    if scope["type"] == "websocket":
        await WebSocketClose(code=1008)(scope, receive, send)
    else:
        await error_response(status, detail, error=error)(scope, receive, send)


class SecurityHeaders:
    """Add ``SECURITY_HEADERS`` to every HTTP response that does not set them itself."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope["path"] in DOCS_PATHS
        https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if docs:
                    headers["Content-Security-Policy"] = DOCS_CONTENT_SECURITY_POLICY
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def is_loopback(address: str | None) -> bool:
    """Whether ``address`` is this machine: 127.0.0.0/8, ::1, or either mapped into IPv6."""
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


class LoopbackOnly:
    """Answer only peers on this machine.

    The service has no authentication. ``make api`` binds it to 127.0.0.1, but
    a bind is a command-line flag; this makes the guarantee the code's own: a
    connection from any other address - or one whose peer is unknown - is
    refused with 403 before anything else runs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._log = _RefusalLog("request(s) from non-loopback peers")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else None
        if is_loopback(peer):
            await self.app(scope, receive, send)
            return
        self._log.record("peer", peer)
        await _refuse(
            scope,
            receive,
            send,
            403,
            "This service has no authentication and answers only on this machine.",
            "loopback_only",
        )


class TrustedHosts:
    """Answer only requests whose Host - and Origin, if any - is allowed.

    The DNS-rebinding defence described in ``churnsense.hosts``. HTTP gets a
    400 in the ErrorResponse shape - Starlette's TrustedHostMiddleware would
    answer in plain text - and a WebSocket handshake is closed with 1008.

    A browser request whose ``Origin`` is another site is refused with 403. Its
    Host is a genuine ``localhost``, so the Host check alone lets it through,
    and a multipart POST needs no CORS preflight: any page the user visits
    could otherwise make this service score 10,000-row files in a loop.

    Two stricter rules close what an allowed host name alone leaves open, since
    every local program shares the name ``localhost``:

    * a state-changing request (any method but GET, HEAD, OPTIONS) that
      carries an ``Origin`` must come from this very site, port included;
    * an ``/api`` request whose ``Sec-Fetch-Site`` says another site sent it -
      "same-site" included, which is what another localhost port is - is
      refused, so a page elsewhere cannot trigger even a GET export.

    Without an explicit list the configured ``api.allowed_hosts`` is used;
    the service cannot start without a readable config, so there is no
    fallback to reason about.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] | None = None) -> None:
        if isinstance(allowed_hosts, str):
            raise TypeError("allowed_hosts must be a collection of host names, not one string")
        self.app = app
        self._allowed = tuple(allowed_hosts) if allowed_hosts is not None else None
        self._log = _RefusalLog("request(s) for unknown hosts or origins")

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        if self._allowed is not None:
            return self._allowed
        return load_config().api.allowed_hosts

    def _refusal(self, scope: Scope) -> tuple[int, str, str, str | None] | None:
        headers = Headers(scope=scope)
        host, origin = headers.get("host"), headers.get("origin")
        if not is_allowed_host(host, self.allowed_hosts):
            return 400, "Invalid host header", "invalid_host", host
        if not is_allowed_origin(origin, self.allowed_hosts):
            return 403, "Requests from other sites are not accepted", "forbidden_origin", origin
        if scope["type"] != "http":
            return None
        if (
            scope["method"] not in SAFE_METHODS
            and origin is not None
            and not is_same_origin(origin, host, scope.get("scheme", "http"))
        ):
            return 403, "Requests from other sites are not accepted", "cross_origin", origin
        fetch_site = headers.get("sec-fetch-site")
        if scope["path"].startswith("/api/") and fetch_site not in (None, "same-origin", "none"):
            return 403, "Requests from other sites are not accepted", "cross_site", fetch_site
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        refusal = self._refusal(scope)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, detail, error, offending = refusal
        self._log.record(error, offending)
        await _refuse(scope, receive, send, status, detail, error)


#: Which (method, path) spends from which budget. The legacy path shares the
#: batch budget with its /api twin, so the alias is not a way around it.
RATE_LIMITED: dict[tuple[str, str], str] = {
    ("POST", "/api/explain"): "explain",
    ("POST", "/api/batch/score"): "batch",
    ("POST", "/api/predict/batch"): "batch",
    ("POST", "/predict/batch"): "batch",
}

#: Budgets to use instead of the configured ``api.rate_limits``; an empty
#: mapping turns throttling off. Module-level, read per request, so tests can
#: set it - never taken from a request.
RATE_LIMITS: Mapping[str, RateLimitRule] | None = None

#: Distinct clients remembered at once. The service answers loopback peers
#: only, so this is a ceiling on memory, not a working size.
MAX_TRACKED_CLIENTS = 1024


class RateLimit:
    """Per-client token buckets for the endpoints that cost real CPU.

    A SHAP explanation costs about 0.3 s of CPU and a batch upload up to a
    few seconds, so an unthrottled loop - a stuck page, a script - could keep a
    core busy indefinitely. Each (budget, client) pair holds a bucket of
    ``burst`` tokens that refills one token every ``per_seconds``; a request
    with no token left gets 429 and a ``Retry-After`` saying when one will be.
    """

    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self._clock = clock
        self._buckets: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()

    @staticmethod
    def _limits() -> Mapping[str, RateLimitRule]:
        return RATE_LIMITS if RATE_LIMITS is not None else dict(load_config().api.rate_limits)

    def _take(self, budget: str, client: str, rule: RateLimitRule) -> float:
        """Spend one token; the seconds until one is available if there is none, else 0."""
        now = self._clock()
        key = (budget, client)
        tokens, last = self._buckets.pop(key, (float(rule.burst), now))
        tokens = min(float(rule.burst), tokens + (now - last) / rule.per_seconds)
        wait = 0.0 if tokens >= 1 else (1 - tokens) * rule.per_seconds
        self._buckets[key] = (tokens - 1 if not wait else tokens, now)
        while len(self._buckets) > MAX_TRACKED_CLIENTS:
            self._buckets.popitem(last=False)
        return wait

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        budget = (
            RATE_LIMITED.get((scope["method"], scope["path"])) if scope["type"] == "http" else None
        )
        rule = self._limits().get(budget) if budget else None
        if rule is not None:
            client = scope.get("client")
            wait = self._take(budget, client[0] if client else "", rule)
            if wait:
                await error_response(
                    429,
                    "Too many requests to this endpoint. Try again shortly.",
                    headers={"Retry-After": str(math.ceil(wait))},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
            await error_response(413, self._too_large())(scope, receive, send)
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
