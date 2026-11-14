"""The public demo, end to end: synthetic customers and published aggregates only.

The service is run the way the deployed demo runs it - ``customer_records:
synthetic`` - beside a "dataset" it must never show: conftest's synthetic
customers stand in for the real file, exactly as in every other test. Reading
that dataset through the service fails the test, whichever path reaches it.

What is checked across every response a visitor can get:

* no customer of the dataset is named, and no row of it is returned;
* every customer listed is a generated demo customer, labelled as one, with no
  outcome;
* every observed figure equals the one the dataset-backed service computes,
  and does not depend on the demo customers;
* when the demo data cannot be built, nothing is served in its place.
"""

from __future__ import annotations

import io
import json
import re
import shutil
from collections.abc import Iterator
from itertools import permutations
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from churnsense import disclosure_audit as audit
from churnsense import evidence as evidence_module
from churnsense import insights, score_disclosure
from churnsense.book import score_book
from churnsense.config import Config, Deployment
from churnsense.data import schema
from churnsense.demo import build
from churnsense.demo import customers as demo
from churnsense.evaluation.disclosure import MIN_CELL
from tests.conftest import PUBLIC_SHA256
from tests.test_api_dashboard import _write_reports

LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)
SYNTHETIC = Deployment(mode="local", customer_records="synthetic")
DATASET = Deployment(mode="local", customer_records="dataset")

ID_TOKEN = re.compile(r"[A-Za-z0-9-]+")
SWEEPS = ("", "?cost=20&success=0.5", "?horizon=24&margin=0.4", "?cost=120&success=0.1&horizon=6")
ONE_WAY = [f"/api/segments?rows={rows}" for rows in insights.DIMENSIONS]
TWO_WAY = [
    f"/api/segments?rows={rows}&cols={cols}" for rows, cols in permutations(insights.DIMENSIONS, 2)
]
EVIDENCE_PATHS = [
    "/api/overview",
    "/api/performance",
    *(f"/api/threshold/sweep{query}" for query in SWEEPS),
    *ONE_WAY,
    *TWO_WAY,
]


def _explode(*_args, **_kwargs):
    raise AssertionError("the public demo tried to read the dataset")


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


# --- the served environment --------------------------------------------------------


@pytest.fixture(scope="module")
def artifacts(
    public_predictor, public_parts, public_frame, public_book, cfg: Config, tmp_path_factory
) -> tuple[Path, Path]:
    """An artifact directory as the image holds it: the model, the published
    evidence and the demo explanations - and a reports directory."""
    from churnsense.explainability.shap_explain import background_sample, explanation_cache

    root = tmp_path_factory.mktemp("public_demo")
    directory, reports = root / "artifacts", root / "reports"
    shutil.copytree(public_predictor.source, directory)
    reports.mkdir()
    _write_reports(reports, public_frame, public_parts)
    columns = public_predictor.feature_columns
    cache = explanation_cache(
        public_predictor.model,
        public_predictor.meta,
        public_parts.X_val[columns].head(12),
        background_sample(public_parts.X_train[columns], cfg.random_seed),
        seed=cfg.random_seed,
        permutations=2,
    )
    shap_cache = directory / "shap_global.json"
    shap_cache.write_text(json.dumps(cache))
    build.write(
        directory,
        build.evidence_of(public_predictor, public_book, shap_cache),
        build.explanations_of(public_predictor, sample=12, permutations=2),
    )
    return directory, reports


#: The built front end's shell, as the image holds it (``frontend/dist``).
SHELL = "<!doctype html><html><head><title>ChurnSense AI</title></head><body></body></html>"


@pytest.fixture(scope="module")
def frontend_dist(tmp_path_factory) -> Path:
    """A built front end of this test's own, so readiness never depends on whether
    ``make frontend`` ran on this machine: CI's Python job builds none, and a
    developer's checkout usually has one. ``/ready`` itself is not changed."""
    dist = tmp_path_factory.mktemp("frontend") / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SHELL)
    (dist / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (dist / "assets" / "index-test.js").write_text("export {};\n")
    return dist


@pytest.fixture(scope="module")
def service(artifacts, public_frame, frontend_dist) -> Iterator[tuple[TestClient, dict[str, dict]]]:
    """The running service in public mode, and what the dataset-backed one answered.

    The dataset-backed answers are taken first, from the same model and the
    same "dataset", so the two can be compared figure by figure.
    """
    from churnsense.api import main as api_main
    from churnsense.api import spa, state
    from churnsense.data import loader

    directory, reports = artifacts
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(spa, "FRONTEND_DIST", frontend_dist)
        patch.setattr(state, "ARTIFACTS_DIR", directory)
        patch.setattr(state, "REPORTS_DIR", reports)
        patch.setattr(state, "DEPLOYMENT", DATASET)
        patch.setattr(
            state, "BOOK_LOADER", lambda model: score_book(public_frame, PUBLIC_SHA256, model)
        )
        state.reset()
        with TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK) as client:
            dataset_answers = {path: _ok(client.get(path)) for path in EVIDENCE_PATHS}

            patch.setattr(state, "DEPLOYMENT", SYNTHETIC)
            patch.setattr(state, "BOOK_LOADER", _explode)
            patch.setattr(loader, "read_raw", _explode)
            patch.setattr(loader, "verify_dataset", _explode)
            state.reset()
            yield client, dataset_answers
        state.reset()


@pytest.fixture(scope="module")
def public(service) -> TestClient:
    return service[0]


@pytest.fixture(scope="module")
def dataset_answers(service) -> dict[str, dict]:
    return service[1]


@pytest.fixture(scope="module")
def demo_book() -> pd.DataFrame:
    return demo.demo_customers()


@pytest.fixture(scope="module")
def real_ids(public_frame) -> frozenset[str]:
    return frozenset(public_frame[schema.ID_COLUMN])


def _fingerprints(frame: pd.DataFrame, columns: list[str]) -> set[tuple]:
    """Each row's values as text, numbers by value: 29.9 and "29.90" are one customer's."""
    return {
        tuple(_canonical(column, value) for column, value in zip(columns, row, strict=True))
        for row in frame[columns].itertuples(index=False, name=None)
    }


def _canonical(column: str, value: object) -> str:
    if column in schema.NUMERIC_COLUMNS:
        return f"{float(value):.6f}"
    return str(value).strip()


@pytest.fixture(scope="module")
def features(public_predictor) -> list[str]:
    return public_predictor.feature_columns


@pytest.fixture(scope="module")
def real_rows(public_frame, features) -> set[tuple]:
    return _fingerprints(public_frame, features)


@pytest.fixture(scope="module")
def demo_rows(demo_book, features) -> set[tuple]:
    return _fingerprints(demo_book, features)


@pytest.fixture
def rebuilt(public, artifacts, tmp_path, monkeypatch):
    """Serve from an edited copy of the artifacts for one test, then from the good ones again."""
    from churnsense.api import state

    copy = tmp_path / "artifacts"
    shutil.copytree(artifacts[0], copy)

    def serve() -> TestClient:
        monkeypatch.setattr(state, "ARTIFACTS_DIR", copy)
        state.reset()
        return public

    serve.directory = copy
    serve.client = public
    yield serve
    monkeypatch.undo()
    state.reset()


# --- what a response may hold -----------------------------------------------------------


def _row_of(values: dict, features: list[str]) -> tuple | None:
    """``values`` as a customer's fingerprint; ``None`` if it is not a customer's values
    (the feature labels are keyed by feature too, and are not numbers)."""
    if not set(features) <= values.keys():
        return None
    try:
        return tuple(_canonical(f, values[f]) for f in features)
    except (TypeError, ValueError):
        return None


def _rows_in(payload: object, features: list[str]) -> Iterator[tuple]:
    """Every customer-shaped object anywhere in a JSON payload, as a fingerprint."""
    if isinstance(payload, dict):
        if (row := _row_of(payload, features)) is not None:
            yield row
        contributions = payload.get("contributions")
        if isinstance(contributions, list) and contributions:
            values = {c["feature"]: c["value"] for c in contributions}
            if (row := _row_of(values, features)) is not None:
                yield row
        for value in payload.values():
            yield from _rows_in(value, features)
    elif isinstance(payload, list):
        for value in payload:
            yield from _rows_in(value, features)


def _names_a_dataset_customer(text: str, real_ids: frozenset[str]) -> set[str]:
    return set(ID_TOKEN.findall(text)) & real_ids


def _assert_clean(response, real_ids, real_rows, features) -> None:
    assert not _names_a_dataset_customer(response.text, real_ids), response.url
    if response.headers["content-type"].startswith("application/json"):
        leaked = set(_rows_in(response.json(), features)) & real_rows
        assert not leaked, f"{response.url} returns {len(leaked)} dataset row(s)"


# --- E1-E6: every customer is a demo customer ---------------------------------------------


def test_the_listing_holds_only_demo_customers(public, demo_book, real_ids):
    listed, page = [], 1
    while True:
        body = _ok(public.get(f"/api/customers?page={page}&page_size=100&sort=id&dir=asc"))
        listed += body["rows"]
        if page >= body["pages"]:
            break
        page += 1
    assert body["total"] == len(demo_book) == len(listed)
    assert [row["id"] for row in listed] == sorted(demo_book[schema.ID_COLUMN])
    assert not {row["id"] for row in listed} & real_ids


def test_every_listed_customer_is_synthetic_active_and_has_no_outcome(public):
    body = _ok(public.get("/api/customers?page_size=100"))
    assert {row["partition"] for row in body["rows"]} == {"synthetic"}
    assert {row["outcome"] for row in body["rows"]} == {"withheld"}
    assert {row["status"] for row in body["rows"]} == {"active"}
    assert all(row["at_risk"] is not None for row in body["rows"])
    assert body["status_counts"] == {"active": body["total"], "churned": 0, "unknown": 0}
    # Nothing was observed of a generated customer, so no observed rate exists.
    assert body["summary"]["observed"] is None
    assert body["withheld_reason"] is None


def test_revenue_at_risk_is_the_probability_times_the_charge(public):
    for row in _ok(public.get("/api/customers?page_size=50"))["rows"]:
        assert row["at_risk"] == pytest.approx(row["churn_probability"] * row["monthly_charges"])


@pytest.mark.parametrize("partition", ["train", "validation", "test", "unknown"])
def test_no_demo_customer_is_in_a_partition_of_the_dataset(public, partition):
    assert _ok(public.get(f"/api/customers?partition={partition}"))["total"] == 0


def test_the_synthetic_partition_is_the_whole_book(public, demo_book):
    assert _ok(public.get("/api/customers?partition=synthetic"))["total"] == len(demo_book)


def test_the_export_is_the_demo_book_and_nothing_else(
    public, demo_book, demo_rows, real_ids, real_rows, features
):
    response = public.get("/api/customers/export?sort=id&dir=asc")
    assert response.status_code == 200
    assert "demo" in response.headers["content-disposition"]
    exported = pd.read_csv(io.BytesIO(response.content), dtype=str)
    assert sorted(exported[schema.ID_COLUMN]) == sorted(demo_book[schema.ID_COLUMN])
    assert not set(exported[schema.ID_COLUMN]) & real_ids
    rows = _fingerprints(exported, features)
    assert rows == demo_rows
    assert not rows & real_rows
    assert set(exported["partition"]) == {"synthetic"}
    assert set(exported["outcome"]) == {"withheld"}
    assert set(exported["status"]) == {"active"}
    assert schema.TARGET not in exported.columns


def test_a_demo_customer_is_scored_like_any_record(public, demo_book, features):
    record = demo_book.iloc[17]
    detail = _ok(public.get(f"/api/customers/{record[schema.ID_COLUMN]}"))
    assert detail["partition"] == "synthetic" and detail["outcome"] == "withheld"
    served = _ok(public.post("/api/predict", json=detail["features"]))
    assert detail["churn_probability"] == pytest.approx(served["churn_probability"], abs=1e-12)
    assert tuple(_canonical(f, detail["features"][f]) for f in features) == tuple(
        _canonical(f, record[f]) for f in features
    )


def test_a_dataset_customer_cannot_be_looked_up(public, real_ids):
    for customer_id in sorted(real_ids)[:25]:
        response = public.get(f"/api/customers/{customer_id}")
        assert response.status_code == 404, customer_id
        assert customer_id not in response.text


def test_a_dataset_customer_cannot_be_listed_by_id_or_searched(public, real_ids):
    some = sorted(real_ids)[:20]
    query = "&".join(f"ids={customer_id}" for customer_id in some)
    assert _ok(public.get(f"/api/customers?{query}"))["total"] == 0
    assert public.get(f"/api/customers/export?{query}").content.count(b"\n") == 1
    # A search is by substring, and the fixture's "dataset" IDs are prefixes of demo
    # IDs: whatever it finds, it is never the dataset's customer.
    for customer_id in some[:5]:
        found = _ok(public.get(f"/api/customers?q={customer_id}&page_size=100"))
        assert not {row["id"] for row in found["rows"]} & real_ids
        assert all(row["partition"] == "synthetic" for row in found["rows"])


def test_a_dataset_customer_cannot_be_explained(public, real_ids):
    customer_id = sorted(real_ids)[0]
    response = public.post("/api/explain", json={"customer_ids": [customer_id]})
    assert response.status_code == 404


def test_the_examples_are_demo_customers(public, demo_book, real_ids, real_rows, features):
    body = _ok(public.get("/api/customers/examples"))
    assert body["customers"], "the single-customer page needs its examples"
    known = set(demo_book[schema.ID_COLUMN])
    for customer in body["customers"]:
        assert customer["id"] in known and customer["id"] not in real_ids
        assert customer["partition"] == "synthetic" and customer["outcome"] == "withheld"
    assert not set(_rows_in(body["customers"], features)) & real_rows


def test_an_explanation_is_of_a_demo_customer_against_a_synthetic_background(
    public, demo_book, features
):
    record = demo_book.iloc[3]
    body = _ok(public.post("/api/explain", json={"customer_ids": [record[schema.ID_COLUMN]]}))
    assert body["background"] == "synthetic"
    explanation = body["explanations"][0]
    values = {c["feature"]: c["value"] for c in explanation["contributions"]}
    assert tuple(_canonical(f, values[f]) for f in features) == tuple(
        _canonical(f, record[f]) for f in features
    )
    total = explanation["base_value"] + sum(c["shap_value"] for c in explanation["contributions"])
    assert total == pytest.approx(explanation["churn_probability"], abs=1e-9)


def test_a_typed_record_is_explained_against_the_synthetic_background_too(public):
    record = dict(schema.EXAMPLE_HIGH_RISK)
    body = _ok(public.post("/api/explain", json={"record": record}))
    assert body["background"] == "synthetic"
    by_id = _ok(public.post("/api/explain", json={"customer_ids": ["DEMO-000000"]}))
    assert body["explanations"][0]["base_value"] == pytest.approx(
        by_id["explanations"][0]["base_value"], abs=1e-12
    )


def test_the_global_explanation_draws_demo_customers_only(public, demo_rows, real_rows, features):
    body = _ok(public.get("/api/explain/global"))
    assert body["partition"] == "synthetic"
    drawn = set(_rows_in(body["x"], features))
    assert drawn and drawn <= demo_rows
    assert not drawn & real_rows
    assert body["n_explained"] == len(body["x"]) == len(body["values"])
    # Nothing of the training partition is read for a demo customer's page.
    assert body["total_charges_r"] is not None


def test_the_global_explanation_and_one_customer_share_a_baseline(public):
    cached = _ok(public.get("/api/explain/global"))
    one = _ok(public.post("/api/explain", json={"customer_ids": ["DEMO-000001"]}))
    assert one["explanations"][0]["base_value"] == pytest.approx(cached["base_value"], abs=1e-12)
    assert one["background_customers"] == cached["n_background"]


# --- every route, swept --------------------------------------------------------------------

#: Every GET the API documents, with what to ask it. A route added to the API
#: without an entry here fails ``test_every_documented_get_is_swept``.
SWEPT: dict[str, list[str]] = {
    "/health": ["/health"],
    "/ready": ["/ready"],
    "/model-info": ["/model-info"],
    "/api/health": ["/api/health"],
    "/api/ready": ["/api/ready"],
    "/api/model-info": ["/api/model-info"],
    "/api/overview": ["/api/overview"],
    "/api/customers": [
        "/api/customers",
        "/api/customers?page=3&page_size=100&sort=tenure",
        "/api/customers?flagged=true&sort=at_risk",
        "/api/customers?contract=Month-to-month&internet=Fiber%20optic&sort=monthly&dir=asc",
        "/api/customers?bands=High&bands=Critical&exclude_churned=true",
        "/api/customers?q=DEMO-0000",
    ],
    "/api/customers/export": [
        "/api/customers/export",
        "/api/customers/export?flagged=true",
        "/api/customers/export?ids=DEMO-000001&ids=DEMO-000002",
    ],
    "/api/customers/examples": ["/api/customers/examples"],
    "/api/customers/{customer_id}": ["/api/customers/DEMO-000000", "/api/customers/DEMO-001999"],
    "/api/explain/global": ["/api/explain/global"],
    "/api/segments": [*ONE_WAY, *TWO_WAY[:20]],
    "/api/threshold/sweep": [f"/api/threshold/sweep{query}" for query in SWEEPS],
    "/api/performance": ["/api/performance"],
}


def test_every_documented_get_is_swept(public):
    documented = {
        path
        for path, methods in public.get("/openapi.json").json()["paths"].items()
        if "get" in methods
    }
    assert documented == set(SWEPT), sorted(documented ^ set(SWEPT))


@pytest.mark.parametrize("path", [path for paths in SWEPT.values() for path in paths])
def test_no_get_names_a_dataset_customer_or_returns_a_dataset_row(
    public, path, real_ids, real_rows, features
):
    response = public.get(path)
    assert response.status_code == 200, response.text
    _assert_clean(response, real_ids, real_rows, features)


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/", "/explorer", "/favicon.svg"])
def test_no_static_answer_names_a_dataset_customer(public, path, real_ids):
    response = public.get(path)
    assert not _names_a_dataset_customer(response.text, real_ids)


def test_no_post_returns_a_dataset_row(public, real_ids, real_rows, features):
    answers = [
        public.post("/api/explain", json={"customer_ids": [f"DEMO-{i:06d}" for i in range(8)]}),
        public.post("/api/explain", json={"record": dict(schema.EXAMPLE_LOW_RISK)}),
        public.post("/api/predict", json=dict(schema.EXAMPLE_HIGH_RISK)),
    ]
    for response in answers:
        assert response.status_code == 200, response.text
        _assert_clean(response, real_ids, real_rows, features)


def test_no_row_endpoint_states_an_outcome(public):
    """A generated customer churned or stayed nowhere: the words never reach a row."""
    for path in (
        "/api/customers?page_size=100",
        "/api/customers/examples",
        "/api/customers/DEMO-000005",
    ):
        text = json.dumps(_ok(public.get(path)))
        assert '"outcome": "churned"' not in text and '"outcome": "stayed"' not in text
        assert '"status": "churned"' not in text and '"status": "unknown"' not in text
    export = public.get("/api/customers/export").text
    assert ",churned" not in export and ",stayed" not in export


# --- the observed figures are the dataset-backed ones --------------------------------------


def _same(found: object, expected: object, where: str = "") -> None:
    """Equal, with floats compared to the last few digits: two orders of one sum."""
    if isinstance(expected, dict):
        assert isinstance(found, dict) and found.keys() == expected.keys(), where
        for key in expected:
            _same(found[key], expected[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(found, list) and len(found) == len(expected), where
        for i, (a, b) in enumerate(zip(found, expected, strict=True)):
            _same(a, b, f"{where}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert found == pytest.approx(expected, rel=1e-9, abs=1e-9), where
    else:
        assert found == expected, where


@pytest.mark.parametrize("path", [*ONE_WAY, *TWO_WAY])
def test_every_breakdown_is_the_dataset_backed_one(public, dataset_answers, path):
    _same(_ok(public.get(path)), dataset_answers[path], path)


def _cuts(answer: dict) -> list[float]:
    return [point["threshold"] for point in answer["points"]]


def _the_fine_points_at(found: list[dict], fine: list[dict], where: str) -> None:
    """Each published point is the dataset-backed sweep's point at the same threshold."""
    by_threshold = {point["threshold"]: point for point in fine}
    assert len(found) < len(fine), f"{where}: the published sweep is as fine as the dataset's"
    for point in found:
        _same(point, by_threshold[point["threshold"]], f"{where}@{point['threshold']}")


@pytest.mark.parametrize("query", SWEEPS)
def test_the_sweep_is_the_dataset_backed_one_read_at_the_safe_cuts(public, dataset_answers, query):
    path = f"/api/threshold/sweep{query}"
    found, expected = _ok(public.get(path)), dataset_answers[path]
    for key in ("assumptions", "customers", "churners", "shipped_threshold", "disclaimer"):
        _same(found[key], expected[key], key)
    _the_fine_points_at(found["points"], expected["points"], path)
    cuts = _cuts(found)
    assert found["shipped_threshold"] in cuts
    by_threshold = {point["threshold"]: point for point in expected["points"]}
    for name in ("recommended", "peak"):
        scenario = found[name]
        assert scenario["threshold"] in cuts, name
        for key in ("flagged", "true_positives", "net_benefit"):
            _same(scenario[key], by_threshold[scenario["threshold"]][key], f"{name}.{key}")
    assert {found["band"]["low"], found["band"]["high"]} <= set(cuts)
    assert {row["threshold"] for row in found["sensitivity"]} <= set(cuts)


def test_performance_is_the_dataset_backed_one_on_the_safe_cuts(public, dataset_answers):
    found, expected = _ok(public.get("/api/performance")), dataset_answers["/api/performance"]
    # The book's own counts differ, and the test release is the ledger's, not evidence.
    same = expected.keys() - {
        "bands",
        "test",
        "test_reason",
        "validation",
        "validation_evaluation",
        "calibration",
    }
    for key in same:
        _same(found[key], expected[key], key)
    for key in ("customers", "churners"):
        assert found["validation"][key] == expected["validation"][key]
    _the_fine_points_at(found["validation"]["points"], expected["validation"]["points"], "curve")
    cuts = _cuts(found["validation"])

    measured, wanted = found["validation_evaluation"], expected["validation_evaluation"]
    for key in ("metrics", "churn_rate", "no_skill_accuracy", "ece"):
        _same(measured[key], wanted[key], key)
    assert [(b["low"], b["high"]) for b in measured["reliability"]] == list(
        zip(cuts, [*cuts[1:], 1.0], strict=True)
    )
    assert all(
        b["churned"] >= MIN_CELL and b["customers"] - b["churned"] >= MIN_CELL
        for b in measured["reliability"]
    )
    for row, before in zip(measured["segments"], wanted["segments"], strict=True):
        if row["churners"] is not None:
            _same(row, before, row["segment"])
        else:
            assert all(row[key] is None for key in insights.SEGMENT_OUTCOMES)
            assert (row["customers"], row["flagged"]) == (before["customers"], before["flagged"])

    calibration, recorded = found["calibration"], expected["calibration"]
    for key in ("method", "epsilon", "uncalibrated_ece", "calibrated_ece"):
        _same(calibration[key], recorded[key], key)
    assert calibration["uncalibrated"] is None, "the candidate's own cut at 0.5 is not published"
    assert 0.5 in cuts
    half = next(p for p in found["validation"]["points"] if p["threshold"] == 0.5)
    _same(calibration["calibrated"]["precision"], half["precision"], "precision at 0.5")
    _same(calibration["calibrated"]["recall"], half["recall"], "recall at 0.5")
    _same(calibration["calibrated"]["brier"], recorded["calibrated"]["brier"], "brier")
    _bands_are_guarded(found["bands"], expected["bands"])


def _bands_are_guarded(found: list[dict], expected: list[dict]) -> None:
    hidden = 0
    for band, wanted in zip(found, expected, strict=True):
        if band["observed"]["rate"] is None and wanted["observed"]["rate"] is not None:
            hidden += 1
            assert band["observed"]["customers"] == wanted["observed"]["customers"]
            assert band["observed"]["churned"] is None
        else:
            _same(band["observed"], wanted["observed"], band["name"])
    assert hidden, "no band is hidden by the score rule: nothing is checked"


def test_the_overviews_observed_figures_are_the_dataset_backed_ones_where_safe(
    public, dataset_answers
):
    found, expected = _ok(public.get("/api/overview")), dataset_answers["/api/overview"]
    for key in ("observed", "segments", "lifecycle", "association", "threshold"):
        _same(found[key], expected[key], key)
    _same(found["importance"], expected["importance"], "importance")
    _same(found["importance_basis"], expected["importance_basis"], "importance_basis")
    _bands_are_guarded(found["bands"], expected["bands"])
    # Validation customers per 0.05 of score would cut the published intervals finer.
    assert all(b["observed"] is None and b["observed_mean"] is None for b in found["histogram"])
    assert any(b["observed"] is not None for b in expected["histogram"])

    campaign, wanted = found["campaign"], expected["campaign"]
    for key in (
        "threshold",
        "customers",
        "flagged",
        "precision",
        "recall",
        "intervention_cost",
        "retained_value",
        "net_benefit",
        "assumptions",
    ):
        _same(campaign[key], wanted[key], key)
    cuts = [point["threshold"] for point in campaign["curve"]]
    fine = {point["threshold"]: point for point in wanted["curve"]}
    assert len(cuts) < len(fine) and campaign["threshold"] in cuts
    for point in campaign["curve"]:
        _same(point, fine[point["threshold"]], "curve")
    assert {campaign["peak_threshold"], campaign["recommended_threshold"]} <= set(cuts)


def test_the_overviews_book_is_the_demo_book(public, demo_book, dataset_answers):
    found = _ok(public.get("/api/overview"))
    assert found["customers"] == len(demo_book) != dataset_answers["/api/overview"]["customers"]
    assert found["monthly_charges"] == pytest.approx(
        demo_book["MonthlyCharges"].astype(float).sum()
    )
    assert found["current_monthly_charges"] == pytest.approx(found["monthly_charges"])
    assert found["unknown_customers"] == 0 and found["unknown_monthly_charges"] == 0
    assert sum(band["customers"] for band in found["bands"]) == len(demo_book)
    assert sum(bin_["customers"] for bin_ in found["histogram"]) == len(demo_book)
    queue = found["flagged"]
    assert queue["in_queue"] == queue["in_queue_active"] == queue["customers"]
    assert queue["in_queue_unknown"] == 0
    assert found["withheld_reason"] is None


def test_a_breakdown_counts_the_dataset_not_the_demo_book(public, public_frame, demo_book):
    body = _ok(public.get("/api/segments?rows=Contract"))
    assert body["book_customers"] == len(public_frame) != len(demo_book)


def test_the_observed_figures_do_not_depend_on_the_demo_customers(
    rebuilt, public_predictor, monkeypatch
):
    """Another demo book changes what is listed and nothing that was observed."""
    from churnsense.api import state

    others = demo.demo_customers(n=300, seed=99)
    explanations = build.explanations_of(public_predictor, others, sample=6, permutations=2)
    (rebuilt.directory / evidence_module.DEMO_EXPLANATIONS_FILE).write_text(
        json.dumps(explanations), encoding="utf-8"
    )
    paths = ("/api/segments?rows=Contract&cols=tenure", "/api/threshold/sweep")
    before = {path: _ok(rebuilt.client.get(path)) for path in paths}
    before_measured = _ok(rebuilt.client.get("/api/performance"))["validation_evaluation"]
    monkeypatch.setattr(state, "DEMO_GENERATOR", lambda: others)
    client = rebuilt()
    assert _ok(client.get("/api/customers"))["total"] == 300
    for path in paths:
        _same(_ok(client.get(path)), before[path], path)
    _same(_ok(client.get("/api/performance"))["validation_evaluation"], before_measured)


# --- model-info says what is served ---------------------------------------------------------


def test_model_info_names_the_synthetic_source(public, demo_book):
    source = _ok(public.get("/api/model-info"))["data_source"]
    assert source["customer_records"] == "synthetic"
    assert source["customers"] == len(demo_book)
    assert "synthetic" in source["title"].lower()
    for key in ("notice", "figures_note", "evidence_note"):
        assert isinstance(source[key], str) and source[key]
    assert "illustrative" in source["figures_note"].lower()
    assert "aggregate" in source["evidence_note"].lower()
    assert source["sweep_note"] is None, "a sweep is published: nothing to say about its absence"
    # Why the sweep has few cuts, the calibration bins are uneven and no validation
    # histogram is drawn: the service's own sentence, which the pages print.
    assert f"{MIN_CELL} churners and {MIN_CELL} stayers" in source["evidence_note"]
    # A visitor comparing with the model card learns the peak here is among those cuts.
    assert "peak and the recommendation are read among those cuts" in source["evidence_note"]


_COUNTS_AT_A_CUT = {
    "threshold",
    "n",
    "positives",
    "tn",
    "fp",
    "fn",
    "tp",
    "accuracy",
    "precision",
    "recall",
    "f1",
}


def test_model_info_states_validation_counts_only_at_a_published_cut(public, public_predictor):
    """The model's record at 0.5 is a cut of its own: here it is read off the published sweep."""
    metrics = _ok(public.get("/api/model-info"))["validation_metrics"]
    point = next(
        p for p in _ok(public.get("/api/threshold/sweep"))["points"] if p["threshold"] == 0.5
    )
    assert (metrics["threshold"], metrics["tp"], metrics["fp"]) == (
        0.5,
        point["true_positives"],
        point["false_positives"],
    )
    assert metrics["precision"] == pytest.approx(point["precision"])
    assert metrics["recall"] == pytest.approx(point["recall"])
    # The whole-partition scalars are the published evidence's, the ones /api/performance
    # serves: one ROC-AUC per partition, whichever page shows it.
    measured = _ok(public.get("/api/performance"))["validation_evaluation"]["metrics"]
    for scalar in ("roc_auc", "average_precision", "brier"):
        assert metrics[scalar] == measured[scalar]
    recorded = public_predictor.meta["validation_metrics"]
    assert metrics["roc_auc"] == pytest.approx(recorded["roc_auc"], abs=0.02)


def test_model_info_states_no_validation_count_where_0_5_is_not_a_cut(rebuilt):
    def without_one_half(body: dict) -> None:
        basis = body["sweep_basis"]
        i = basis["thresholds"].index(0.5)
        for name in (
            "thresholds",
            "true_positives",
            "false_positives",
            "false_negatives",
            "flagged_churner_charges",
        ):
            basis[name].pop(i)
        body["validation"] = None  # its bins were the intervals 0.5 split

    _edit(rebuilt.directory / evidence_module.EVIDENCE_FILE, without_one_half)
    client = rebuilt()
    assert 0.5 not in _cuts(_ok(client.get("/api/threshold/sweep")))
    metrics = _ok(client.get("/api/model-info"))["validation_metrics"]
    assert not metrics.keys() & _COUNTS_AT_A_CUT
    assert set(metrics) == {"roc_auc", "average_precision", "brier"}


def test_model_info_reports_verified_provenance_from_the_evidence(public):
    provenance = _ok(public.get("/api/model-info"))["provenance"]
    assert provenance["verified"] is True and provenance["withheld_reason"] is None


# --- E7: no label is compared, and nothing tells a test customer apart ------------------------


def _upload(public, frame: pd.DataFrame):
    return public.post(
        "/api/batch/score",
        files={"file": ("customers.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )


def test_a_batch_compares_and_returns_no_label(public, public_raw):
    body = _ok(_upload(public, public_raw.head(60)))
    assert body["summary"]["labels"] is None
    assert "not compared" in body["summary"]["labels_reason"]
    assert all(row["label"] is None for row in body["rows"])
    assert schema.TARGET not in pd.read_csv(io.StringIO(body["csv"])).columns
    # The note about the column says what happens here, not what happens beside a dataset.
    (note,) = [n for n in body["notes"] if n.startswith("Churn")]
    assert note == "Churn is never used to score, and here it is not compared with the predictions"
    assert not any("kept only to compare" in n for n in body["notes"])


def test_a_batch_answers_alike_for_test_customers_and_strangers(
    public, public_raw, public_frame, public_parts
):
    """Nothing in the answer depends on whether an uploaded row is a held-out customer."""
    test_ids = set(public_frame.loc[public_parts.X_test.index, schema.ID_COLUMN])
    held = public_raw[public_raw[schema.ID_COLUMN].isin(test_ids)].head(30)
    seen = public_raw[~public_raw[schema.ID_COLUMN].isin(test_ids)].head(30)
    for frame in (held, seen):
        body = _ok(_upload(public, frame))
        assert body["summary"]["labels"] is None
        assert "excluded_test" not in json.dumps(body)
    a, b = _ok(_upload(public, held))["summary"], _ok(_upload(public, seen))["summary"]
    assert a["labels_reason"] == b["labels_reason"] and a.keys() == b.keys()


# --- the generated book's own rules ------------------------------------------------------------


def test_a_generated_book_takes_no_outcome_column(public_predictor):
    """Nobody generated has an outcome: a frame that carries one is not the generator's."""
    from churnsense.book import synthetic_book
    from churnsense.exceptions import DataError

    generated = demo.demo_customers(60).assign(**{schema.TARGET: "No"})
    with pytest.raises(DataError, match="no outcome"):
        synthetic_book(generated, public_predictor)


def test_generated_customers_must_each_have_their_own_id(public_predictor):
    """A lookup by ID answers for one customer: two under one ID would hide one of them."""
    from churnsense.book import synthetic_book
    from churnsense.exceptions import DataError

    generated = demo.demo_customers(60)
    generated.loc[1, schema.ID_COLUMN] = generated.loc[0, schema.ID_COLUMN]
    with pytest.raises(DataError, match="their own ID"):
        synthetic_book(generated, public_predictor)
    assert len(synthetic_book(demo.demo_customers(60), public_predictor).frame) == 60


# --- fail closed --------------------------------------------------------------------------

DATA_PATHS = (
    "/api/overview",
    "/api/customers",
    "/api/customers/export",
    "/api/customers/examples",
    "/api/customers/DEMO-000000",
    "/api/explain/global",
    "/api/segments",
    "/api/threshold/sweep",
    "/api/performance",
)


def _assert_nothing_is_served(client, real_ids) -> None:
    for path in DATA_PATHS:
        response = client.get(path)
        assert response.status_code == 503, f"{path}: {response.status_code}"
        assert response.json()["error"] == "demo_data_unavailable", path
        assert not _names_a_dataset_customer(response.text, real_ids)
    response = client.post("/api/explain", json={"customer_ids": ["DEMO-000000"]})
    assert response.status_code == 503
    ready = client.get("/api/ready")
    assert ready.status_code == 503 and ready.json()["dataset"] is False
    # Fail closed: with no published sweep to read it off, no count at the record's cut.
    metrics = _ok(client.get("/api/model-info"))["validation_metrics"]
    assert set(metrics) <= {"roc_auc", "average_precision", "brier"}
    assert client.get("/api/health").status_code == 200
    # The model alone still scores what a visitor types in.
    assert client.post("/api/predict", json=dict(schema.EXAMPLE_HIGH_RISK)).status_code == 200


def test_a_generator_that_fails_serves_nothing(rebuilt, real_ids, monkeypatch):
    from churnsense.api import state

    def broken() -> pd.DataFrame:
        raise RuntimeError("no demo customers")

    monkeypatch.setattr(state, "DEMO_GENERATOR", broken)
    _assert_nothing_is_served(rebuilt(), real_ids)


def test_a_book_that_cannot_be_prepared_serves_nothing(rebuilt, real_ids, monkeypatch):
    """Preparing the dashboard is inside the same net as checking its files: a failure
    after the last check is still 503, and is remembered rather than tried per request."""
    from churnsense.api import state

    calls = []

    def broken(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("no background")

    monkeypatch.setattr(state, "background_sample", broken)
    _assert_nothing_is_served(rebuilt(), real_ids)
    assert len(calls) == 1, "a failed build is not repeated by every request"


def test_other_customers_than_the_explained_ones_serve_nothing(rebuilt, real_ids, monkeypatch):
    """The cached explanations name the customers they are of: another book is refused."""
    from churnsense.api import state

    monkeypatch.setattr(state, "DEMO_GENERATOR", lambda: demo.demo_customers(seed=12345))
    _assert_nothing_is_served(rebuilt(), real_ids)


@pytest.mark.parametrize(
    "name", [evidence_module.EVIDENCE_FILE, evidence_module.DEMO_EXPLANATIONS_FILE]
)
def test_a_missing_artifact_serves_nothing(rebuilt, real_ids, name):
    (rebuilt.directory / name).unlink()
    _assert_nothing_is_served(rebuilt(), real_ids)


def test_a_file_without_a_sweep_serves_no_figure_at_any_cut_and_says_why(rebuilt, real_ids):
    """Where no cuts keep twenty of each between them, the file holds no sweep and no figure
    at the threshold; every page that needs one says why, and the rest still serves."""
    _edit(
        rebuilt.directory / evidence_module.EVIDENCE_FILE,
        lambda body: body.update(sweep_basis=None, validation=None),
    )
    client = rebuilt()
    for path in ("/api/threshold/sweep", "/api/threshold/sweep?success=0.5"):
        response = client.get(path)
        # Not the outcomes: only the sweep is withheld, and the code says which.
        assert response.status_code == 503 and response.json()["error"] == "sweep_withheld"
        assert score_disclosure.SWEEP_WITHHELD in response.text
    # The sentence every page that needs the sweep prints, in the service's own words.
    source = _ok(client.get("/api/model-info"))["data_source"]
    assert source["sweep_note"] == score_disclosure.SWEEP_WITHHELD
    performance = _ok(client.get("/api/performance"))
    assert performance["validation"] is None and performance["validation_evaluation"] is None
    assert performance["calibration"]["calibrated"] is None
    assert performance["calibration"]["uncalibrated"] is None
    assert _ok(client.get("/api/overview"))["campaign"] is None
    assert _ok(client.get("/api/customers"))["total"] == 2000
    assert client.get("/api/ready").status_code == 200


def test_the_public_demo_is_ready_only_with_its_front_end_and_serves_that_shell(
    public, frontend_dist, tmp_path, monkeypatch
):
    """Readiness still requires the built front end: the fixture supplies one, it does not
    waive the check. Without it the same service answers 503, naming what is missing."""
    from churnsense.api import spa

    _ok(public.get("/api/customers"))  # readiness reads memory: the book must be built
    answer = public.get("/api/ready")
    assert answer.status_code == 200 and answer.json()["frontend"] is True, answer.text
    assert public.get("/", headers={"Accept": "text/html"}).text == SHELL

    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path / "never-built")
    missing = public.get("/api/ready")
    assert missing.status_code == 503
    assert missing.json()["frontend"] is False
    rest = {k: v for k, v in missing.json().items() if k not in ("status", "frontend")}
    assert rest == {k: v for k, v in answer.json().items() if k not in ("status", "frontend")}


def test_readiness_is_of_the_book_the_configuration_now_asks_for(rebuilt, monkeypatch):
    """A book built from another generator, or for another deployment, is not ready.

    ``/api/ready`` reads memory only: switching the source without a rebuild must
    turn it unready rather than vouch for the book still held.
    """
    from churnsense.api import state

    def ready(client) -> bool:
        response = client.get("/api/ready")
        assert response.status_code == (200 if response.json()["dataset"] else 503)
        return response.json()["dataset"]

    client = rebuilt()
    _ok(client.get("/api/customers"))
    served = state.DEMO_GENERATOR
    assert ready(client) is True

    monkeypatch.setattr(state, "DEMO_GENERATOR", lambda: demo.demo_customers(n=50))
    assert ready(client) is False
    monkeypatch.setattr(state, "DEMO_GENERATOR", served)
    assert ready(client) is True, "nothing was rebuilt: the book held is the configured one again"

    monkeypatch.setattr(state, "DEPLOYMENT", DATASET)
    assert ready(client) is False


def _edit(path: Path, change) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    change(body)
    path.write_text(json.dumps(body), encoding="utf-8")


def _for_another_model(body: dict) -> None:
    body["identity"]["model_sha256"] = "0" * 64


def _for_another_dataset(body: dict) -> None:
    body["identity"]["data_sha256"] = "1" * 64


def _for_another_split(body: dict) -> None:
    body["identity"]["split_digest"] = "2" * 64


def _for_another_threshold(body: dict) -> None:
    body["identity"]["threshold"] = 0.99


def _with_a_row(body: dict) -> None:
    body["customers"] = [{"customerID": "1234-ABCDE", "tenure": 1}]


def _with_a_small_groups_rate(body: dict) -> None:
    body["observed"] = {"customers": 5, "churned": 2, "rate": 0.4}


def _with_another_format(body: dict) -> None:
    body["format"] = 999


@pytest.mark.parametrize(
    "change",
    [
        _for_another_model,
        _for_another_dataset,
        _for_another_split,
        _for_another_threshold,
        _with_a_row,
        _with_a_small_groups_rate,
        _with_another_format,
    ],
)
def test_evidence_that_is_not_this_models_or_is_malformed_serves_nothing(rebuilt, real_ids, change):
    _edit(rebuilt.directory / evidence_module.EVIDENCE_FILE, change)
    _assert_nothing_is_served(rebuilt(), real_ids)


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"format": 1}', "{" * 5000])
def test_unreadable_evidence_serves_nothing(rebuilt, real_ids, text):
    (rebuilt.directory / evidence_module.EVIDENCE_FILE).write_text(text, encoding="utf-8")
    _assert_nothing_is_served(rebuilt(), real_ids)


def _explanations_of_real_rows(body: dict) -> None:
    body["partition"] = "validation"


def _explanations_without_a_record_hash(body: dict) -> None:
    del body["records_sha256"]


def _explanations_of_another_model(body: dict) -> None:
    body["model_sha256"] = "0" * 64


@pytest.mark.parametrize(
    "change",
    [
        _explanations_of_real_rows,
        _explanations_without_a_record_hash,
        _explanations_of_another_model,
    ],
)
def test_explanations_that_are_not_of_the_demo_book_serve_nothing(rebuilt, real_ids, change):
    _edit(rebuilt.directory / evidence_module.DEMO_EXPLANATIONS_FILE, change)
    _assert_nothing_is_served(rebuilt(), real_ids)


def test_a_dataset_row_under_a_correct_header_serves_nothing(
    rebuilt, real_ids, public_frame, features, caplog
):
    """The header says synthetic and names the right book; one row is a dataset customer's.
    The service compares every row with the customers it lists, whatever the file says."""
    real = json.loads(public_frame[features].iloc[[0]].to_json(orient="records"))[0]

    def smuggle(body: dict) -> None:
        body["x"][7] = real

    _edit(rebuilt.directory / evidence_module.DEMO_EXPLANATIONS_FILE, smuggle)
    with caplog.at_level("ERROR", logger="churnsense.api.state"):
        _assert_nothing_is_served(rebuilt(), real_ids)
    assert "draw a row that is no demo customer's" in caplog.text


def test_explanations_of_other_features_serve_nothing(rebuilt, real_ids, caplog):
    """Attributions over fewer features than the model's are of another model: every row
    still matches a demo customer on the features it keeps, so only this check refuses it."""

    def drop_tenure(body: dict) -> None:
        at = body["features"].index("tenure")
        del body["features"][at]
        for row in body["values"]:
            del row[at]
        for row in body["x"]:
            del row["tenure"]
        body["importance"] = [i for i in body["importance"] if i.get("feature") != "tenure"]

    _edit(rebuilt.directory / evidence_module.DEMO_EXPLANATIONS_FILE, drop_tenure)
    with caplog.at_level("ERROR", logger="churnsense.api.state"):
        _assert_nothing_is_served(rebuilt(), real_ids)
    assert "of other features than the model's" in caplog.text


def test_the_datasets_own_cache_is_never_read_in_public_mode(rebuilt, real_rows, features):
    """``shap_global.json`` holds validation rows: present or not, it changes nothing."""
    before = _ok(rebuilt().get("/api/explain/global"))
    (rebuilt.directory / "shap_global.json").unlink()
    from churnsense.api import state

    state.reset()
    after = _ok(rebuilt().get("/api/explain/global"))
    assert after == before
    assert not set(_rows_in(after["x"], features)) & real_rows


def test_a_public_demo_beside_a_dataset_file_is_not_ready(rebuilt, real_ids, tmp_path, monkeypatch):
    """A mounted dataset has no business in a public container: the service stays down."""
    from churnsense.api import state

    present = tmp_path / "Telco-Customer-Churn.csv"
    present.write_text("customerID\n", encoding="utf-8")
    monkeypatch.setattr(state, "DEPLOYMENT", Deployment("public_demo", "synthetic"))
    monkeypatch.setattr(state, "RAW_DATA_FILE", present)
    _assert_nothing_is_served(rebuilt(), real_ids)


def test_a_public_demo_without_a_dataset_file_serves(rebuilt, tmp_path, monkeypatch):
    from churnsense.api import state

    monkeypatch.setattr(state, "DEPLOYMENT", Deployment("public_demo", "synthetic"))
    monkeypatch.setattr(state, "RAW_DATA_FILE", tmp_path / "absent.csv")
    client = rebuilt()
    assert _ok(client.get("/api/customers"))["total"] == demo.BOOK_SIZE
    assert client.get("/api/ready").json()["dataset"] is True


def test_the_dataset_backed_service_is_unchanged_by_the_demo_artifacts(
    artifacts, public_frame, real_ids, monkeypatch
):
    """With ``customer_records: dataset`` the book is the dataset's, as before."""
    from churnsense.api import main as api_main
    from churnsense.api import state

    monkeypatch.setattr(state, "DEPLOYMENT", DATASET)
    monkeypatch.setattr(
        state, "BOOK_LOADER", lambda model: score_book(public_frame, PUBLIC_SHA256, model)
    )
    state.reset()
    try:
        with TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK) as client:
            body = _ok(client.get("/api/customers?page_size=100"))
            assert {row["id"] for row in body["rows"]} <= real_ids
            assert (
                _ok(client.get("/api/model-info"))["data_source"]["customer_records"] == "dataset"
            )
            explained = _ok(
                client.post("/api/explain", json={"customer_ids": [body["rows"][0]["id"]]})
            )
            assert explained["background"] == "training"
    finally:
        monkeypatch.undo()
        state.reset()


# --- an outsider's arithmetic: nothing small can be solved for (E8) ---------------------------


def _cut(threshold: float, churners: int, stayers: int) -> list[audit.Fact]:
    return [
        audit.at_or_above(threshold, audit.CHURNED, churners),
        audit.at_or_above(threshold, audit.STAYED, stayers),
    ]


def _point(row: dict, churners: int) -> list[audit.Fact]:
    """A cut stated as flagged, precision and recall, as the campaign and sensitivity do."""
    tp = round(row["recall"] * churners)
    return _cut(row["threshold"], tp, row["flagged"] - tp)


def _score_facts(
    sweeps: list[dict],
    performance: dict,
    overview: dict,
    *,
    reliability: bool = True,
    at_half: bool = True,
    model_info: dict | None = None,
) -> list[audit.Fact]:
    """Every published count of customers split by score, as the API states it."""
    both, everyone = (audit.VALIDATION, audit.TRAINING), (audit.CHURNED, audit.STAYED)
    facts: list[audit.Fact] = []
    churners = sweeps[0]["churners"]
    for sweep in sweeps:
        facts += _cut(0.0, sweep["churners"], sweep["customers"] - sweep["churners"])
        for point in sweep["points"]:
            facts += _cut(point["threshold"], point["true_positives"], point["false_positives"])
        for name in ("recommended", "peak"):
            s = sweep[name]
            facts += _cut(s["threshold"], s["true_positives"], s["flagged"] - s["true_positives"])
        for row in sweep["sensitivity"]:
            facts += _point(row, churners)
    if performance["validation"] is not None:
        for point in performance["validation"]["points"]:
            facts += _cut(point["threshold"], point["true_positives"], point["false_positives"])
    measured = performance["validation_evaluation"]
    if measured is not None:
        m = measured["metrics"]
        facts += _cut(m["threshold"], m["tp"], m["fp"])
        for b in measured["reliability"] if reliability else []:
            facts.append(
                audit.between({audit.VALIDATION}, everyone, b["low"], b["high"], b["customers"])
            )
            if b["churned"] is not None:
                facts.append(
                    audit.between(
                        {audit.VALIDATION}, {audit.CHURNED}, b["low"], b["high"], b["churned"]
                    )
                )
    half = performance["calibration"]["calibrated"] if at_half else None
    if half is not None and half["precision"]:
        tp = round(half["recall"] * churners)
        facts += _cut(0.5, tp, round(tp / half["precision"]) - tp)
    for band in (*overview["bands"], *performance["bands"]):
        seen = band["observed"]
        facts.append(audit.between(both, everyone, band["min"], band["max"], seen["customers"]))
        if seen["churned"] is not None:
            facts.append(
                audit.between(both, {audit.CHURNED}, band["min"], band["max"], seen["churned"])
            )
    seen = overview["observed"]
    facts.append(audit.between(both, everyone, 0.0, 2.0, seen["customers"]))
    facts.append(audit.between(both, {audit.CHURNED}, 0.0, 2.0, seen["churned"]))
    for b in overview["histogram"]:
        if b["observed"] is not None:
            facts.append(
                audit.between(
                    {audit.VALIDATION}, everyone, b["low"], b["high"], b["observed"]["customers"]
                )
            )
            if b["observed"]["churned"] is not None:
                facts.append(
                    audit.between(
                        {audit.VALIDATION},
                        {audit.CHURNED},
                        b["low"],
                        b["high"],
                        b["observed"]["churned"],
                    )
                )
    if overview["campaign"] is not None:
        facts += _point(overview["campaign"], churners)
    record = (model_info or {}).get("validation_metrics", {})
    if "tp" in record:
        facts += _cut(record["threshold"], record["tp"], record["fp"])
    return facts


def _scores_of(book) -> dict:
    from churnsense.book import labelled

    out = {}
    for partition in (audit.VALIDATION, audit.TRAINING):
        rows = labelled(book.frame, partition, shown=book.outcomes)
        out[partition] = (
            rows[schema.TARGET].to_numpy().astype(int),
            rows["churn_probability"].to_numpy(),
        )
    return out


def _public_answers(client) -> tuple[list[dict], dict, dict, dict]:
    sweeps = [_ok(client.get(f"/api/threshold/sweep{query}")) for query in SWEEPS]
    return (
        sweeps,
        _ok(client.get("/api/performance")),
        _ok(client.get("/api/overview")),
        _ok(client.get("/api/model-info")),
    )


def test_no_interval_of_scores_can_be_solved_for_from_the_public_api(public, public_book):
    """Differences of cuts, a total less the shown groups, a band less a validation interval:
    every count an outsider can compute from the public answers keeps twenty of each."""
    sweeps, performance, overview, info = _public_answers(public)
    facts = _score_facts(sweeps, performance, overview, model_info=info)
    found, solved = audit.score_violations(facts, _scores_of(public_book))
    assert solved >= 20, "almost nothing can be solved for: the check is vacuous"
    assert found == []
    # The same arithmetic from the published figures alone, as an outsider has them.
    assert audit.score_violations(facts) == ([], solved)


def test_the_dataset_backed_answers_would_give_customers_away(dataset_answers, public_book):
    """The control: the same arithmetic on the dataset's own answers finds the old E8."""
    sweeps = [dataset_answers[f"/api/threshold/sweep{query}"] for query in SWEEPS]
    facts = _score_facts(
        sweeps,
        dataset_answers["/api/performance"],
        dataset_answers["/api/overview"],
        reliability=False,  # right-closed bins of their own; the sweep alone is enough here
        at_half=False,  # the model's record, of the dataset it was trained on, not this book
    )
    found, _ = audit.score_violations(facts, _scores_of(public_book))
    assert any("['validation']" in line for line in found), found[:5]
    outsider, _ = audit.score_violations(facts)
    assert any("['validation']" in line for line in outsider), outsider[:5]


def test_every_cut_a_public_answer_names_is_a_published_cut(public):
    sweeps, performance, overview, info = _public_answers(public)
    cuts = {point["threshold"] for point in sweeps[0]["points"]}
    named = (
        {info["validation_metrics"]["threshold"]} if "tp" in info["validation_metrics"] else set()
    )
    for sweep in sweeps:
        assert {p["threshold"] for p in sweep["points"]} == cuts
        named |= {sweep["recommended"]["threshold"], sweep["peak"]["threshold"]}
        named |= {sweep["band"]["low"], sweep["band"]["high"], sweep["shipped_threshold"]}
        named |= {row["threshold"] for row in sweep["sensitivity"]}
    named |= {p["threshold"] for p in performance["validation"]["points"]}
    measured = performance["validation_evaluation"]
    named.add(measured["metrics"]["threshold"])
    named |= {b["low"] for b in measured["reliability"]}
    named |= {b["high"] for b in measured["reliability"]} - {1.0}
    campaign = overview["campaign"]
    named |= {campaign["threshold"], campaign["peak_threshold"], campaign["recommended_threshold"]}
    named |= {point["threshold"] for point in campaign["curve"]}
    assert named <= cuts


def _segment_view(segments: list[dict]) -> dict[str, list]:
    view: dict[str, list] = {}
    for row in segments:
        cells = None
        if row["churners"] is not None:
            churners, flagged = row["churners"], row["flagged"]
            tp = round(row["recall"] * churners) if churners else 0
            fn = churners - tp
            cells = (tp, flagged - tp, fn, row["customers"] - flagged - fn)
        view.setdefault(row["dimension"], []).append((cells, row["customers"], row["flagged"]))
    return view


def _segment_truth(book, model, segments: list[dict]) -> dict[str, list]:
    rows, y, p = evidence_module.LiveEvidence(book, model, Path("absent")).validation_scores()
    cells = score_disclosure.segment_cells(y, p, rows, schema.VALIDATION_SEGMENTS, model.threshold)
    truth: dict[str, list] = {}
    for row in segments:
        truth.setdefault(row["dimension"], []).append(cells[row["dimension"], row["segment"]])
    return truth


@pytest.mark.parametrize("source", ["public", "dataset"])
def test_no_union_of_segments_can_be_solved_for_and_the_dataset_answers_could(
    public, dataset_answers, public_book, public_predictor, source
):
    performance = (
        _ok(public.get("/api/performance"))
        if source == "public"
        else dataset_answers["/api/performance"]
    )
    segments, m = (
        performance["validation_evaluation"]["segments"],
        performance["validation_evaluation"]["metrics"],
    )
    found, solved = audit.segment_violations(
        _segment_view(segments),
        (m["tp"], m["fp"], m["fn"], m["tn"]),
        _segment_truth(public_book, public_predictor, segments),
    )
    assert solved
    if source == "public":
        assert found == []
    else:
        assert found, "the control finds nothing: the segment check is vacuous"
