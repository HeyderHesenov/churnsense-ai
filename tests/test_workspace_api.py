"""``POST /api/v2/imports/preview``: where it is served, what guards it, what it leaves behind.

The route is absent from the application unless a local deployment switches it
on, and never present in a public demo, whatever the environment says. Where
it is served, every V1 layer still stands in front of it, and a request writes
nothing anywhere: an audit hook records every file the process opens while one
runs. Every upload is synthetic (``tests/workspace_csv.py``); no model, book or
dataset is loaded.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
from fastapi.testclient import TestClient

from churnsense.config import PROJECT_ROOT, load_config
from churnsense.workspace import api as preview_api
from tests.workspace_csv import payload, row

PATH = preview_api.PREVIEW_PATH
LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)
ON = {preview_api.FLAG: "1"}
DEPLOY_CONFIG = PROJECT_ROOT / "configs" / "deploy.yaml"


def _file(body: bytes, name: str = "accounts.csv") -> dict:
    return {"file": (name, body, "text/csv")}


def _paths(app) -> set[str]:
    """Every path the application routes. FastAPI keeps an included router as one
    entry of ``app.routes``, so the paths are read from the schema built afresh."""
    app.openapi_schema = None
    return set(app.openapi()["paths"])


def _has_route(app) -> bool:
    return PATH in _paths(app)


@pytest.fixture
def app(monkeypatch) -> Iterator:
    """The service's own application with the preview installed, for this test only."""
    from churnsense.api import main as api_main
    from churnsense.api import middleware

    application = api_main.app
    routes = list(application.router.routes)
    monkeypatch.setattr(middleware, "LOCAL_UPLOAD_PATHS", middleware.LOCAL_UPLOAD_PATHS)
    monkeypatch.setattr(middleware, "LOCAL_QUERY_PATHS", middleware.LOCAL_QUERY_PATHS)
    application.openapi_schema = None
    assert preview_api.install(application, ON)
    yield application
    application.router.routes[:] = routes
    application.openapi_schema = None


@pytest.fixture
def client(app) -> TestClient:
    # No `with`: the lifespan, which loads the model, never runs.
    return TestClient(app, base_url=LOCAL, client=LOOPBACK)


# --- where it is served ----------------------------------------------------------------------


def test_the_preview_is_off_by_default():
    from churnsense.api import main as api_main
    from churnsense.api import middleware

    assert preview_api.FLAG not in os.environ
    assert not _has_route(api_main.app)
    assert frozenset() == middleware.LOCAL_UPLOAD_PATHS
    response = TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK).post(
        PATH, files=_file(payload([row()]))
    )
    assert response.status_code == 404


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "1 ", "on", "TRUE"])
def test_only_exactly_1_switches_it_on(value):
    assert not preview_api.preview_enabled({preview_api.FLAG: value}, load_config())
    assert preview_api.preview_enabled(ON, load_config())


def test_a_local_deployment_with_the_flag_serves_it(client, app):
    assert _has_route(app)
    assert client.post(PATH, files=_file(payload([row()]))).status_code == 200


def test_a_public_demo_never_installs_it_even_with_the_flag(caplog):
    from fastapi import FastAPI

    public = load_config(DEPLOY_CONFIG)
    assert public.deployment.public
    fresh = FastAPI()
    with caplog.at_level(logging.ERROR, logger="churnsense.workspace.api"):
        assert not preview_api.install(fresh, ON, public)
    assert not _has_route(fresh)
    assert "stays off" in caplog.text and "public_demo" in caplog.text


_SERVICE = textwrap.dedent(
    f"""
    import json
    from fastapi.testclient import TestClient
    from churnsense.api import main, middleware
    middleware.RATE_LIMITS = {{}}
    client = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5))
    body = b"account_external_id,as_of_date,status,mrr,currency\\nS,2026-10-31,active,1,USD\\n"
    answer = client.post({PATH!r}, files={{"file": ("a.csv", body, "text/csv")}})
    print(json.dumps({{
        "documented": {PATH!r} in main.app.openapi()["paths"],
        "v1_documented": "/api/batch/score" in main.app.openapi()["paths"],
        "status": answer.status_code,
        "local_uploads": sorted(middleware.LOCAL_UPLOAD_PATHS),
        "mode": main.load_config().deployment.mode,
    }}))
    """
)


def _service(config: Path, environ: dict[str, str]) -> tuple[dict, str]:
    """The real application, imported in its own process under ``config`` and ``environ``."""
    env = {k: v for k, v in os.environ.items() if k != preview_api.FLAG}
    env.update({"CHURNSENSE_CONFIG": str(config), **environ})
    done = subprocess.run(
        [sys.executable, "-c", _SERVICE],
        capture_output=True,
        text=True,
        env=env,
        cwd=PROJECT_ROOT,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1]), done.stderr


def test_the_public_demo_service_has_no_preview_even_with_the_flag_set():
    result, stderr = _service(DEPLOY_CONFIG, ON)
    assert result == {
        "documented": False,
        "v1_documented": True,
        "status": 404,
        "local_uploads": [],
        "mode": "public_demo",
    }
    assert "stays off" in stderr


def test_the_local_service_serves_the_preview_only_with_the_flag():
    """The control for the test above: the same harness sees the route where it is served."""
    local = PROJECT_ROOT / "configs" / "config.yaml"
    result, _ = _service(local, ON)
    assert result == {
        "documented": True,
        "v1_documented": True,
        "status": 200,
        "local_uploads": [PATH],
        "mode": "local",
    }
    result, _ = _service(local, {})
    assert (result["documented"], result["status"], result["local_uploads"]) == (False, 404, [])


def test_the_route_refuses_itself_outside_a_local_deployment(client, monkeypatch):
    """Mounted by hand into a public demo, the route still answers 404."""
    public = load_config(DEPLOY_CONFIG)
    monkeypatch.setattr(preview_api, "load_config", lambda: public)
    assert client.post(PATH, files=_file(payload([row()]))).status_code == 404


def test_it_is_documented_only_where_it_is_served(app):
    operation = app.openapi()["paths"][PATH]["post"]
    form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert set(form["properties"]) == {"file", "mapping", "status_mapping"}
    assert form["required"] == ["file"]


# --- the service's own layers still stand in front of it ---------------------------------------


def test_another_host_is_refused(client):
    answer = client.post(PATH, files=_file(payload([row()])), headers={"Host": "evil.example"})
    assert answer.status_code == 400


def test_another_site_is_refused(client):
    headers = {"Origin": "https://evil.example"}
    assert client.post(PATH, files=_file(payload([row()])), headers=headers).status_code == 403
    headers = {"Sec-Fetch-Site": "cross-site"}
    assert client.post(PATH, files=_file(payload([row()])), headers=headers).status_code == 403


def test_another_machine_is_refused(app):
    remote = TestClient(app, base_url=LOCAL, client=("203.0.113.9", 50000))
    assert remote.post(PATH, files=_file(payload([row()]))).status_code == 403


def test_it_takes_the_upload_limit_where_installed_and_64_kib_where_not(app, monkeypatch):
    from churnsense.api import middleware

    limit = middleware.BodySizeLimit(lambda *_: None, max_bytes=5 * 1_048_576)
    assert limit.limit_for(PATH) == 5 * 1_048_576
    assert limit.limit_for(PATH + "/") == middleware.SMALL_BODY_BYTES
    monkeypatch.setattr(middleware, "LOCAL_UPLOAD_PATHS", frozenset())
    assert limit.limit_for(PATH) == middleware.SMALL_BODY_BYTES


def test_a_body_over_the_upload_envelope_is_cut_off(client):
    from churnsense.api.middleware import MULTIPART_OVERHEAD_BYTES

    cfg = load_config()
    too_big = cfg.api.max_upload_bytes + MULTIPART_OVERHEAD_BYTES + 1
    answer = client.post(
        PATH,
        content=b"x" * too_big,
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert answer.status_code == 413


def test_a_file_over_the_upload_limit_inside_the_envelope_is_refused(client):
    cfg = load_config()
    answer = client.post(PATH, files=_file(b"a" * (cfg.api.max_upload_bytes + 1)))
    assert answer.status_code == 413
    assert answer.json()["error"] == "payload_too_large"


def test_the_row_and_column_limits_refuse_the_file(client):
    cfg = load_config()
    rows = [row(account_external_id=f"SYN-{i}") for i in range(cfg.api.max_batch_rows + 1)]
    answer = client.post(PATH, files=_file(payload(rows)))
    assert answer.status_code == 422
    assert "data rows" in answer.json()["detail"]
    wide = ",".join(f"c{i}" for i in range(cfg.api.max_upload_columns + 1)).encode() + b"\n"
    answer = client.post(PATH, files=_file(wide + b"x\n"))
    assert answer.status_code == 422
    assert "columns" in answer.json()["detail"]


def test_it_spends_the_batch_budget(client, monkeypatch):
    from churnsense.api import middleware
    from churnsense.config import RateLimit

    monkeypatch.setattr(middleware, "RATE_LIMITS", {"batch": RateLimit(burst=1, per_seconds=3600)})
    assert client.post(PATH, files=_file(payload([row()]))).status_code == 200
    answer = client.post(PATH, files=_file(payload([row()])))
    assert answer.status_code == 429 and "Retry-After" in answer.headers


def test_it_takes_a_batch_slot_and_is_turned_away_when_none_is_free(client):
    from churnsense.api import state

    taken = 0
    while state._batch_slots.acquire(blocking=False):
        taken += 1
    try:
        answer = client.post(PATH, files=_file(payload([row()])))
    finally:
        for _ in range(taken):
            state._batch_slots.release()
    assert answer.status_code == 429
    assert answer.json()["error"] == "batch_busy"
    assert client.post(PATH, files=_file(payload([row()]))).status_code == 200


def test_the_slot_is_given_back_however_the_preview_ends(client):
    from churnsense.api import state

    free = state.BATCH_SLOTS
    client.post(PATH, files=_file(b""))  # refused inside the slot
    client.post(PATH, files=_file(payload([row()])))
    taken = 0
    while state._batch_slots.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        state._batch_slots.release()
    assert taken == free


# --- the form ----------------------------------------------------------------------------------


def test_a_mapping_and_a_status_mapping_are_read_from_the_form(client):
    body = b"Ref,Snapshot,State,Monthly,Cur\nSYN-1,2026-10-31,unpaid,10.5,USD\n"
    mapping = {
        "Ref": "account_external_id",
        "Snapshot": "as_of_date",
        "State": "status",
        "Monthly": "mrr",
        "Cur": "currency",
    }
    data = {"mapping": json.dumps(mapping), "status_mapping": json.dumps({"unpaid": "past_due"})}
    answer = client.post(PATH, files=_file(body), data=data)
    assert answer.status_code == 200, answer.json()
    result = answer.json()
    assert result["mapping"]["source"] == "explicit"
    assert result["preview"][0]["status"] == "past_due"
    assert result["preview"][0]["mrr"] == "10.5"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"json": {"file": "x"}}, "multipart/form-data"),
        ({"data": {"mapping": "{}"}, "files": {"other": ("a.csv", b"x", "text/csv")}}, "form"),
        ({"data": {"mapping": "{}", "x": "1"}, "files": _file(b"a\n1\n")}, "form"),
        ({"data": {"file": "not a file"}}, "file"),
        ({"data": {"mapping": "x" * (65 * 1024)}, "files": _file(b"a\n1\n")}, "form"),
    ],
)
def test_a_malformed_form_is_refused_with_422(client, kwargs, reason):
    answer = client.post(PATH, **kwargs)
    assert answer.status_code == 422
    assert reason in answer.json()["detail"]
    assert set(answer.json()) == {"error", "detail", "problems"}


def test_two_files_are_refused(client):
    files = [("file", ("a.csv", b"a\n1\n", "text/csv")), ("file", ("b.csv", b"a\n1\n", "text/csv"))]
    assert client.post(PATH, files=files).status_code == 422


def test_a_mapping_error_is_the_structured_422(client):
    answer = client.post(PATH, files=_file(payload([row()])), data={"mapping": '{"a": "b"}'})
    assert answer.status_code == 422
    body = answer.json()
    assert body["error"] == "invalid_input"
    assert any("not a column of the file" in p for p in body["problems"])


def test_a_partly_invalid_file_is_a_preview_not_an_error(client):
    answer = client.post(PATH, files=_file(payload([row(), row(account_external_id="S", mrr="x")])))
    assert answer.status_code == 200
    assert answer.json()["status"] == "partially_valid"


# --- nothing is read from or written to disk ---------------------------------------------------

_OPENED: list[list[tuple[str, object, object]]] = []


def _audit(event: str, args: tuple) -> None:
    if _OPENED and event in {"open", "tempfile.mkstemp", "tempfile.mkdtemp", "os.mkdir"}:
        _OPENED[-1].append((event, *(*args, None, None)[:2]))


sys.addaudithook(_audit)


class _Opened:
    """Every file the process opens - in any thread - while the block runs."""

    def __enter__(self) -> list[tuple[str, object, object]]:
        self.events: list[tuple[str, object, object]] = []
        _OPENED.append(self.events)
        return self.events

    def __exit__(self, *exc) -> None:
        _OPENED.remove(self.events)


def _writes(events) -> list:
    flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND
    found = []
    for event, path, mode in events:
        if event != "open":
            found.append((event, path))
        elif (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(mode, int) and mode & flags
        ):
            found.append((path, mode))
    return found


def test_the_audit_hook_sees_a_spool_rolled_over_to_disk():
    """The control: without it, an empty list below would prove nothing."""
    with _Opened() as events, SpooledTemporaryFile(max_size=10) as spool:
        spool.write(b"x" * 100)
    assert _writes(events)


def test_a_preview_of_megabytes_writes_nothing_and_reads_no_artifact_or_dataset(client):
    from churnsense.api import state

    rows = [
        row(
            account_external_id=f"SYN-{i:06d}",
            account_name=f"Synthetic Co {i}",
            segment="S" * 64,
            industry="I" * 60,
        )
        for i in range(9_000)
    ]
    body = payload(rows)
    assert len(body) > 1_048_576  # past Starlette's own 1 MB spool
    state.get_predictor.cache_clear()
    with _Opened() as events:
        answer = client.post(PATH, files=_file(body))
    assert answer.status_code == 200
    assert answer.json()["summary"]["accepted"] == 9_000
    assert _writes(events) == []
    guarded = [PROJECT_ROOT / name for name in ("artifacts", "data", "reports", "governance")]
    touched = [
        path
        for _, path, _ in events
        if isinstance(path, str | bytes | os.PathLike)
        and any(Path(os.fsdecode(path)).resolve().is_relative_to(g) for g in guarded)
    ]
    assert touched == []
    assert state.get_predictor.cache_info().currsize == 0
    assert state._built is None


def test_a_refused_upload_writes_nothing_either(client):
    with _Opened() as events:
        assert client.post(PATH, files=_file(b"\xff\xfe" * 600_000)).status_code == 422
    assert _writes(events) == []


# --- no value reaches a log or a response it should not ---------------------------------------

SECRET = "SECRET-91c2"


def test_no_rejected_or_personal_value_reaches_the_response_or_any_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    rows = [
        row(mrr=SECRET, phone=SECRET, notes=SECRET),
        row(account_external_id=f"{SECRET}@synthetic.example"),
        row(account_external_id="SYN-2", status=SECRET, currency=SECRET),
    ]
    answer = client.post(PATH, files=_file(payload(rows)))
    assert answer.status_code == 200
    assert SECRET not in answer.text
    assert SECRET not in caplog.text
    body = f'account_external_id,mrr\n"{SECRET},1\n'.encode()
    answer = client.post(PATH, files=_file(body))
    assert answer.status_code == 422
    assert SECRET not in answer.text
    assert SECRET not in caplog.text


def test_the_upload_is_not_kept_by_the_module_after_the_request(client):
    from churnsense.workspace import api, mapping, preview, reader, validate

    before = {m.__name__: set(vars(m)) for m in (api, mapping, preview, reader, validate)}
    client.post(PATH, files=_file(payload([row()])))
    after = {m.__name__: set(vars(m)) for m in (api, mapping, preview, reader, validate)}
    assert before == after


# --- V1 is as it was ---------------------------------------------------------------------------


def test_v1_routes_and_upload_paths_are_unchanged(app):
    from churnsense.api import middleware, spa

    served = _paths(app)
    # The preview, and the Retention Workbench's reads that the same gate installs.
    assert {p for p in served if p.startswith(preview_api.PREFIX)} == {
        PATH,
        f"{preview_api.PREFIX}/portfolio/summary",
        f"{preview_api.PREFIX}/accounts",
        f"{preview_api.PREFIX}/accounts/{{account_id}}",
    }
    assert "/api/batch/score" in served and "/predict" in served
    assert {"/api/batch/score", "/api/predict/batch", "/predict/batch"} == middleware.BATCH_PATHS
    assert PATH not in spa.PAGES
    assert ("POST", PATH) not in middleware.RATE_LIMITED
