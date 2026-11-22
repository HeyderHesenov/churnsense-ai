"""The Workbench's three reads: what they return, where they are served, what guards them.

They share the Data Hub preview's gate: absent unless a local deployment
switches it on, never in a public demo. Every V1 layer stands in front of them,
and a request reads nothing from disk and writes nothing anywhere. No model,
book or dataset is loaded; every account is synthetic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from churnsense.config import PROJECT_ROOT, load_config
from churnsense.workspace import api as preview_api
from churnsense.workspace import risk, synthetic, workbench, workbench_api
from tests.workspace_csv import payload, row
from tests.workspace_snapshots import AS_OF, snap

PREFIX = preview_api.PREFIX
SUMMARY = PREFIX + workbench_api.SUMMARY_PATH
ACCOUNTS = PREFIX + workbench_api.ACCOUNTS_PATH
DETAIL = ACCOUNTS + "/{account_id}"
V2_PATHS = {preview_api.PREVIEW_PATH, SUMMARY, ACCOUNTS, DETAIL}
LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)
ON = {preview_api.FLAG: "1"}
DEPLOY_CONFIG = PROJECT_ROOT / "configs" / "deploy.yaml"


def _paths(app) -> set[str]:
    app.openapi_schema = None
    return set(app.openapi()["paths"])


@pytest.fixture
def app(monkeypatch) -> Iterator:
    """The service's own application with V2 installed, for this test only."""
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
    application.dependency_overrides.clear()
    application.openapi_schema = None


@pytest.fixture
def client(app) -> TestClient:
    # No `with`: the lifespan, which loads the model, never runs.
    return TestClient(app, base_url=LOCAL, client=LOOPBACK)


def _all_rows(client, **params) -> list[dict]:
    rows, page = [], 1
    while True:
        body = client.get(ACCOUNTS, params={**params, "page": page, "page_size": 100}).json()
        rows += body["rows"]
        if page >= body["pages"]:
            return rows
        page += 1


# --- where it is served ----------------------------------------------------------------------


def test_the_workbench_is_off_by_default():
    from churnsense.api import main as api_main
    from churnsense.api import middleware

    assert preview_api.FLAG not in os.environ
    assert not V2_PATHS & _paths(api_main.app)
    assert frozenset() == middleware.LOCAL_QUERY_PATHS
    plain = TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK)
    for path in (SUMMARY, ACCOUNTS, ACCOUNTS + "/DEMO-ACC-000001"):
        answer = plain.get(path)
        assert answer.status_code == 404
        assert answer.json()["error"] == "not_found"


def test_a_local_opt_in_serves_exactly_the_v2_routes(app, client):
    from churnsense.api import middleware

    assert {p for p in _paths(app) if p.startswith(PREFIX)} == V2_PATHS
    assert {SUMMARY, ACCOUNTS} == middleware.LOCAL_QUERY_PATHS
    for path in (SUMMARY, ACCOUNTS, ACCOUNTS + "/DEMO-ACC-000001"):
        assert client.get(path).status_code == 200


def test_a_public_demo_never_installs_them_even_with_the_flag():
    from fastapi import FastAPI

    fresh = FastAPI()
    assert not preview_api.install(fresh, ON, load_config(DEPLOY_CONFIG))
    assert not V2_PATHS & _paths(fresh)


_SERVICE = textwrap.dedent(
    f"""
    import json
    from fastapi.testclient import TestClient
    from churnsense.api import main, middleware
    middleware.RATE_LIMITS = {{}}
    client = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5))
    paths = [{SUMMARY!r}, {ACCOUNTS!r}, {ACCOUNTS + "/DEMO-ACC-000001"!r}]
    documented = main.app.openapi()["paths"]
    print(json.dumps({{
        "documented": sorted(p for p in documented if p.startswith({PREFIX!r})),
        "v1_documented": "/api/customers" in documented and "/predict" in documented,
        "statuses": [client.get(p).status_code for p in paths],
        "local_queries": sorted(middleware.LOCAL_QUERY_PATHS),
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


def test_the_public_demo_service_has_no_workbench_even_with_the_flag_set():
    result, stderr = _service(DEPLOY_CONFIG, ON)
    assert result == {
        "documented": [],
        "v1_documented": True,
        "statuses": [404, 404, 404],
        "local_queries": [],
        "mode": "public_demo",
    }
    assert "stays off" in stderr


def test_the_local_service_serves_the_workbench_only_with_the_flag():
    """The control for the test above: the same harness sees the routes where they are served."""
    local = PROJECT_ROOT / "configs" / "config.yaml"
    result, _ = _service(local, ON)
    assert result == {
        "documented": sorted(V2_PATHS),
        "v1_documented": True,
        "statuses": [200, 200, 200],
        "local_queries": sorted({SUMMARY, ACCOUNTS}),
        "mode": "local",
    }
    result, _ = _service(local, {})
    assert (result["documented"], result["statuses"]) == ([], [404, 404, 404])


def test_the_routes_refuse_themselves_outside_a_local_deployment(client, monkeypatch):
    """Mounted into a public demo, each route still answers 404: the mode is read per request."""
    public = load_config(DEPLOY_CONFIG)
    monkeypatch.setattr(preview_api, "load_config", lambda: public)
    for path in (SUMMARY, ACCOUNTS, ACCOUNTS + "/DEMO-ACC-000001"):
        assert client.get(path).status_code == 404


def test_the_v2_openapi_text_makes_no_causal_claim(app):
    from tests.test_wording import _offending

    app.openapi_schema = None
    assert not _offending(json.dumps(app.openapi()))


def test_the_committed_openapi_schema_has_no_v2_path():
    """The front end's types are generated from the default (gate off) schema."""
    schema = json.loads((PROJECT_ROOT / "frontend" / "openapi.json").read_text())
    assert not [p for p in schema["paths"] if p.startswith(PREFIX)]


def test_no_front_end_code_calls_or_computes_v2():
    """Every business number is the API's: no V2 page exists yet to compute one."""
    found = [
        path
        for path in (PROJECT_ROOT / "frontend" / "src").rglob("*")
        if path.is_file()
        and path.name != "schema.d.ts"
        and ("/v2" in (text := path.read_text(errors="ignore")) or "exposed_mrr" in text)
    ]
    assert found == []


# --- the service's own layers still stand in front -------------------------------------------


def test_another_host_site_or_machine_is_refused(app, client):
    assert client.get(SUMMARY, headers={"Host": "evil.example"}).status_code == 400
    assert client.get(ACCOUNTS, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get(ACCOUNTS, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    remote = TestClient(app, base_url=LOCAL, client=("203.0.113.9", 50000))
    assert remote.get(SUMMARY).status_code == 403


def test_every_answer_carries_the_security_headers_and_the_csp(client):
    from churnsense.api import middleware

    for answer in (client.get(SUMMARY), client.get(ACCOUNTS + "/DEMO-ACC-999999")):
        for name, value in middleware.SECURITY_HEADERS.items():
            assert answer.headers[name] == value


def test_a_body_gets_the_small_limit_not_the_upload_one(client):
    from churnsense.api import middleware

    limit = middleware.BodySizeLimit(lambda *_: None, max_bytes=5 * 1_048_576)
    for path in (SUMMARY, ACCOUNTS, ACCOUNTS + "/DEMO-ACC-000001"):
        assert limit.limit_for(path) == middleware.SMALL_BODY_BYTES
    big = b"x" * (middleware.SMALL_BODY_BYTES + 1)
    assert client.request("GET", ACCOUNTS, content=big).status_code == 413


def test_the_list_and_the_summary_spend_the_query_budget(client, monkeypatch):
    from churnsense.api import middleware
    from churnsense.config import RateLimit

    monkeypatch.setattr(middleware, "RATE_LIMITS", {"query": RateLimit(burst=1, per_seconds=3600)})
    assert client.get(SUMMARY).status_code == 200
    answer = client.get(ACCOUNTS)
    assert answer.status_code == 429 and "Retry-After" in answer.headers
    # One account is generated alone, so it is not budgeted, as V1's one customer is not.
    assert client.get(ACCOUNTS + "/DEMO-ACC-000001").status_code == 200


def test_v1_paths_and_budgets_are_unchanged(app):
    from churnsense.api import middleware, spa

    served = _paths(app)
    assert {"/api/customers", "/api/batch/score", "/predict", "/health"} <= served
    assert {"/api/batch/score", "/api/predict/batch", "/predict/batch"} == middleware.BATCH_PATHS
    assert not {path for _, path in middleware.RATE_LIMITED} & V2_PATHS
    assert not set(spa.PAGES) & V2_PATHS


# --- the portfolio summary ---------------------------------------------------------------------


def test_the_summary_is_the_books_own_numbers(client):
    body = client.get(SUMMARY).json()
    book = workbench.load_book()
    assessed = [e for e in book.entries if e.assessment.assessed]
    assert body["total_accounts"] == 500
    assert body["eligible_accounts"] == len(assessed)
    assert body["excluded_accounts"] == 500 - len(assessed)
    assert sum(body["not_assessed"].values()) == body["excluded_accounts"]
    assert sum(body["levels"].values()) == body["eligible_accounts"]
    assert sum(c["accounts"] for c in body["currencies"]) == 500
    for c in body["currencies"]:
        attention, watch = Decimal(c["attention_mrr"]), Decimal(c["watch_mrr"])
        assert Decimal(c["exposed_mrr"]) == attention + watch
        assert Decimal(c["active_mrr"]) == attention + watch + Decimal(c["no_signals_mrr"])
        own = [e for e in assessed if e.latest.currency == c["currency"]]
        assert Decimal(c["active_mrr"]) == sum((e.latest.mrr for e in own), Decimal(0))
        assert all(isinstance(c[k], str) for k in c if k.endswith("_mrr"))
    assert body["methodology"]["method"] == "rules"
    assert body["methodology"]["method_version"] == risk.METHOD_VERSION
    assert [r["code"] for r in body["methodology"]["rules"]] == list(risk.RULE_ORDER)
    assert body["dataset"]["source"] == "synthetic"
    assert client.get(SUMMARY).json() == body  # deterministic


def test_the_summary_matches_a_hand_worked_book(app, client):
    later = AS_OF + timedelta(days=40)
    entries = [
        snap(account_external_id="DEMO-ACC-900001", mrr=Decimal("0.10"), status="past_due"),
        snap(account_external_id="DEMO-ACC-900002", mrr=Decimal("0.20"), renewal_date=later),
        snap(account_external_id="DEMO-ACC-900003", currency="EUR", mrr=Decimal("7.00")),
        snap(account_external_id="DEMO-ACC-900004", mrr=Decimal(0), status="trialing"),
    ]
    book = workbench.Book(
        workbench.dataset_info(), tuple(workbench.Entry(s, risk.assess(s)) for s in entries)
    )
    app.dependency_overrides[workbench_api._book] = lambda: book
    body = client.get(SUMMARY).json()
    assert body["levels"] == {"attention": 1, "watch": 1, "no_signals": 1}
    assert body["not_assessed"] == {"trialing": 1, "paused": 0, "canceled": 0}
    eur, usd = body["currencies"]
    assert (eur["currency"], eur["active_mrr"], eur["exposed_mrr"]) == ("EUR", "7.00", "0")
    assert {k: usd[k] for k in ("active_mrr", "attention_mrr", "watch_mrr", "exposed_mrr")} == {
        "active_mrr": "0.30",
        "attention_mrr": "0.10",
        "watch_mrr": "0.20",
        "exposed_mrr": "0.30",
    }
    assert (usd["accounts"], usd["eligible_accounts"], usd["excluded_accounts"]) == (3, 2, 1)
    assert [w["accounts"] for w in body["renewals"]] == [0, 1]


def test_no_number_adds_two_currencies(client):
    body = client.get(SUMMARY).json()
    assert len(body["currencies"]) == 3
    assert not [k for k in body if "mrr" in k]
    assert "not predicted revenue loss" in body["definitions"]["exposed_mrr"]


# --- the account list -------------------------------------------------------------------------


def _expected_priority(rows):
    rank = {"attention": 0, "watch": 1, "no_signals": 2, None: 3}
    return sorted(
        rows,
        key=lambda r: (
            rank[r["assessment"]["level"]],
            r["days_to_renewal"] is None,
            r["days_to_renewal"] or 0,
            r["account_id"],
        ),
    )


def test_the_default_order_is_priority_with_a_stable_tie_break(client):
    rows = _all_rows(client)
    assert len(rows) == 500 and len({r["account_id"] for r in rows}) == 500
    assert rows == _expected_priority(rows)
    levels = [r["assessment"]["level"] for r in rows]
    assert levels.index("watch") > levels.index("attention")
    assert levels[-1] is None  # not assessed comes last
    assert _all_rows(client) == rows


def test_renewal_ascending_puts_accounts_without_a_renewal_last(client):
    rows = _all_rows(client, sort="renewal_asc")
    days = [r["days_to_renewal"] for r in rows]
    known = [d for d in days if d is not None]
    assert days[: len(known)] == sorted(known)
    assert all(d is None for d in days[len(known) :])
    for r in rows:
        if r["assessment"]["eligibility"] == "not_assessed":
            assert r["days_to_renewal"] is None


def test_mrr_sorting_needs_one_currency(client):
    answer = client.get(ACCOUNTS, params={"sort": "mrr_desc"})
    assert answer.status_code == 422
    assert answer.json()["error"] == "invalid_input"
    assert "currency" in answer.json()["detail"]
    rows = _all_rows(client, sort="mrr_desc", currency="EUR")
    assert {r["currency"] for r in rows} == {"EUR"}
    keys = [(-Decimal(r["mrr"]), r["account_id"]) for r in rows]
    assert keys == sorted(keys)


def test_filters_agree_with_the_summary(client):
    summary = client.get(SUMMARY).json()
    for level, count in summary["levels"].items():
        body = client.get(ACCOUNTS, params={"level": level}).json()
        assert body["total"] == count
        assert {r["assessment"]["level"] for r in body["rows"]} == {level}
    body = client.get(ACCOUNTS, params={"level": "not_assessed"}).json()
    assert body["total"] == summary["excluded_accounts"]
    assert {r["assessment"]["eligibility"] for r in body["rows"]} == {"not_assessed"}
    both = client.get(ACCOUNTS, params=[("level", "watch"), ("level", "attention")]).json()
    assert both["total"] == summary["levels"]["attention"] + summary["levels"]["watch"]
    assert both["filters"]["level"] == ["attention", "watch"]
    for c in summary["currencies"]:
        assert (
            client.get(ACCOUNTS, params={"currency": c["currency"]}).json()["total"]
            == (c["accounts"])
        )
    soon = client.get(ACCOUNTS, params={"renewal_within_days": 30}).json()
    assert soon["total"] == summary["renewals"][0]["accounts"]
    sixty = _all_rows(client, renewal_within_days=60)
    assert len(sixty) == sum(w["accounts"] for w in summary["renewals"])
    assert all(0 <= r["days_to_renewal"] <= 60 for r in sixty)


def test_status_segment_and_search_filters(client):
    rows = _all_rows(client, status=["paused", "canceled"])
    assert {r["status"] for r in rows} == {"paused", "canceled"}
    assert all(r["assessment"]["eligibility"] == "not_assessed" for r in rows)
    enterprise = _all_rows(client, segment="Enterprise")
    assert enterprise and {r["segment"] for r in enterprise} == {"Enterprise"}
    assert client.get(ACCOUNTS, params={"segment": "enterprise"}).json()["total"] == 0
    one = client.get(ACCOUNTS, params={"q": "demo-acc-000042"}).json()
    assert [r["account_id"] for r in one["rows"]] == ["DEMO-ACC-000042"]
    name = synthetic.generate_account(42).latest.account_name
    by_name = client.get(ACCOUNTS, params={"q": name.upper()}).json()
    assert "DEMO-ACC-000042" in [r["account_id"] for r in by_name["rows"]]
    assert client.get(ACCOUNTS, params={"q": "no such account"}).json()["total"] == 0


def test_pagination_is_bounded_and_clamped(client):
    first = client.get(ACCOUNTS, params={"page_size": 100}).json()
    assert (first["page"], first["pages"], len(first["rows"])) == (1, 5, 100)
    past = client.get(ACCOUNTS, params={"page": 99, "page_size": 100}).json()
    assert past["page"] == 5 and past["rows"] == _all_rows(client)[400:]
    empty = client.get(ACCOUNTS, params={"q": "nothing matches"}).json()
    assert (empty["total"], empty["page"], empty["pages"], empty["rows"]) == (0, 1, 1, [])


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 101},
        {"page_size": 0},
        {"page": 0},
        {"level": "high"},
        {"status": "churned"},
        {"currency": "usd"},
        {"sort": "probability"},
        {"renewal_within_days": -1},
        {"q": "x" * 101},
        {"segment": "bad\x01value"},
    ],
)
def test_an_unsupported_value_is_refused(client, params):
    answer = client.get(ACCOUNTS, params=params)
    assert answer.status_code == 422
    assert set(answer.json()) == {"error", "detail", "problems"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (ACCOUNTS, {"risk_level": "attention"}),
        (ACCOUNTS, [("currency", "USD"), ("currency", "EUR")]),
        (ACCOUNTS, [("sort", "priority"), ("sort", "mrr_desc")]),
        (SUMMARY, {"currency": "USD"}),
        (ACCOUNTS + "/DEMO-ACC-000001", {"seed": "1"}),
    ],
)
def test_an_unknown_or_repeated_parameter_is_refused_not_ignored(client, path, params):
    answer = client.get(path, params=params)
    assert answer.status_code == 422
    assert answer.json()["error"] == "invalid_input"


def test_a_row_carries_its_assessment_and_context(client):
    rows = client.get(ACCOUNTS, params={"page_size": 100}).json()["rows"]
    for r in rows:
        a = r["assessment"]
        assert a["method"] == "rules" and a["method_version"] == risk.METHOD_VERSION
        assert r["next_step_context"]
        if a["level"] == "attention":
            assert any(i["severity"] == "attention" for i in a["indicators"])
            assert r["next_step_context"].startswith("Attention: ")
        for key in ("owner", "task_status", "case_status", "playbook"):
            assert key not in r


# --- one account --------------------------------------------------------------------------------


def test_an_account_has_its_snapshot_assessment_and_history(client):
    account = synthetic.generate_account(3)
    body = client.get(f"{ACCOUNTS}/{account.account_id}").json()
    assert body["account_id"] == account.account_id
    assert body["latest"]["as_of_date"] == synthetic.DEFAULT_AS_OF.isoformat()
    assert body["latest"]["mrr"] == str(account.latest.mrr)
    assert [h["snapshot"]["as_of_date"] for h in body["history"]] == [
        s.as_of_date.isoformat() for s in account.snapshots
    ]
    assert body["history"][-1]["assessment"] == body["assessment"]
    assert body["history"][0]["assessment"]["trend_compared_with"] is None
    for entry in body["history"]:
        assert entry["assessment"]["as_of_date"] == entry["snapshot"]["as_of_date"]
    expected = [workbench.assessment_out(a) for a in risk.assess_history(account.snapshots)]
    assert [h["assessment"] for h in body["history"]] == [
        e.model_dump(mode="json") for e in expected
    ]
    sub = body["subscription"]
    assert sub["currency"] == account.latest.currency
    if account.latest.customer_since:
        assert sub["tenure_days"] == (AS_OF - account.latest.customer_since).days


def test_an_account_not_assessed_says_why(client):
    canceled = next(a for a in synthetic.generate() if a.latest.status == "canceled")
    body = client.get(f"{ACCOUNTS}/{canceled.account_id}").json()
    a = body["assessment"]
    assert (a["eligibility"], a["level"], a["indicators"]) == ("not_assessed", None, [])
    assert "Canceled" in a["not_assessed_reason"]
    assert body["subscription"]["days_to_renewal"] is None
    assert body["subscription"]["canceled_at"] is not None


@pytest.mark.parametrize("account_id", ["DEMO-ACC-000501", "DEMO-ACC-000000", "SYN-1", "x"])
def test_a_missing_account_is_the_structured_404(client, account_id):
    answer = client.get(f"{ACCOUNTS}/{account_id}")
    assert answer.status_code == 404
    assert answer.json() == {
        "error": "not_found",
        "detail": "No account with that ID.",
        "problems": [],
    }


def test_an_overlong_id_is_refused(client):
    assert client.get(f"{ACCOUNTS}/{'A' * 129}").status_code == 422


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_no_response_has_a_probability_score_or_outcome(client):
    bodies = [
        client.get(SUMMARY).json(),
        client.get(ACCOUNTS, params={"page_size": 100}).json(),
        client.get(f"{ACCOUNTS}/DEMO-ACC-000010").json(),
    ]
    for body in bodies:
        keys = _keys(body)
        banned = ("probab", "score", "churned", "label", "outcome", "predict")
        assert not {k for k in keys if any(w in k for w in banned)}


# --- the Data Hub and the Workbench are not connected ----------------------------------------


def test_an_upload_to_the_preview_never_reaches_the_workbench(client):
    before = (client.get(SUMMARY).json(), client.get(f"{ACCOUNTS}/DEMO-ACC-000001").json())
    upload = payload(
        [row(account_external_id="DEMO-ACC-000001", mrr="999999.99", status="past_due")]
    )
    answer = client.post(preview_api.PREVIEW_PATH, files={"file": ("a.csv", upload, "text/csv")})
    assert answer.status_code == 200 and answer.json()["committed"] is False
    after = (client.get(SUMMARY).json(), client.get(f"{ACCOUNTS}/DEMO-ACC-000001").json())
    assert before == after


# --- nothing is read from or written to disk; nothing is kept ---------------------------------

_OPENED: list[list[tuple]] = []


def _audit(event: str, args: tuple) -> None:
    if _OPENED and event in {"open", "tempfile.mkstemp", "tempfile.mkdtemp", "os.mkdir"}:
        _OPENED[-1].append((event, *args[:1]))


sys.addaudithook(_audit)


def test_the_audit_hook_sees_an_open():
    """The control: without it, an empty list below would prove nothing."""
    _OPENED.append([])
    try:
        Path(__file__).read_bytes()
    finally:
        events = _OPENED.pop()
    assert events


def test_the_reads_open_no_file_and_load_no_model_or_book(client):
    from churnsense.api import state

    client.get(SUMMARY)  # the configuration is read and cached by the service, as V1's is
    state.get_predictor.cache_clear()
    _OPENED.append([])
    try:
        assert client.get(SUMMARY).status_code == 200
        assert client.get(ACCOUNTS, params={"page_size": 100}).status_code == 200
        assert client.get(f"{ACCOUNTS}/DEMO-ACC-000250").status_code == 200
        assert client.get(f"{ACCOUNTS}/DEMO-ACC-000999").status_code == 404
    finally:
        events = _OPENED.pop()
    assert events == []
    assert state.get_predictor.cache_info().currsize == 0
    assert state._built is None


def test_no_module_keeps_anything_between_requests(client):
    from churnsense.workspace import portfolio, snapshot

    modules = (workbench_api, workbench, synthetic, risk, portfolio, snapshot, preview_api)
    before = {m.__name__: dict(vars(m)) for m in modules}
    client.get(SUMMARY)
    client.get(ACCOUNTS, params={"level": "attention"})
    client.get(f"{ACCOUNTS}/DEMO-ACC-000001")
    after = {m.__name__: dict(vars(m)) for m in modules}
    assert before.keys() == after.keys()
    for name in before:
        assert before[name].keys() == after[name].keys()
        assert all(before[name][k] is after[name][k] for k in before[name]), name
    assert risk.Rules() == risk.RULES
