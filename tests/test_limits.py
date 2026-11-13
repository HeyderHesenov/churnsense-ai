"""Per-path body limits, the per-endpoint budgets, and the per-client in-flight cap.

The public demo has no authentication: these keep one client from monopolising
the server - with large bodies on endpoints that never need them, with loops of
cheap-but-not-free requests, or with many slow requests held open at once.
Everything runs on the middleware itself or the app with no model loaded.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from churnsense.config import DEFAULT_RATE_LIMITS, RateLimit

LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)


def _scope(path: str, *, method: str = "POST", length: int | None = None) -> dict:
    headers = [] if length is None else [(b"content-length", str(length).encode())]
    return {"type": "http", "method": method, "path": path, "headers": headers, "client": LOOPBACK}


def _run(middleware, scope: dict, body: bytes = b"") -> tuple[int | None, bool]:
    """(status sent by the middleware or the app, whether the app was entered)."""
    entered, sent = [], []

    async def app(scope, receive, send):
        entered.append(True)
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    try:
        asyncio.run(middleware(app)(scope, receive, send))
    except StarletteHTTPException as exc:  # the streamed-body 413, raised for FastAPI to answer
        return exc.status_code, bool(entered)
    return sent[0]["status"], bool(entered)


# --- body limits by path -------------------------------------------------------------------


def test_only_the_three_upload_paths_get_the_upload_limit():
    from churnsense.api.middleware import BATCH_PATHS, SMALL_BODY_BYTES, BodySizeLimit

    assert {"/api/batch/score", "/api/predict/batch", "/predict/batch"} == BATCH_PATHS
    limit = BodySizeLimit(lambda *_: None, max_bytes=5 * 1_048_576)
    for path in BATCH_PATHS:
        assert limit.limit_for(path) == 5 * 1_048_576
    for path in (
        "/predict",
        "/api/predict",
        "/api/explain",
        "/predict/batch/",
        "/api/batch/score/",
        "/API/batch/score",
        "/api/batch/score%2F",
        "/anything",
    ):
        assert limit.limit_for(path) == SMALL_BODY_BYTES == 64 * 1024


def test_the_upload_limit_is_still_the_configured_five_mib_plus_the_envelope(cfg):
    from churnsense.api.middleware import MULTIPART_OVERHEAD_BYTES, BodySizeLimit

    assert cfg.api.max_upload_bytes == 5 * 1_048_576
    assert BodySizeLimit(lambda *_: None).limit_for("/api/batch/score") == (
        5 * 1_048_576 + MULTIPART_OVERHEAD_BYTES
    )


@pytest.mark.parametrize("path", ["/predict", "/api/predict", "/api/explain", "/predict/batch/"])
def test_a_declared_body_over_64_kib_is_refused_unread_off_the_upload_paths(path):
    from churnsense.api.middleware import SMALL_BODY_BYTES, BodySizeLimit

    status, entered = _run(BodySizeLimit, _scope(path, length=SMALL_BODY_BYTES + 1))
    assert (status, entered) == (413, False)
    status, entered = _run(BodySizeLimit, _scope(path, length=SMALL_BODY_BYTES))
    assert (status, entered) == (204, True)


def test_a_streamed_body_over_64_kib_is_cut_off_off_the_upload_paths():
    from churnsense.api.middleware import SMALL_BODY_BYTES, BodySizeLimit

    status, _ = _run(BodySizeLimit, _scope("/predict"), body=b"x" * (SMALL_BODY_BYTES + 1))
    assert status == 413


def test_an_upload_path_still_takes_a_body_of_megabytes():
    from churnsense.api.middleware import BodySizeLimit

    big = 2 * 1_048_576
    assert _run(BodySizeLimit, _scope("/api/batch/score", length=big))[0] == 204
    assert _run(BodySizeLimit, _scope("/api/batch/score"), body=b"x" * big)[0] == 204


@pytest.mark.parametrize(
    "content_type", ["application/json", "text/csv", "multipart/form-data; boundary=b"]
)
def test_the_content_type_does_not_change_the_limit(content_type):
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK) as client:
        response = client.post(
            "/predict",
            content=b"{" + b" " * (70 * 1024) + b"}",
            headers={"content-type": content_type},
        )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"
    assert "64 KB" in response.json()["detail"]


def test_the_app_refuses_a_large_json_body_for_predict():
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK) as client:
        response = client.post("/api/predict", json={"tenure": "1" * (80 * 1024)})
    assert response.status_code == 413


# --- budgets -------------------------------------------------------------------------------


def test_every_budget_has_a_default():
    from churnsense.api.middleware import RATE_LIMITED

    assert set(RATE_LIMITED.values()) == {name for name, _ in DEFAULT_RATE_LIMITS}


@pytest.mark.parametrize(
    ("method", "path", "budget"),
    [
        ("POST", "/predict", "predict"),
        ("POST", "/api/predict", "predict"),
        ("GET", "/api/customers", "query"),
        ("GET", "/api/segments", "query"),
        ("GET", "/api/threshold/sweep", "query"),
        ("GET", "/api/customers/export", "export"),
        ("HEAD", "/api/customers/export", "export"),
        ("POST", "/api/explain", "explain"),
        ("POST", "/predict/batch", "batch"),
    ],
)
def test_the_costly_endpoints_spend_their_budget(method, path, budget, monkeypatch):
    from churnsense.api import middleware

    monkeypatch.setattr(middleware, "RATE_LIMITS", {budget: RateLimit(burst=1, per_seconds=3600)})
    limiter = middleware.RateLimit(lambda *_: None)
    calls = []

    def app(scope, receive, send):
        async def run():
            calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return run()

    statuses = []
    for _ in range(2):
        sent = []

        async def send(message, sent=sent):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        limiter.app = app
        asyncio.run(limiter(_scope(path, method=method), receive, send))
        statuses.append(sent[0]["status"])
    assert statuses == [204, 429]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/api/health"),
        ("GET", "/api/ready"),
        ("GET", "/api/overview"),
        ("GET", "/assets/index-abc.js"),
        ("GET", "/explorer"),
    ],
)
def test_health_pages_assets_and_memoised_reads_are_never_budgeted(method, path, monkeypatch):
    from churnsense.api import middleware

    tight = RateLimit(burst=1, per_seconds=3600)
    monkeypatch.setattr(middleware, "RATE_LIMITS", {n: tight for n, _ in DEFAULT_RATE_LIMITS})
    assert middleware.RATE_LIMITED.get((method, path)) is None


def test_the_budgets_are_spent_by_the_visitor_behind_the_trusted_proxy(monkeypatch, tmp_path):
    from churnsense.api import main as api_main
    from churnsense.api import middleware, state

    monkeypatch.setattr(middleware, "TRUSTED_PROXIES", ("172.30.10.2",))
    monkeypatch.setattr(middleware, "ALLOWED_HOSTS", ("demo.example.com",))
    monkeypatch.setattr(
        middleware, "RATE_LIMITS", {"predict": RateLimit(burst=1, per_seconds=3600)}
    )
    monkeypatch.setattr(state, "ARTIFACTS_DIR", tmp_path / "no_model")
    state.reset()
    with TestClient(
        api_main.app, base_url="http://demo.example.com", client=("172.30.10.2", 1)
    ) as client:

        def predict(visitor: str):
            return client.post(
                "/predict",
                json={},
                headers={"x-forwarded-for": visitor, "x-forwarded-proto": "https"},
            ).status_code

        assert predict("203.0.113.7") != 429
        assert predict("203.0.113.7") == 429
        assert predict("198.51.100.1, 203.0.113.7") == 429, (
            "a spoofed left entry is no fresh budget"
        )
        assert predict("198.51.100.20") != 429
    state.reset()


# --- in-flight cap -------------------------------------------------------------------------


def _held_app(release: asyncio.Event, entered: list):
    async def app(scope, receive, send):
        entered.append(scope["client"][0])
        await release.wait()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


def _in_flight(path: str, clients: list[str], cap: int, monkeypatch) -> list[int]:
    """Send one request per entry of ``clients`` at once, all held; return the statuses."""
    from churnsense.api import middleware

    monkeypatch.setattr(middleware, "MAX_CLIENT_REQUESTS", cap)

    async def scenario():
        release, entered = asyncio.Event(), []
        limiter = middleware.RateLimit(_held_app(release, entered))
        statuses: list[int] = []

        async def one(ip: str):
            sent = []

            async def send(message):
                sent.append(message)

            async def receive():
                return {"type": "http.request", "body": b""}

            scope = {
                "type": "http",
                "method": "GET",
                "path": path,
                "headers": [],
                "client": (ip, 1),
            }
            await limiter(scope, receive, send)
            return sent[0]["status"]

        tasks = [asyncio.create_task(one(ip)) for ip in clients]
        for _ in range(50):
            await asyncio.sleep(0)
        release.set()
        statuses = await asyncio.gather(*tasks)
        assert limiter._in_flight == {}, "every count is given back"
        return list(statuses)

    return asyncio.run(scenario())


def test_one_client_cannot_hold_more_than_the_cap(monkeypatch):
    statuses = _in_flight("/api/overview", ["203.0.113.7"] * 5, 3, monkeypatch)
    assert sorted(statuses) == [204, 204, 204, 429, 429]


def test_another_client_is_not_blocked_by_the_first(monkeypatch):
    statuses = _in_flight("/api/overview", ["203.0.113.7"] * 3 + ["198.51.100.1"], 3, monkeypatch)
    assert statuses == [204, 204, 204, 204]


def test_an_ipv6_client_is_capped_across_its_slash_64(monkeypatch):
    clients = ["2001:db8:1:2::1", "2001:db8:1:2::2", "2001:db8:1:2::3"]
    statuses = _in_flight("/api/overview", clients, 2, monkeypatch)
    assert sorted(statuses) == [204, 204, 429]


@pytest.mark.parametrize("path", ["/health", "/ready", "/api/ready", "/assets/a.js", "/explorer"])
def test_health_and_static_paths_are_not_counted(path, monkeypatch):
    statuses = _in_flight(path, ["203.0.113.7"] * 4, 1, monkeypatch)
    assert statuses == [204] * 4


def test_the_cap_refusal_is_an_immediate_429_with_retry_after(monkeypatch):
    from churnsense.api import middleware

    monkeypatch.setattr(middleware, "MAX_CLIENT_REQUESTS", 0)
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def app(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("entered")

    asyncio.run(middleware.RateLimit(app)(_scope("/predict"), receive, send))
    assert sent[0]["status"] == 429
    assert dict(sent[0]["headers"])[b"retry-after"] == b"1"
    assert b"too_many_in_flight" in sent[1]["body"]


def test_a_count_is_given_back_when_the_app_fails(monkeypatch):
    from churnsense.api import middleware

    async def failing(scope, receive, send):
        raise RuntimeError("boom")

    limiter = middleware.RateLimit(failing)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    with pytest.raises(RuntimeError):
        asyncio.run(limiter(_scope("/predict"), receive, send))
    assert limiter._in_flight == {}


def test_the_cap_is_well_below_the_servers_concurrency_limit():
    from pathlib import Path

    from churnsense.api import middleware

    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    limit = int(dockerfile.split('"--limit-concurrency", "')[1].split('"')[0])
    assert limit >= middleware.MAX_CLIENT_REQUESTS * 4, "one client must not hold a quarter"


def test_production_allows_each_client_twelve_requests_in_flight():
    """The production cap, pinned on its own: only the browser-test server
    (``scripts/e2e_server.py``, never imported here) gives its one shared address a cap per
    browser, and that only in its own process."""
    from churnsense.api import middleware

    assert middleware.MAX_CLIENT_REQUESTS == 12
