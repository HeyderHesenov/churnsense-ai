"""Integration tests for the FastAPI service.

The service is exercised through a real HTTP client against a real trained
artifact - no mocked model. A mocked prediction would test the plumbing and
miss the thing that matters most here: that the API returns exactly what the
dashboard and batch path return.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.models.predict import load_predictor
from churnsense.models.train import save_artifact, train_all

#: TestClient's default host, "testserver", is not an allowed host.
LOCAL = "http://localhost"


@pytest.fixture(scope="module")
def artifacts(clean_frame, cfg: Config, tmp_path_factory):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    directory = tmp_path_factory.mktemp("api_artifacts")
    save_artifact(outcome, directory, cfg)
    return directory


@pytest.fixture(scope="module")
def client(artifacts, monkeypatch_session):
    from churnsense.api import main as api_main

    monkeypatch_session.setattr(api_main, "ARTIFACTS_DIR", artifacts)
    with TestClient(api_main.app, base_url=LOCAL) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def payload(clean_frame, cfg: Config) -> dict:
    record = features_and_target(clean_frame, cfg)[0].iloc[0].to_dict()
    return {k: (int(v) if k == "tenure" else v) for k, v in record.items()}


def test_health_reports_a_loaded_model(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_model_info_describes_the_artifact(client):
    body = client.get("/model-info").json()
    assert body["model_key"]
    assert 0.0 <= body["threshold"] <= 1.0
    assert body["feature_columns"]
    assert "validation_metrics" in body


def test_model_info_does_not_leak_local_paths(client):
    assert "/Users/" not in client.get("/model-info").text


def test_predict_returns_the_documented_shape(client, payload):
    body = client.post("/predict", json=payload).json()
    assert set(body) >= {"churn_probability", "risk_band", "flagged", "threshold"}
    assert 0.0 <= body["churn_probability"] <= 1.0
    assert isinstance(body["flagged"], bool)


def test_api_agrees_with_the_library_to_the_last_digit(client, payload, artifacts):
    """The contract that makes dashboard, API and batch interchangeable."""
    api_probability = client.post("/predict", json=payload).json()["churn_probability"]
    local = load_predictor(artifacts).predict_one(payload)
    assert api_probability == pytest.approx(local.churn_probability, abs=1e-12)


def test_predict_rejects_a_missing_field(client, payload):
    body = dict(payload)
    del body["Contract"]
    response = client.post("/predict", json=body)
    assert response.status_code == 422
    assert "Contract" in response.text


def test_predict_rejects_an_out_of_range_number(client, payload):
    response = client.post("/predict", json={**payload, "tenure": -3})
    assert response.status_code == 422


def test_predict_rejects_an_unknown_category(client, payload):
    response = client.post("/predict", json={**payload, "Contract": "Lifetime"})
    assert response.status_code == 422


def test_validation_errors_do_not_leak_internals(client, payload):
    text = client.post("/predict", json={**payload, "tenure": "abc"}).text
    assert "Traceback" not in text
    assert "/Users/" not in text


def test_batch_scores_every_row(client, demo_csv):
    response = client.post(
        "/predict/batch",
        files={"file": ("customers.csv", demo_csv.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["n_scored"] == 120
    assert len(body["predictions"]) == 120
    assert all(0.0 <= p["churn_probability"] <= 1.0 for p in body["predictions"])


def test_batch_agrees_with_single_prediction(client, demo_csv, artifacts, cfg: Config):
    """Row-by-row equality between the batch endpoint and the single endpoint."""
    from churnsense.models.predict import validate_upload

    predictions = client.post(
        "/predict/batch", files={"file": ("c.csv", demo_csv.read_bytes(), "text/csv")}
    ).json()["predictions"]
    frame = validate_upload(demo_csv.read_bytes(), cfg)
    expected = load_predictor(artifacts).predict_frame(frame)

    for position in (0, 50, 119):
        assert predictions[position]["churn_probability"] == pytest.approx(
            expected["churn_probability"].iloc[position], abs=1e-12
        )


def test_batch_rows_point_at_the_uploaded_file_even_with_duplicates(client, demo_csv, artifacts):
    """Regression: a duplicate row was dropped and every later `row` shifted by one,
    so the response attributed probabilities to the wrong customers."""
    import pandas as pd

    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    upload = pd.concat([raw.head(1), raw.head(6)], ignore_index=True)  # rows 0 and 1 identical
    body = client.post(
        "/predict/batch",
        files={"file": ("dup.csv", upload.to_csv(index=False).encode(), "text/csv")},
    ).json()

    assert body["n_scored"] == len(upload)
    rows = {p["row"]: p["churn_probability"] for p in body["predictions"]}
    assert sorted(rows) == list(range(len(upload)))
    assert rows[0] == rows[1]

    predictor = load_predictor(artifacts)
    for position in (2, 6):
        record = payload_from(upload.iloc[position])
        expected = predictor.predict_one(record).churn_probability
        assert rows[position] == pytest.approx(expected, abs=1e-12)


def payload_from(row) -> dict:
    """A raw CSV row as the JSON body /predict expects."""
    from churnsense.data.loader import clean_frame

    cleaned, _ = clean_frame(row.to_frame().T.reset_index(drop=True), deduplicate=False)
    return cleaned.iloc[0].to_dict()


def test_batch_refuses_a_number_it_would_have_had_to_guess(client, demo_csv):
    """Regression: tenure 'abc' was imputed with the training median and scored."""
    import pandas as pd

    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    raw.loc[0, "tenure"] = "abc"
    response = client.post(
        "/predict/batch",
        files={"file": ("c.csv", raw.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 422
    assert "'tenure' has 1 missing or non-numeric value(s)" in response.json()["problems"]


def test_batch_rejects_a_non_csv_upload(client):
    response = client.post(
        "/predict/batch", files={"file": ("x.csv", b"\x00\x01 nonsense", "text/csv")}
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_batch_rejects_an_oversized_upload(client, cfg: Config):
    payload = b"a," * (cfg.api.max_upload_bytes // 2 + 10)
    response = client.post("/predict/batch", files={"file": ("big.csv", payload, "text/csv")})
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_service_reports_unavailable_rather_than_guessing(tmp_path, monkeypatch):
    """With no artifact the API must fail loudly, never return a default score."""
    from churnsense.api import main as api_main

    monkeypatch.setattr(api_main, "ARTIFACTS_DIR", tmp_path / "empty")
    api_main.get_predictor.cache_clear()
    with TestClient(api_main.app, base_url=LOCAL) as unloaded:
        assert unloaded.get("/health").json()["model_loaded"] is False
        assert unloaded.post("/predict", json={}).status_code in (422, 503)


def test_unavailable_model_is_a_503_in_the_documented_shape(tmp_path, monkeypatch, payload):
    from churnsense.api import main as api_main

    monkeypatch.setattr(api_main, "ARTIFACTS_DIR", tmp_path / "empty")
    api_main.get_predictor.cache_clear()
    with TestClient(api_main.app, base_url=LOCAL) as unloaded:
        response = unloaded.post("/predict", json=payload)
    assert response.status_code == 503
    assert response.json()["error"] == "model_unavailable"


# --- request size, error shape, headers --------------------------------------


def _chunked(total: int, head: bytes = b"", chunk: int = 64 * 1024):
    """A request body with no Content-Length: httpx sends a generator chunked."""
    yield head
    for start in range(0, total, chunk):
        yield b"a" * min(chunk, total - start)


def test_body_limit_refuses_a_declared_oversized_body_unread():
    """The declared length alone decides; nothing behind the limit is entered."""
    import asyncio

    from churnsense.api.main import BodySizeLimit

    entered, sent = [], []

    async def inner(scope, receive, send):
        entered.append(scope)

    async def receive():
        pytest.fail("an oversized body must not be read")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/predict/batch",
        "headers": [(b"content-length", str(10**9).encode())],
    }
    asyncio.run(BodySizeLimit(inner, max_bytes=1024)(scope, receive, send))

    assert entered == []
    assert sent[0]["status"] == 413


def _drive(middleware, scope: dict, messages: list[dict]) -> tuple[int, Exception | None]:
    """Run ``middleware`` over ``messages`` one at a time, as a real server streams them.

    The inner app drains the body the way Starlette's parsers do. Returns how
    many bytes it got and whatever the middleware raised.
    """
    import asyncio

    queue = list(messages)
    got = 0

    async def receive():
        return queue.pop(0)

    async def send(message):
        pass

    async def app(scope, receive, send):
        nonlocal got
        while True:
            message = await receive()
            got += len(message.get("body", b""))
            if not message.get("more_body"):
                return

    try:
        asyncio.run(middleware(app)(scope, receive, send))
    except Exception as exc:  # noqa: BLE001 - the raised exception is the result
        return got, exc
    return got, None


def _post_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": "POST", "path": "/predict", "headers": headers or []}


def test_body_limit_counts_a_streamed_body_across_messages():
    """TestClient sends a body as one message, so the running count is tested here."""
    from starlette.exceptions import HTTPException

    from churnsense.api.main import BodySizeLimit

    chunks = [{"type": "http.request", "body": b"a" * 1024, "more_body": True}] * 8
    chunks.append({"type": "http.request", "body": b"", "more_body": False})

    got, raised = _drive(lambda app: BodySizeLimit(app, max_bytes=4096), _post_scope(), chunks)

    assert isinstance(raised, HTTPException)
    assert raised.status_code == 413
    assert got == 4096, "the app must not see the message that crossed the limit"

    got, raised = _drive(lambda app: BodySizeLimit(app, max_bytes=8192), _post_scope(), chunks)
    assert raised is None
    assert got == 8192


def test_body_limit_refuses_an_absurdly_long_content_length():
    """Regression: past 4,300 digits int() raised and the request became a 500."""
    from churnsense.api.main import BodySizeLimit

    headers = [(b"content-length", b"9" * 5000)]
    body = [{"type": "http.request", "body": b"", "more_body": False}]
    got, raised = _drive(lambda app: BodySizeLimit(app, max_bytes=1024), _post_scope(headers), body)
    assert raised is None
    assert got == 0, "refused before the app read anything"


def test_a_request_without_a_body_does_not_need_the_config(monkeypatch):
    """GET /docs must not fail because a config file is missing."""
    from churnsense.api import main as api_main

    def missing_config():
        raise AssertionError("the body limit read the config for a request with no body")

    monkeypatch.setattr(api_main, "load_config", missing_config)
    scope = {"type": "http", "method": "GET", "path": "/docs", "headers": []}
    body = [{"type": "http.request", "body": b"", "more_body": False}]
    _, raised = _drive(api_main.BodySizeLimit, scope, body)
    assert raised is None


@pytest.mark.parametrize("declared", ["²", "1e9", "-1", " 12", ""])
def test_body_limit_ignores_a_content_length_it_cannot_read(declared: str):
    """Not a crash: the streamed count still applies. h11 rejects these first anyway."""
    from churnsense.api.main import BodySizeLimit

    headers = [(b"content-length", declared.encode("latin-1"))]
    body = [{"type": "http.request", "body": b"{}", "more_body": False}]
    got, raised = _drive(lambda app: BodySizeLimit(app, max_bytes=1024), _post_scope(headers), body)
    assert raised is None
    assert got == 2


def test_body_limit_defaults_to_the_configured_upload_limit(cfg: Config):
    from churnsense.api.main import MULTIPART_OVERHEAD_BYTES, BodySizeLimit

    limit = BodySizeLimit(lambda *_: None).max_bytes
    assert limit == cfg.api.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


def test_an_unhandled_error_is_generic_and_still_carries_the_headers():
    """A 500 is rendered outside every user middleware; the headers must come with it."""
    import asyncio

    from starlette.requests import Request

    from churnsense.api.main import _unhandled_handler

    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    response = asyncio.run(_unhandled_handler(request, RuntimeError("secret detail")))

    assert response.status_code == 500
    assert b"secret detail" not in response.body
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_openapi_declares_every_error_the_scoring_endpoints_return(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"413", "422", "503"} <= set(paths["/predict"]["post"]["responses"])
    assert {"413", "422", "503"} <= set(paths["/predict/batch"]["post"]["responses"])


def test_body_limit_cuts_off_a_chunked_upload(client, cfg: Config):
    """Without a Content-Length the limit still holds through FastAPI's form parsing.

    The 413 is raised from inside the multipart parser's read and must come
    back as an ordinary 413, not FastAPI's 400 "error parsing the body".
    TestClient delivers the body as a single message, so the running count
    across messages is tested directly, in the test above.
    """
    head = (
        b"--b\r\n"
        b'Content-Disposition: form-data; name="file"; filename="big.csv"\r\n'
        b"Content-Type: text/csv\r\n\r\n"
    )
    response = client.post(
        "/predict/batch",
        content=_chunked(2 * cfg.api.max_upload_bytes, head),
        headers={"content-type": "multipart/form-data; boundary=b"},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_body_limit_also_bounds_json(client, cfg: Config):
    response = client.post(
        "/predict",
        content=_chunked(2 * cfg.api.max_upload_bytes),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_http_errors_use_the_documented_error_shape(client):
    missing = client.get("/no-such-route")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"

    wrong_method = client.get("/predict")
    assert wrong_method.status_code == 405
    assert set(wrong_method.json()) == {"error", "detail", "problems"}
    assert "POST" in wrong_method.headers["allow"]


def test_responses_are_neither_sniffed_nor_cached(client, payload, cfg: Config):
    responses = [
        client.get("/health"),
        client.post("/predict", json=payload),
        client.get("/no-such-route"),
        client.post(
            "/predict",
            content=_chunked(2 * cfg.api.max_upload_bytes),
            headers={"content-type": "application/json"},
        ),
    ]
    for response in responses:
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_predict_rejects_non_finite_numbers(client, payload, value):
    """Python's json accepts NaN and Infinity; the field bounds must still refuse them."""
    import json

    body = json.dumps({**payload, "MonthlyCharges": value})
    response = client.post("/predict", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8000", "localhost.evil"])
def test_a_request_for_a_foreign_host_is_refused(client, host: str):
    """DNS rebinding: the browser calls 127.0.0.1 but names the attacker's host."""
    response = client.get("/health", headers={"host": host})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_host"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8001", "LOCALHOST"])
def test_the_loopback_names_are_answered(client, host: str):
    assert client.get("/health", headers={"host": host}).status_code == 200


def test_a_request_without_a_host_is_refused():
    from churnsense.api.main import TrustedHosts

    reached = []

    async def app(scope, receive, send):
        reached.append(scope)

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/health", "headers": []}
    import asyncio

    asyncio.run(TrustedHosts(app, ["localhost"])(scope, None, send))
    assert reached == []
    assert sent[0]["status"] == 400


def test_a_websocket_for_a_foreign_host_is_closed_unaccepted():
    import asyncio

    from churnsense.api.main import TrustedHosts

    reached, sent = [], []

    async def app(scope, receive, send):
        reached.append(scope)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", b"attacker.example")]}
    asyncio.run(TrustedHosts(app, ["localhost"])(scope, receive, send))
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": 1008, "reason": ""}]


def test_one_host_given_as_a_string_is_a_type_error():
    """Not a tuple of its letters, which would refuse every request."""
    from churnsense.api.main import TrustedHosts

    with pytest.raises(TypeError):
        TrustedHosts(lambda *_: None, "localhost")


def test_refusals_are_logged_sparsely(caplog):
    """A rebound page can loop requests; the log gets the 1st, 10th, 100th."""
    import asyncio

    from churnsense.api.main import TrustedHosts

    async def app(scope, receive, send):
        pass

    async def send(message):
        pass

    guard = TrustedHosts(app, ["localhost"])
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"host", b"x.example")]}
    with caplog.at_level("WARNING", logger="churnsense.api.main"):
        for _ in range(150):
            asyncio.run(guard(scope, None, send))
    assert [r.args[0] for r in caplog.records] == [1, 10, 100]


def test_openapi_schema_is_served(client):
    schema = client.get("/openapi.json").json()
    assert "/predict" in schema["paths"]
    assert "/health" in schema["paths"]


# --- the API's contract must not drift from the model's ---------------------


def test_api_category_vocabularies_match_the_training_contract():
    """The aliases in api/schemas.py are hand-written; this is what keeps them true.

    If a category is added to the training contract and not to the API, the
    service would reject input the model handles perfectly well - and the
    failure would surface as a confusing 422 in production, not here.
    """
    import typing

    from churnsense.api.schemas import CATEGORY_ALIASES
    from churnsense.config import load_config
    from churnsense.data import schema

    cfg = load_config()
    model_inputs = set(
        schema.model_input_columns(cfg.features.drop_columns, cfg.features.include_total_charges)
    )
    # Only the categoricals the model actually consumes: `gender` is in the raw
    # contract but is dropped as a protected attribute, so the API has no field
    # for it and must not grow one.
    expected = {c for c in schema.ALLOWED_CATEGORIES if c in model_inputs}

    assert set(CATEGORY_ALIASES) == expected
    for column, alias in CATEGORY_ALIASES.items():
        assert set(typing.get_args(alias)) == set(schema.ALLOWED_CATEGORIES[column]), column


def test_api_accepts_every_model_input_column():
    """Every feature the pipeline consumes must be a field on the request model."""
    from churnsense.api.schemas import CustomerFeatures
    from churnsense.config import load_config
    from churnsense.data import schema

    cfg = load_config()
    required = schema.model_input_columns(
        cfg.features.drop_columns, cfg.features.include_total_charges
    )
    assert set(required) == set(CustomerFeatures.model_fields)
