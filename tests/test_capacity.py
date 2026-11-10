"""The public service's bounds on batch scoring, and its liveness and readiness.

A deployed demo has no authentication, so a batch upload - up to 10,000 rows of
parsing, validation and scoring - is CPU any visitor can spend. Per-client
budgets (``RateLimit``) stop one visitor; ``state.batch_slot`` stops many at
once: past ``BATCH_SLOTS`` concurrent scorings a request is turned away at once
with 429, never queued. Every test runs on the synthetic model and book.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from churnsense.book import score_book
from churnsense.exceptions import DataError
from churnsense.models.predict import Predictor
from tests.conftest import SYNTHETIC_SHA256

LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)
BATCH_PATHS = ("/api/batch/score", "/api/predict/batch", "/predict/batch")


def _csv(frame: pd.DataFrame) -> dict:
    return {"file": ("customers.csv", frame.to_csv(index=False).encode(), "text/csv")}


def _free_slots() -> int:
    """How many batch slots are free right now (taken and given back)."""
    from churnsense.api import state

    taken = 0
    while state._batch_slots.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        state._batch_slots.release()
    return taken


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    from churnsense.api import spa

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>t</title>")
    monkeypatch.setattr(spa, "FRONTEND_DIST", dist)
    return dist


@pytest.fixture
def calls(monkeypatch, clean_frame):
    """A synthetic book loader that counts how often the book is built."""
    from churnsense.api import state

    count = {"n": 0}

    def synthetic_book(model):
        count["n"] += 1
        return score_book(clean_frame, SYNTHETIC_SHA256, model)

    monkeypatch.setattr(state, "BOOK_LOADER", synthetic_book)
    return count


def _app(monkeypatch, artifacts, **kwargs) -> TestClient:
    from churnsense.api import main as api_main
    from churnsense.api import state

    monkeypatch.setattr(state, "ARTIFACTS_DIR", artifacts)
    state.reset()
    return TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK, **kwargs)


@pytest.fixture
def scoring(monkeypatch, dashboard_predictor, calls, frontend):
    from churnsense.api import state

    with _app(monkeypatch, dashboard_predictor.source) as client:
        yield client
    state.reset()
    assert _free_slots() == state.BATCH_SLOTS, "a test left a batch slot taken"


# --- batch capacity ------------------------------------------------------------------------


def test_the_process_allows_a_small_fixed_number_of_batches():
    from churnsense.api import state

    assert 1 <= state.BATCH_SLOTS <= 4
    assert _free_slots() == state.BATCH_SLOTS


@pytest.mark.parametrize("path", BATCH_PATHS)
def test_a_batch_past_the_slots_is_turned_away_at_once(scoring, raw_frame, path):
    from churnsense.api import state

    for _ in range(state.BATCH_SLOTS):
        assert state._batch_slots.acquire(blocking=False)
    try:
        started = time.monotonic()
        refused = scoring.post(path, files=_csv(raw_frame.head(5)))
        assert time.monotonic() - started < 5, "a busy server answers at once, it does not queue"
        assert refused.status_code == 429
        assert refused.json()["error"] == "batch_busy"
        assert int(refused.headers["retry-after"]) >= 1
        assert refused.headers["content-security-policy"].startswith("default-src 'self'")
    finally:
        for _ in range(state.BATCH_SLOTS):
            state._batch_slots.release()
    assert scoring.post(path, files=_csv(raw_frame.head(5))).status_code == 200


def test_concurrent_batches_beyond_the_slots_are_refused_not_queued(
    scoring, raw_frame, monkeypatch
):
    """BATCH_SLOTS uploads score at once; one more, sent while they run, gets 429."""
    from churnsense.api import state

    scored = Predictor.predict_frame
    inside = threading.Semaphore(0)
    gate = threading.Event()

    def held(self, df, threshold=None):
        inside.release()
        assert gate.wait(30), "the test never opened the gate"
        return scored(self, df, threshold)

    monkeypatch.setattr(Predictor, "predict_frame", held)
    with ThreadPoolExecutor(max_workers=state.BATCH_SLOTS) as pool:
        running = [
            pool.submit(scoring.post, "/api/batch/score", files=_csv(raw_frame.head(5)))
            for _ in range(state.BATCH_SLOTS)
        ]
        try:
            for _ in range(state.BATCH_SLOTS):
                assert inside.acquire(timeout=30), "a batch never started scoring"
            extra = scoring.post("/predict/batch", files=_csv(raw_frame.head(5)))
        finally:
            gate.set()
        assert extra.status_code == 429
        assert extra.json()["error"] == "batch_busy"
        assert [f.result(timeout=60).status_code for f in running] == [200] * state.BATCH_SLOTS
    assert _free_slots() == state.BATCH_SLOTS


def test_a_refused_file_gives_its_slot_back(scoring):
    from churnsense.api import state

    for _ in range(state.BATCH_SLOTS + 2):
        response = scoring.post(
            "/api/batch/score", files={"file": ("x.csv", b"tenure\nnot a number\n", "text/csv")}
        )
        assert response.status_code == 422
    assert _free_slots() == state.BATCH_SLOTS


def test_an_internal_error_gives_its_slot_back(
    monkeypatch, dashboard_predictor, calls, frontend, raw_frame
):
    from churnsense.api import state

    def broken(self, df, threshold=None):
        raise RuntimeError("scoring failed")

    with _app(monkeypatch, dashboard_predictor.source, raise_server_exceptions=False) as client:
        monkeypatch.setattr(Predictor, "predict_frame", broken)
        for path in BATCH_PATHS * state.BATCH_SLOTS:
            response = client.post(path, files=_csv(raw_frame.head(3)))
            assert response.status_code == 500
            assert response.json()["error"] == "internal_error"
    state.reset()
    assert _free_slots() == state.BATCH_SLOTS


def test_a_scored_batch_gives_its_slot_back_and_scores_as_before(scoring, raw_frame):
    from churnsense.api import state

    for _ in range(state.BATCH_SLOTS + 2):
        assert scoring.post("/api/batch/score", files=_csv(raw_frame.head(5))).status_code == 200
    assert _free_slots() == state.BATCH_SLOTS


def test_a_busy_refusal_is_logged_without_the_upload(scoring, raw_frame, caplog):
    from churnsense.api import state

    marker = "DEMO-UNIQUE-MARKER-0001"
    frame = raw_frame.head(3).copy()
    frame["customerID"] = marker
    for _ in range(state.BATCH_SLOTS):
        state._batch_slots.acquire(blocking=False)
    try:
        assert scoring.post("/api/batch/score", files=_csv(frame)).status_code == 429
    finally:
        for _ in range(state.BATCH_SLOTS):
            state._batch_slots.release()
    assert marker not in caplog.text


# --- liveness and readiness ----------------------------------------------------------------


def test_a_ready_service_says_so(scoring, calls):
    for path in ("/ready", "/api/ready"):
        response = scoring.get(path)
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "model": True,
            "dataset": True,
            "frontend": True,
        }
        assert response.headers["cache-control"] == "no-store"
    assert calls["n"] == 1, "the book is built once, at startup, and never by a readiness check"


def test_the_book_is_built_at_startup_not_by_the_first_visitor(
    monkeypatch, dashboard_predictor, calls, frontend
):
    from churnsense.api import state

    with _app(monkeypatch, dashboard_predictor.source):
        assert calls["n"] == 1
    state.reset()


def test_without_a_model_the_service_is_alive_but_not_ready(monkeypatch, tmp_path, calls, frontend):
    from churnsense.api import state

    with _app(monkeypatch, tmp_path / "no_model") as client:
        live = client.get("/health")
        ready = client.get("/ready")
    state.reset()
    assert live.status_code == 200 and live.json()["status"] == "degraded"
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "model": False,
        "dataset": False,
        "frontend": True,
    }
    assert calls["n"] == 0


def test_without_the_dataset_the_service_is_alive_but_not_ready(
    monkeypatch, dashboard_predictor, frontend
):
    from churnsense.api import state

    attempts = {"n": 0}

    def missing(model):
        attempts["n"] += 1
        raise DataError("no dataset here")

    monkeypatch.setattr(state, "BOOK_LOADER", missing)
    with _app(monkeypatch, dashboard_predictor.source) as client:
        assert attempts["n"] == 1, "startup tried to build the book"
        live = client.get("/health")
        ready = [client.get("/ready") for _ in range(3)]
    state.reset()
    assert live.status_code == 200 and live.json() == {
        "status": "ok",
        "model_loaded": True,
        "detail": None,
    }
    assert all(r.status_code == 503 for r in ready)
    assert ready[0].json() == {
        "status": "not_ready",
        "model": True,
        "dataset": False,
        "frontend": True,
    }
    assert attempts["n"] == 1, "a readiness check never retries the load"


def test_without_the_built_front_end_the_service_is_not_ready(
    monkeypatch, dashboard_predictor, calls, tmp_path
):
    from churnsense.api import spa, state

    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path / "not_built")
    with _app(monkeypatch, dashboard_predictor.source) as client:
        response = client.get("/ready")
    state.reset()
    assert response.status_code == 503
    assert response.json()["frontend"] is False and response.json()["dataset"] is True


def test_a_failure_while_warming_up_does_not_stop_the_service(
    monkeypatch, dashboard_predictor, frontend
):
    """An unexpected error building the book at startup is logged; the process still serves."""
    from churnsense.api import state

    def broken(model):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(state, "BOOK_LOADER", broken)
    with _app(monkeypatch, dashboard_predictor.source) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
    state.reset()


def test_readiness_names_no_path_or_reason(monkeypatch, tmp_path, calls, frontend):
    from churnsense.api import state

    with _app(monkeypatch, tmp_path / "no_model") as client:
        body = client.get("/ready").text
    state.reset()
    assert str(tmp_path) not in body and "make" not in body


def test_readiness_does_not_wait_for_a_rebuild_holding_the_lock(scoring):
    """A rebuild holds the dashboard lock for seconds; a health check answers meanwhile."""
    from churnsense.api import state

    with ThreadPoolExecutor(max_workers=1) as pool, state._dashboard_lock:
        answer = pool.submit(scoring.get, "/ready")
        assert answer.result(timeout=10).status_code == 200
