"""The e2e server's guards: synthetic customers only, and no path outside its temporary folder.

The server itself (training, caching, serving) runs in the browser tests; these
are the parts that decide what it may touch, checked without running it.
"""

from __future__ import annotations

import copy
import importlib.util
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from churnsense.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_RATE_LIMITS,
    PROJECT_ROOT,
    RateLimit,
    load_config,
)

_SPEC = importlib.util.spec_from_file_location(
    "e2e_server", PROJECT_ROOT / "scripts" / "e2e_server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
e2e_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(e2e_server)

#: The IBM Telco ID format (tests/test_governance.py).
_DATASET_ID = re.compile(r"\b[0-9]{4}-[A-Z]{5}\b")


@pytest.fixture(scope="module")
def base() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def e2e_cfg(base, tmp_path):
    """The e2e configuration, written under ``tmp_path`` and loaded the way the server loads it."""
    conf = e2e_server.e2e_config(base, tmp_path, sha256="0" * 64, rows=2000)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(conf), encoding="utf-8")
    return load_config(path)


def test_the_synthetic_customers_are_all_e2e_ids_with_one_of_the_longest_kind():
    ids = e2e_server.synthetic_customers(rows=300, seed=1)["customerID"]
    assert ids.is_unique and len(ids) == 300
    assert ids.str.startswith(e2e_server.ID_PREFIX).all()
    assert not ids.str.startswith("DEMO-").any()  # the batch demo file's rows never match one
    assert (ids.str.len() == 32).sum() == 1
    assert ids.str.fullmatch(r"[A-Za-z0-9-]{1,32}").all()  # the API's ID pattern
    assert not ids.str.contains(_DATASET_ID).any()


def test_the_e2e_configuration_keeps_every_section_and_moves_every_path(base, tmp_path):
    conf = e2e_server.e2e_config(base, tmp_path, sha256="ab" * 32, rows=2000)
    assert conf.keys() == base.keys()
    assert conf["paths"].keys() == base["paths"].keys()
    for path in conf["paths"].values():
        assert Path(path).is_absolute() and Path(path).is_relative_to(tmp_path.resolve())
    assert conf["dataset"]["sha256"] == "ab" * 32 and conf["dataset"]["expected_rows"] == 2000
    assert conf["training"]["n_jobs"] == 1
    assert conf["api"]["rate_limits"] == e2e_server.RATE_LIMITS
    # The repository's configuration is read, never changed.
    assert base == yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_the_e2e_configuration_is_one_the_api_accepts_and_stays_inside_its_folder(
    e2e_cfg, tmp_path
):
    e2e_server.confine(e2e_cfg, tmp_path)
    assert e2e_cfg.raw_data_file.is_relative_to(tmp_path)
    assert dict(e2e_cfg.api.rate_limits)["explain"].burst == 1000


# --- the budgets: raised for the browser tests only ----------------------------------------

#: The service's real per-client budgets. A change here is a change to a public
#: limit: it belongs in a reviewed change of its own, never in the test server.
PRODUCTION_BUDGETS = {
    "explain": RateLimit(burst=10, per_seconds=2.0),
    "batch": RateLimit(burst=5, per_seconds=6.0),
    "query": RateLimit(burst=30, per_seconds=0.5),
    "predict": RateLimit(burst=20, per_seconds=0.5),
    "export": RateLimit(burst=3, per_seconds=10.0),
}


def _drive(budgets, path: str, times: int, *, method: str = "GET") -> list[tuple[int, str | None]]:
    """``times`` requests, one after another, from one loopback client: (status, error code)."""
    import asyncio
    import json

    from churnsense.api import middleware

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    limiter = middleware.RateLimit(app, clock=lambda: 0.0)  # no time passes: no refill
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "client": ("127.0.0.1", 50000),
    }
    answers = []
    before = middleware.RATE_LIMITS
    middleware.RATE_LIMITS = budgets
    try:
        for _ in range(times):
            sent: list[dict] = []

            async def send(message, sent=sent):
                sent.append(message)

            asyncio.run(limiter(dict(scope), receive, send))
            status = sent[0]["status"]
            error = json.loads(sent[1]["body"])["error"] if status != 204 else None
            answers.append((status, error))
    finally:
        middleware.RATE_LIMITS = before
    return answers


def test_every_budget_the_service_has_is_raised_for_the_browser_tests(e2e_cfg):
    """A budget left at its real value lets one browser test starve another (PR #34's e2e run)."""
    from churnsense.api.middleware import RATE_LIMITED

    budgeted = set(RATE_LIMITED.values())
    assert budgeted == {name for name, _ in DEFAULT_RATE_LIMITS} == set(e2e_server.RATE_LIMITS)
    loaded = dict(e2e_cfg.api.rate_limits)
    assert set(loaded) == budgeted
    for name in budgeted:
        assert loaded[name] == RateLimit(burst=1000, per_seconds=0.001), name


@pytest.mark.parametrize("path", ["/api/customers", "/api/segments", "/api/threshold/sweep"])
def test_one_client_at_the_real_query_budget_is_refused_and_at_the_e2e_one_is_not(e2e_cfg, path):
    """What the browser suite met: sequential queries, never two in flight, refused by budget."""
    real = _drive(PRODUCTION_BUDGETS, path, 40)
    assert real[:30] == [(204, None)] * 30
    assert real[30:] == [(429, "rate_limited")] * 10, "the budget refuses, not the in-flight cap"
    assert _drive(dict(e2e_cfg.api.rate_limits), path, 400) == [(204, None)] * 400


def test_the_e2e_budgets_do_not_alter_the_production_defaults(cfg):
    """Importing and using the test server leaves every real limit exactly as it was."""
    from churnsense.api import middleware

    e2e_server.e2e_config(
        yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")),
        Path("/nonexistent-e2e-root"),
        sha256="0" * 64,
        rows=10,
    )
    assert dict(DEFAULT_RATE_LIMITS) == PRODUCTION_BUDGETS
    assert dict(cfg.api.rate_limits) == PRODUCTION_BUDGETS
    deployed = load_config(PROJECT_ROOT / "configs" / "deploy.yaml")
    assert dict(deployed.api.rate_limits) == PRODUCTION_BUDGETS
    assert middleware.MAX_CLIENT_REQUESTS == 12
    assert middleware.RATE_LIMITED == {
        ("POST", "/api/explain"): "explain",
        ("POST", "/api/batch/score"): "batch",
        ("POST", "/api/predict/batch"): "batch",
        ("POST", "/predict/batch"): "batch",
        ("POST", "/predict"): "predict",
        ("POST", "/api/predict"): "predict",
        ("GET", "/api/customers"): "query",
        ("GET", "/api/segments"): "query",
        ("GET", "/api/threshold/sweep"): "query",
        ("GET", "/api/customers/export"): "export",
    }


def test_the_in_flight_cap_still_applies_under_the_e2e_budgets(e2e_cfg):
    """The test server raises budgets only: a client past the cap is still refused."""
    import asyncio

    from churnsense.api import middleware

    held = asyncio.Event()
    statuses: list[int] = []

    async def app(scope, receive, send):
        await held.wait()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def one(limiter):
        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/customers",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
        await limiter(scope, receive, send)

    async def run():
        limiter = middleware.RateLimit(app)
        tasks = [asyncio.create_task(one(limiter)) for _ in range(middleware.MAX_CLIENT_REQUESTS)]
        await asyncio.sleep(0)
        await one(limiter)  # one more, while the others are held open
        assert statuses == [429]
        held.set()
        await asyncio.gather(*tasks)

    before = middleware.RATE_LIMITS
    middleware.RATE_LIMITS = dict(e2e_cfg.api.rate_limits)
    try:
        asyncio.run(run())
    finally:
        middleware.RATE_LIMITS = before
    assert statuses == [429] + [204] * middleware.MAX_CLIENT_REQUESTS


def test_the_test_server_overrides_budgets_through_its_own_configuration_only():
    """No patch of the middleware, no change to the cap, and no place in a deployment."""
    import ast

    source = (PROJECT_ROOT / "scripts" / "e2e_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        *(node.id for node in ast.walk(tree) if isinstance(node, ast.Name)),
        *(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)),
        *(alias.name for node in ast.walk(tree) if isinstance(node, ast.alias) for alias in [node]),
    }
    assert "MAX_CLIENT_REQUESTS" not in names
    assert "middleware" not in names and "churnsense.api.middleware" not in names
    keys = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "rate_limits"
    ]
    assert len(keys) == 1, "the budgets are set in one place: the temporary file"
    assert 'conf["api"]["rate_limits"] = copy.deepcopy(RATE_LIMITS)' in source
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!scripts/verify_deploy_bundle.py" in dockerignore
    assert "e2e_server" not in dockerignore and "!scripts/\n" not in dockerignore
    for name in ("config.yaml", "deploy.yaml"):
        text = (PROJECT_ROOT / "configs" / name).read_text(encoding="utf-8")
        assert "1000" not in text.split("rate_limits:")[1].split("\n\n")[0], name


@pytest.mark.parametrize("field", ["raw_dir", "artifacts_dir", "reports_dir", "figures_dir"])
def test_a_path_back_in_the_repository_is_refused(e2e_cfg, tmp_path, field):
    moved = replace(e2e_cfg, paths=replace(e2e_cfg.paths, **{field: PROJECT_ROOT / "artifacts"}))
    with pytest.raises(ValueError, match=field):
        e2e_server.confine(moved, tmp_path)


def test_a_relative_path_is_refused_even_where_it_would_land_inside(e2e_cfg, tmp_path, monkeypatch):
    # Run from the folder itself, a relative path resolves inside it; it is still
    # refused, since the API would resolve it against the repository instead.
    monkeypatch.chdir(tmp_path)
    moved = replace(e2e_cfg, paths=replace(e2e_cfg.paths, reports_dir=Path("reports")))
    with pytest.raises(ValueError, match="reports_dir"):
        e2e_server.confine(moved, tmp_path)


def test_a_dataset_file_outside_the_folder_is_refused(e2e_cfg, tmp_path):
    moved = replace(e2e_cfg, dataset=replace(e2e_cfg.dataset, filename="../../../elsewhere.csv"))
    with pytest.raises(ValueError, match="dataset"):
        e2e_server.confine(moved, tmp_path)


def test_a_temporary_folder_inside_the_repository_is_refused(e2e_cfg):
    with pytest.raises(ValueError, match="inside the repository"):
        e2e_server.confine(e2e_cfg, PROJECT_ROOT / "frontend")
    with pytest.raises(ValueError, match="inside the repository"):
        e2e_server.outside_repository(PROJECT_ROOT)


@pytest.mark.parametrize("escape", ["../elsewhere", "/elsewhere", "data/../../elsewhere"])
def test_a_base_path_that_leaves_the_folder_is_refused(base, tmp_path, escape):
    moved = copy.deepcopy(base)
    moved["paths"]["raw_dir"] = escape
    with pytest.raises(ValueError, match="raw_dir"):
        e2e_server.e2e_config(moved, tmp_path, sha256="0" * 64, rows=2000)


def test_nothing_is_written_before_the_paths_are_checked(base, tmp_path, monkeypatch):
    # The dataset and the configuration are the first writes; an escaping base
    # path is refused before either.
    moved = copy.deepcopy(base)
    moved["paths"]["raw_dir"] = "../escaped"
    config = tmp_path / "base.yaml"
    config.write_text(yaml.safe_dump(moved), encoding="utf-8")
    monkeypatch.setattr(e2e_server, "DEFAULT_CONFIG_PATH", config)
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="raw_dir"):
        e2e_server.prepare(root, rows=300, seed=1)
    assert not (tmp_path / "escaped").exists()
    assert list(root.iterdir()) == []
