"""The ASGI layers every request passes before it reaches a route.

Order matters, and ``churnsense.api.main`` installs them outermost first:

1. ``SecurityHeaders`` - every response, refusals included, carries the
   browser-hardening headers and the Content-Security-Policy.
2. ``LoopbackOnly`` - the service has no authentication, so it answers only
   peers on this machine, whatever address uvicorn was told to bind - and,
   when deployed, the reverse proxies listed in ``api.trusted_proxies``, whose
   forwarded client address and scheme it takes in place of their own
   (``churnsense.proxies``). Everything below sees the visitor, not the proxy.
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

from churnsense import proxies
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

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if docs:
                    headers["Content-Security-Policy"] = DOCS_CONTENT_SECURITY_POLICY
                # Read when the response starts, not when the request arrives:
                # LoopbackOnly, inside this layer, sets the scheme a trusted
                # proxy vouched for.
                if scope.get("scheme") == "https":
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


#: Proxies to trust instead of the configured ``api.trusted_proxies``. Module-level,
#: read per request, so tests can set it - never taken from a request.
TRUSTED_PROXIES: Iterable[str] | None = None


class LoopbackOnly:
    """Answer only peers on this machine, and the reverse proxies configured to front it.

    The service has no authentication. ``make api`` binds it to 127.0.0.1, but
    a bind is a command-line flag; this makes the guarantee the code's own: a
    connection from any other address - or one whose peer is unknown - is
    refused with 403 before anything else runs.

    The peer is the socket's own address (``scope["client"]``), so uvicorn must
    run with ``--no-proxy-headers``: its own proxy handling would put a header's
    word in its place first. A loopback peer is answered as it is, and its
    forwarded headers are ignored. A peer in ``api.trusted_proxies`` (empty
    unless deployed) is answered as the visitor it forwards for: the client
    address and scheme of every layer below are the ones ``churnsense.proxies``
    reads from its ``X-Forwarded-For`` and ``X-Forwarded-Proto``, and a header
    it cannot read is a 400. The scope is updated in place, so the security
    headers above see the scheme as well.
    """

    def __init__(self, app: ASGIApp, trusted_proxies: Iterable[str] | None = None) -> None:
        if isinstance(trusted_proxies, str):
            raise TypeError("trusted_proxies must be a collection of addresses, not one string")
        self.app = app
        self._trusted = tuple(trusted_proxies) if trusted_proxies is not None else None
        self._log = _RefusalLog("request(s) from non-loopback peers")
        self._forwarded_log = _RefusalLog("request(s) with unreadable forwarded headers")

    @property
    def trusted_proxies(self) -> tuple[proxies.IPNetwork, ...]:
        if self._trusted is not None:
            entries = self._trusted
        elif TRUSTED_PROXIES is not None:
            entries = tuple(TRUSTED_PROXIES)
        else:
            entries = load_config().api.trusted_proxies
        return proxies.networks(entries)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else None
        if is_loopback(peer):
            await self.app(scope, receive, send)
            return
        # Only now is the config needed: a loopback peer is answered without it.
        if peer is not None and proxies.is_trusted(proxies.address(peer), self.trusted_proxies):
            headers = Headers(scope=scope)
            try:
                address, scheme = proxies.resolve(
                    peer,
                    headers.getlist("x-forwarded-for"),
                    headers.getlist("x-forwarded-proto"),
                    self.trusted_proxies,
                )
            except proxies.ForwardedHeaderError as exc:
                self._forwarded_log.record("header", str(exc))
                await _refuse(
                    scope,
                    receive,
                    send,
                    400,
                    "The forwarding proxy sent a header that could not be read.",
                    "invalid_forwarded_header",
                )
                return
            scope["client"] = (address, 0)
            if scheme is not None:
                secure = scheme == "https"
                if scope["type"] == "websocket":
                    scope["scheme"] = "wss" if secure else "ws"
                else:
                    scope["scheme"] = scheme
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


#: Host names to answer instead of the configured ``api.allowed_hosts``. Module-level,
#: read per request, so tests can set it - never taken from a request.
ALLOWED_HOSTS: Iterable[str] | None = None


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
        if ALLOWED_HOSTS is not None:
            return tuple(ALLOWED_HOSTS)
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


#: The only paths whose body may be an upload; every other body is ``SMALL_BODY_BYTES``.
BATCH_PATHS = frozenset({"/api/batch/score", "/api/predict/batch", "/predict/batch"})

#: Which (method, path) spends from which budget. A legacy path shares its
#: budget with its /api twin, so the alias is not a way around it; a HEAD is
#: budgeted as the GET it runs (Starlette answers HEAD with the GET route).
#:
#: * explain, batch: SHAP and upload scoring, seconds of CPU each.
#: * predict: one record, cheap, but a loop of them is still CPU.
#: * query: the customer list, segment tables and the threshold sweep filter,
#:   sort or recompute over the book on every new parameter set.
#: * export: every matching row as CSV.
#: The memoised reads (overview, performance, examples, one customer, the
#: global explanation) and health checks are not budgeted.
RATE_LIMITED: dict[tuple[str, str], str] = {
    ("POST", "/api/explain"): "explain",
    **{("POST", path): "batch" for path in BATCH_PATHS},
    ("POST", "/predict"): "predict",
    ("POST", "/api/predict"): "predict",
    ("GET", "/api/customers"): "query",
    ("GET", "/api/segments"): "query",
    ("GET", "/api/threshold/sweep"): "query",
    ("GET", "/api/customers/export"): "export",
}

#: Requests one client (``client_key``) may have in flight at once on the paths
#: that do work. Counted from the moment its headers arrive, so a slow body
#: counts too: one client can hold at most this many of uvicorn's
#: --limit-concurrency slots, whatever its budgets. Past it, 429 at once.
MAX_CLIENT_REQUESTS = 12
#: Paths never counted or budgeted: health checks must always answer.
UNCOUNTED_PATHS = frozenset({"/health", "/ready", "/api/health", "/api/ready"})
#: Legacy paths outside /api that do work.
COUNTED_LEGACY_PATHS = frozenset({"/predict", "/predict/batch", "/model-info"})


def is_counted(path: str) -> bool:
    """Whether a request to ``path`` counts against its client's in-flight cap."""
    if path in UNCOUNTED_PATHS:
        return False
    return path.startswith("/api/") or path in COUNTED_LEGACY_PATHS


#: Budgets to use instead of the configured ``api.rate_limits``; an empty
#: mapping turns throttling off. Module-level, read per request, so tests can
#: set it - never taken from a request.
RATE_LIMITS: Mapping[str, RateLimitRule] | None = None

#: Distinct clients remembered at once. The service answers loopback peers
#: only, so this is a ceiling on memory, not a working size.
MAX_TRACKED_CLIENTS = 1024


def client_key(address: str) -> str:
    """Whose budget a request spends: its address, or for IPv6 its /64.

    One IPv6 subscriber holds a whole /64 and can pick any address in it, so
    keying by the address would hand them a fresh budget per address. Loopback
    and anything that is not an address are keyed as they are.
    """
    ip = proxies.address(address)
    if ip is None:
        return address
    if isinstance(ip, ipaddress.IPv6Address) and not ip.is_loopback:
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


class RateLimit:
    """Per-client token buckets for the endpoints that cost real CPU, and a
    per-client cap on requests in flight.

    A SHAP explanation costs about 0.3 s of CPU and a batch upload up to a
    few seconds, so an unthrottled loop - a stuck page, a script - could keep a
    core busy indefinitely. Each (budget, client) pair holds a bucket of
    ``burst`` tokens that refills one token every ``per_seconds``; a request
    with no token left gets 429 and a ``Retry-After`` saying when one will be.

    Budgets bound how often; ``MAX_CLIENT_REQUESTS`` bounds how many at once,
    so a client sending slow bodies cannot take every connection the server
    has. The count lives only while requests are in flight (bounded by the
    server's own concurrency limit) and is given back however a request ends.
    """

    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self._clock = clock
        self._buckets: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()
        self._in_flight: dict[str, int] = {}

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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = client_key(client[0]) if client else ""
        counted = is_counted(scope["path"])
        if counted:
            # No await between the check and the increment: one event loop, no race.
            if self._in_flight.get(key, 0) >= MAX_CLIENT_REQUESTS:
                await error_response(
                    429,
                    "Too many requests in flight from this client. Try again shortly.",
                    error="too_many_in_flight",
                    headers={"Retry-After": "1"},
                )(scope, receive, send)
                return
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
        try:
            method = "GET" if scope["method"] == "HEAD" else scope["method"]
            budget = RATE_LIMITED.get((method, scope["path"]))
            rule = self._limits().get(budget) if budget else None
            if rule is not None:
                wait = self._take(budget, key, rule)
                if wait:
                    await error_response(
                        429,
                        "Too many requests to this endpoint. Try again shortly.",
                        headers={"Retry-After": str(math.ceil(wait))},
                    )(scope, receive, send)
                    return
            await self.app(scope, receive, send)
        finally:
            if counted:
                left = self._in_flight[key] - 1
                if left:
                    self._in_flight[key] = left
                else:
                    del self._in_flight[key]


#: The body limit of every path but ``BATCH_PATHS``. The largest legitimate
#: one - an explanation request of one customer record or eight IDs - is a
#: couple of KB, so a 5 MB JSON body is never needed and never buffered.
SMALL_BODY_BYTES = 64 * 1024


class BodySizeLimit:
    """Refuse request bodies over the path's limit before anything buffers them.

    Starlette spools a multipart upload to a temporary file, and reads a JSON
    body into memory, before the endpoint runs - so a size check inside the
    endpoint only happens after the disk or memory is already spent. This
    sits in front of both. A declared ``Content-Length`` over the limit is
    refused unread; a body without one (chunked) is counted as it streams and
    cut off the moment it crosses the limit.

    The limit depends on the path alone - never on the content type or the
    method - and only the exact ``BATCH_PATHS`` get the upload limit
    (``max_bytes``); any other path, an alias or a trailing-slash variant
    included, gets ``SMALL_BODY_BYTES``.

    Without an explicit ``max_bytes`` the upload limit is read from the config -
    the same cached value ``predict_batch`` reads, so the two can never
    disagree - and only when an upload path is asked for: a GET of /docs
    works, and importing this module works, without a config file.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        """The upload limit, for ``BATCH_PATHS``."""
        if self._max_bytes is not None:
            return self._max_bytes
        return load_config().api.max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    def limit_for(self, path: str) -> int:
        return self.max_bytes if path in BATCH_PATHS else SMALL_BODY_BYTES

    @staticmethod
    def _too_large(limit: int) -> str:
        if limit >= 1_048_576:
            return f"Request body exceeds the {limit / 1_048_576:.0f} MB limit."
        return f"Request body exceeds the {limit / 1024:.0f} KB limit."

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # isascii: str.isdigit also accepts digits such as '²' that int() rejects.
        # Past 18 digits a length exceeds any limit, and int() refuses a string
        # past 4,300 digits outright.
        path = scope["path"]
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isascii() and declared.isdigit():
            limit = self.limit_for(path)
            if len(declared) > 18 or int(declared) > limit:
                await error_response(413, self._too_large(limit))(scope, receive, send)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request" and (body := message.get("body", b"")):
                received += len(body)
                limit = self.limit_for(path)  # read only once a body arrives
                if received > limit:
                    # FastAPI re-raises an HTTPException met while reading the
                    # body, so this reaches the client as an ordinary 413.
                    raise StarletteHTTPException(status_code=413, detail=self._too_large(limit))
            return message

        await self.app(scope, counting_receive, send)
