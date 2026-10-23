"""The dashboard endpoints under /api, end to end, on synthetic data only.

The service runs exactly as in production - real middleware, real routes, a
real trained model - against the synthetic customers in conftest.py. Nothing
reads the real dataset: the book loader is pointed at the synthetic frame.

Beyond each endpoint's own behaviour, three properties are checked across
every response the dashboard can get:

* no test-partition customer's outcome appears anywhere;
* no rate (or churn count) is given for fewer than 20 customers;
* every error has the ErrorResponse shape and names no file.
"""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from churnsense import analysis as an
from churnsense import insights
from churnsense.book import score_book
from churnsense.config import Config, RateLimit
from churnsense.data import schema
from churnsense.evaluation.threshold import recommend_threshold
from tests.conftest import SYNTHETIC_SHA256

LOCAL = "http://localhost"
LOOPBACK = ("127.0.0.1", 50000)


# --- the served environment --------------------------------------------------------


def _write_reports(directory, clean_frame, parts) -> None:
    """The tracked reports the dashboard reads, from the synthetic training rows."""
    train = clean_frame.loc[parts.X_train.index]
    an.association_ranking(train.drop(columns=[schema.ID_COLUMN])).to_csv(
        directory / "association_ranking.csv", index=False
    )
    pd.DataFrame(
        {
            "model": ["Logistic Regression", "Baseline (predicts the prior)"],
            "cv_pr_auc": [0.6, 0.3],
            "cv_std": [0.02, 0.0],
            "cv_se": [0.01, 0.0],
            "within_one_se": [True, False],
            "val_pr_auc": [0.6, 0.3],
            "val_roc_auc": [0.8, 0.5],
            "selected": [True, False],
        }
    ).to_csv(directory / "model_comparison.csv", index=False)


@pytest.fixture(scope="module")
def environment(dashboard_predictor, dashboard_parts, clean_frame, cfg: Config, tmp_path_factory):
    """An artifact directory with a SHAP cache, and a reports directory."""
    from churnsense.explainability.shap_explain import background_sample, explanation_cache

    root = tmp_path_factory.mktemp("api_dashboard")
    artifacts, reports = root / "artifacts", root / "reports"
    shutil.copytree(dashboard_predictor.source, artifacts)
    reports.mkdir()
    columns = dashboard_predictor.feature_columns
    cache = explanation_cache(
        dashboard_predictor.model,
        dashboard_predictor.meta,
        dashboard_parts.X_val[columns].head(12),
        background_sample(dashboard_parts.X_train[columns], cfg.random_seed),
        seed=cfg.random_seed,
        permutations=2,
    )
    (artifacts / "shap_global.json").write_text(json.dumps(cache))
    _write_reports(reports, clean_frame, dashboard_parts)
    return artifacts, reports


@pytest.fixture(scope="module")
def served(environment, clean_frame, monkeypatch_session) -> Iterator[TestClient]:
    from churnsense.api import main as api_main
    from churnsense.api import state

    artifacts, reports = environment

    def synthetic_book(model):
        return score_book(clean_frame, SYNTHETIC_SHA256, model)

    monkeypatch_session.setattr(state, "ARTIFACTS_DIR", artifacts)
    monkeypatch_session.setattr(state, "REPORTS_DIR", reports)
    monkeypatch_session.setattr(state, "BOOK_LOADER", synthetic_book)
    state.reset()
    with TestClient(api_main.app, base_url=LOCAL, client=LOOPBACK) as client:
        yield client
    state.reset()


@pytest.fixture(scope="module")
def model(served):
    from churnsense.api import state

    return state.get_predictor()


@pytest.fixture(scope="module")
def ids(clean_frame, dashboard_parts) -> dict[str, set[str]]:
    def of(part):
        return set(clean_frame.loc[part.index, schema.ID_COLUMN])

    return {
        "train": of(dashboard_parts.X_train),
        "validation": of(dashboard_parts.X_val),
        "test": of(dashboard_parts.X_test),
    }


@pytest.fixture(scope="module")
def shown(clean_frame, dashboard_parts) -> pd.DataFrame:
    """Training and validation customers: the only ones whose outcome may be shown."""
    return clean_frame.loc[dashboard_parts.X_train.index.union(dashboard_parts.X_val.index)]


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


# --- overview ---------------------------------------------------------------------------


def test_the_overview_counts_outcomes_on_training_and_validation_only(served, shown, clean_frame):
    body = _ok(served.get("/api/overview"))
    assert body["customers"] == len(clean_frame)
    observed = body["observed"]
    assert observed["customers"] == len(shown)
    assert observed["churned"] == int(shown[schema.TARGET].sum())
    assert observed["churned"] != int(clean_frame[schema.TARGET].sum()), "test is not sensitive"
    assert sum(b["customers"] for b in body["bands"]) == len(clean_frame)
    assert sum(h["customers"] for h in body["histogram"]) == len(clean_frame)
    assert sum(h["observed"]["customers"] for h in body["histogram"]) == len(shown)
    assert body["withheld_reason"] is None


def test_the_overview_says_what_its_attributions_were_computed_on(served):
    """ "Mean |SHAP| on 12 validation customers": the sample behind the ranking."""
    body = _ok(served.get("/api/overview"))
    assert body["importance_basis"] == {"partition": "validation", "customers": 12}
    assert body["importance"]


def test_the_overview_breakdowns_read_the_training_partition(served, clean_frame, dashboard_parts):
    body = _ok(served.get("/api/overview"))
    train = clean_frame.loc[dashboard_parts.X_train.index]
    assert [t["key"] for t in body["segments"]] == list(insights.OVERVIEW_SEGMENTS)
    contract = body["segments"][0]
    assert contract["partition_customers"] == len(train)
    expected = an.churn_rate_by(train, "Contract").set_index("Contract")
    for level in contract["levels"]:
        assert level["n"] == expected.loc[level["row"], "customers"]
    assert body["lifecycle"]["partition_customers"] == len(train)


def test_the_campaign_uses_the_shipped_threshold_and_the_repositorys_rule(
    served, model, clean_frame, dashboard_parts, cfg: Config
):
    campaign = _ok(served.get("/api/overview"))["campaign"]
    validation = clean_frame.loc[dashboard_parts.X_val.index]
    expected = recommend_threshold(
        validation[schema.TARGET],
        model.predict_frame(validation)["churn_probability"],
        validation["MonthlyCharges"],
        cfg.business,
    )
    assert campaign["threshold"] == model.threshold
    assert campaign["recommended_threshold"] == pytest.approx(expected.threshold)
    assert campaign["customers"] == len(validation)
    assert len(campaign["curve"]) == 101


# --- customers --------------------------------------------------------------------------


def test_a_page_of_customers_is_filtered_sorted_and_summarised(served, model, clean_frame):
    body = _ok(
        served.get(
            "/api/customers",
            params={"contract": "Month-to-month", "sort": "tenure", "dir": "asc", "page_size": 7},
        )
    )
    expected = clean_frame[clean_frame["Contract"] == "Month-to-month"]
    assert body["total"] == len(expected)
    assert body["pages"] == -(-len(expected) // 7)
    tenures = [r["tenure"] for r in body["rows"]]
    assert tenures == sorted(tenures) and len(tenures) == 7
    assert body["summary"]["customers"] == len(expected)
    assert sum(body["summary"]["histogram"]) == len(expected)


@pytest.mark.parametrize(
    "params",
    [
        {"bands": ["High", "Critical"]},
        {"flagged": "true"},
        {"partition": "validation"},
        {"tenure_min": 3, "tenure_max": 12},
        {"internet": "No", "payment": "Electronic check"},
        {"q": "demo-0001"},
    ],
)
def test_every_filter_narrows_the_book(served, params):
    body = _ok(served.get("/api/customers", params={**params, "page_size": 100}))
    everyone = _ok(served.get("/api/customers", params={"page_size": 1}))["total"]
    assert 0 < body["total"] < everyone
    for row in body["rows"]:
        if "bands" in params:
            assert row["risk_band"] in params["bands"]
        if "flagged" in params:
            assert row["flagged"] is True
        if "partition" in params:
            assert row["partition"] == "validation"
        if "tenure_min" in params:
            assert 3 <= row["tenure"] <= 12
        if "q" in params:
            assert "DEMO-0001" in row["id"]


def test_a_page_past_the_end_shows_the_last_page(served):
    body = _ok(served.get("/api/customers", params={"page": 99_999, "page_size": 100}))
    assert body["page"] == body["pages"] and body["rows"]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page_size": 101}, "page_size"),
        ({"page": 0}, "page"),
        ({"bands": "Severe"}, "bands"),
        ({"tenure_min": 12, "tenure_max": 3}, "tenure_min"),
        ({"q": "x' OR 1=1"}, "q"),
        ({"sort": "churn"}, "sort"),
        ({"contract": "Weekly"}, "contract"),
    ],
)
def test_invalid_listing_parameters_are_a_422_naming_the_parameter(served, params, field):
    response = served.get("/api/customers", params=params)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert body["detail"] == "The request parameters did not match the expected schema."
    assert any(problem.startswith(field) for problem in body["problems"]), body


def test_one_customer_matches_the_api_and_the_library(served, model, clean_frame):
    """Parity: the page, POST /api/predict and Predictor.predict_one give one number."""
    for customer_id in clean_frame[schema.ID_COLUMN].iloc[[0, 50, 150, 299]]:
        detail = _ok(served.get(f"/api/customers/{customer_id}"))
        predicted = _ok(served.post("/api/predict", json=detail["features"]))
        library = model.predict_one(detail["features"])
        assert detail["churn_probability"] == pytest.approx(
            predicted["churn_probability"], abs=1e-12
        )
        assert detail["churn_probability"] == pytest.approx(library.churn_probability, abs=1e-12)
        assert detail["risk_band"] == predicted["risk_band"] == library.risk_band
        assert detail["flagged"] == predicted["flagged"] == library.flagged


def test_a_test_customer_is_scored_but_their_outcome_is_withheld(served, ids):
    customer_id = sorted(ids["test"])[0]
    body = _ok(served.get(f"/api/customers/{customer_id}"))
    assert body["partition"] == "test"
    assert body["outcome"] == "withheld"
    assert 0 <= body["churn_probability"] <= 1


def test_an_unknown_customer_is_a_404(served):
    response = served.get("/api/customers/NOPE-00000")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_static_customer_paths_are_not_read_as_customer_ids(served):
    """/examples and /export must reach their own routes, not the {id} route."""
    assert "readme_example" in _ok(served.get("/api/customers/examples"))
    assert served.get("/api/customers/export").headers["content-type"].startswith("text/csv")


def test_the_export_is_spreadsheet_safe_and_withholds_test_outcomes(served, ids, model):
    response = served.get("/api/customers/export", params={"sort": "id", "dir": "asc"})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    frame = pd.read_csv(io.BytesIO(response.content), dtype=str)
    assert list(frame.columns) == [
        schema.ID_COLUMN,
        *model.feature_columns,
        "churn_probability",
        "risk_band",
        "flagged",
        "partition",
        "outcome",
    ]
    is_test = frame[schema.ID_COLUMN].isin(ids["test"])
    assert is_test.sum() == len(ids["test"])
    assert frame.loc[is_test, "outcome"].eq("withheld").all()
    assert set(frame.loc[~is_test, "outcome"]) <= {"churned", "stayed"}


def test_the_export_of_a_selection_lists_exactly_those_customers(served):
    chosen = ["DEMO-00003", "DEMO-00001"]
    response = served.get("/api/customers/export", params={"ids": chosen})
    frame = pd.read_csv(io.BytesIO(response.content), dtype=str)
    assert sorted(frame[schema.ID_COLUMN]) == sorted(chosen)


def test_the_example_customers_follow_the_rule_and_are_validation_customers(served, ids):
    body = _ok(served.get("/api/customers/examples"))
    assert body["readme_example"] == dict(schema.EXAMPLE_HIGH_RISK)
    cases = list(insights.EXAMPLE_CASES)
    for customer in body["customers"]:
        assert customer["id"] in ids["validation"]
        assert (customer["risk_band"], customer["flagged"]) in cases


# --- explanations ------------------------------------------------------------------------


def test_explanations_add_up_to_the_served_probability(served, clean_frame):
    chosen = list(clean_frame[schema.ID_COLUMN].iloc[[2, 40, 210]])
    body = _ok(served.post("/api/explain", json={"customer_ids": chosen}))
    assert [e["customer_id"] for e in body["explanations"]] == chosen
    for explanation in body["explanations"]:
        detail = _ok(served.get(f"/api/customers/{explanation['customer_id']}"))
        contributions = explanation["contributions"]
        assert len(contributions) == 18
        total = explanation["base_value"] + sum(c["shap_value"] for c in contributions)
        assert total == pytest.approx(explanation["churn_probability"], abs=1e-9)
        assert explanation["churn_probability"] == pytest.approx(
            detail["churn_probability"], abs=1e-12
        )
        magnitudes = [abs(c["shap_value"]) for c in contributions]
        assert magnitudes == sorted(magnitudes, reverse=True)
    assert body["background_customers"] == 100


def test_a_record_outside_the_book_is_explained_the_same_way(served):
    record = dict(schema.EXAMPLE_HIGH_RISK)
    body = _ok(served.post("/api/explain", json={"record": record}))
    predicted = _ok(served.post("/api/predict", json=record))
    (explanation,) = body["explanations"]
    assert explanation["customer_id"] is None
    assert explanation["churn_probability"] == pytest.approx(
        predicted["churn_probability"], abs=1e-12
    )


def test_an_explanation_does_not_depend_on_who_is_explained_beside_it(served, clean_frame):
    """Each customer is explained alone, with the fixed seed: alone or in a
    batch, before or after another customer, the contributions are the same."""
    from churnsense.api import state

    a, b = clean_frame[schema.ID_COLUMN].iloc[[5, 6]]
    alone = _ok(served.post("/api/explain", json={"customer_ids": [b]}))["explanations"][0]
    state.get_dashboard()._explanations.clear()  # recompute, rather than reuse b's
    paired = _ok(served.post("/api/explain", json={"customer_ids": [a, b]}))["explanations"][1]
    # The same orderings give the same contributions, up to the last bits of a
    # float sum; another ordering would move them by ~1e-3.
    assert [c["feature"] for c in alone["contributions"]] == [
        c["feature"] for c in paired["contributions"]
    ]
    assert [c["shap_value"] for c in alone["contributions"]] == pytest.approx(
        [c["shap_value"] for c in paired["contributions"]], abs=1e-12
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"customer_ids": ["DEMO-00001"], "record": dict(schema.EXAMPLE_HIGH_RISK)},
        {"customer_ids": [f"DEMO-{i:05d}" for i in range(9)]},
        {"customer_ids": []},
        {"customer_ids": ["../etc/passwd"]},
        {"customer_ids": ["DEMO-00001", "DEMO-00001"]},
    ],
)
def test_an_invalid_explain_request_is_a_422(served, body):
    response = served.post("/api/explain", json=body)
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_explaining_an_unknown_customer_is_a_404(served):
    response = served.post("/api/explain", json={"customer_ids": ["NOPE-00000"]})
    assert response.status_code == 404


def test_parallel_explanations_equal_sequential_ones(served, clean_frame, monkeypatch):
    """shap seeds numpy's global random state: without the lock, explanations
    computed at once in the thread pool drew each other's orderings."""
    from churnsense.api import state

    chosen = list(clean_frame[schema.ID_COLUMN].iloc[[11, 12, 13, 14]])
    dashboard = state.get_dashboard()
    sequential = {cid: _explain_uncached(dashboard, cid) for cid in chosen}
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel = dict(
            zip(chosen, pool.map(lambda c: _explain_uncached(dashboard, c), chosen), strict=True)
        )
    # Equal orderings give equal contributions; only the last bits of a float
    # sum may differ between threads. Interleaved orderings differ by ~1e-3.
    for customer_id, contributions in sequential.items():
        assert [f for f, _ in parallel[customer_id]] == [f for f, _ in contributions]
        assert [v for _, v in parallel[customer_id]] == pytest.approx(
            [v for _, v in contributions], abs=1e-12
        )


def _explain_uncached(dashboard, customer_id: str):
    from churnsense.explainability.shap_explain import PERMUTATIONS, explain_customer

    row = dashboard.customer(customer_id)
    record = dashboard.model.model_inputs(row.to_frame().T)
    result = explain_customer(
        dashboard.model.model,
        record,
        row=0,
        top_k=record.shape[1],
        background=dashboard.background,
        seed=dashboard.config.random_seed,
        permutations=PERMUTATIONS,
    )
    return [(c.feature, c.shap_value) for c in result.contributions]


def test_the_global_explanation_is_served_from_the_cache(served, environment):
    body = _ok(served.get("/api/explain/global"))
    assert body["n_explained"] == len(body["values"]) == len(body["x"]) == 12
    assert all(len(row) == len(body["features"]) for row in body["values"])
    assert body["max_additivity_gap"] < 1e-9
    assert set(body["association_rank"]) <= set(body["features"])
    assert -1 <= body["total_charges_r"] <= 1


# --- patterns -----------------------------------------------------------------------------


def test_a_cross_tab_reports_every_combination_and_suppresses_small_cells(
    served, clean_frame, dashboard_parts
):
    body = _ok(served.get("/api/segments", params={"rows": "Contract", "cols": "tenure"}))
    assert len(body["cells"]) == 3 * 6
    train = clean_frame.loc[dashboard_parts.X_train.index]
    assert body["partition_customers"] == len(train)
    assert sum(c["n"] for c in body["cells"]) == len(train)
    for cell in body["cells"]:
        if cell["n"] < 20:
            assert cell == {
                "row": cell["row"],
                "column": cell["column"],
                "suppressed": True,
                "n": cell["n"],
            }
        else:
            assert cell["suppressed"] is False and 0 <= cell["rate"] <= 1
    rates = [c["rate"] for c in body["ranked"]]
    assert rates == sorted(rates, reverse=True) and len(rates) <= 7


@pytest.mark.parametrize(
    "params",
    [{"rows": "Contract", "cols": "Contract"}, {"rows": "gender"}, {"rows": "Churn"}],
)
def test_a_cross_tab_of_unknown_or_repeated_dimensions_is_a_422(served, params):
    assert served.get("/api/segments", params=params).status_code == 422


# --- threshold economics --------------------------------------------------------------------


def test_the_sweep_follows_the_assumptions_it_is_given(served):
    default = _ok(served.get("/api/threshold/sweep"))
    cheap = _ok(served.get("/api/threshold/sweep", params={"cost": 5, "success": 0.6}))
    assert len(default["points"]) == 101
    assert [s["offer_success_rate"] for s in default["sensitivity"]] == [0.1, 0.2, 0.3, 0.4, 0.5]
    assert cheap["recommended"]["threshold"] <= default["recommended"]["threshold"]
    assert "SIMULATED" in default["disclaimer"]
    band = default["band"]
    assert band["low"] <= default["recommended"]["threshold"] == band["high"]


@pytest.mark.parametrize(
    "params", [{"success": 1.5}, {"cost": -1}, {"horizon": 0}, {"margin": "x"}, {"horizon": 2.5}]
)
def test_out_of_range_assumptions_are_a_422(served, params):
    assert served.get("/api/threshold/sweep", params=params).status_code == 422


# --- performance ------------------------------------------------------------------------------


def test_performance_without_a_recorded_evaluation_says_so(served):
    body = _ok(served.get("/api/performance"))
    assert body["test"] is None
    assert "make evaluate CONFIRM=yes" in body["test_reason"]
    assert body["candidates"][0]["model"] == "Logistic Regression"
    assert body["validation"]["customers"] > 0


def test_a_recorded_evaluation_is_shown_only_for_the_artifact_it_describes(
    served, environment, model, dashboard_parts, cfg: Config
):
    from churnsense.evaluation.report import final_record, run_final_evaluation

    _, reports = environment
    evaluation = run_final_evaluation(model.model, dashboard_parts, model.threshold, cfg)
    record = final_record(evaluation, model.meta, "2026-10-21T00:00:00+00:00")
    path = reports / "final_evaluation.json"
    try:
        path.write_text(json.dumps(record, allow_nan=False))
        body = _ok(served.get("/api/performance"))
        assert body["test"]["metrics"]["n"] == len(dashboard_parts.X_test)
        info = _ok(served.get("/api/model-info"))
        assert info["test_evaluation"]["recall"] == record["metrics"]["recall"]

        path.write_text(json.dumps({**record, "model_sha256": "0" * 64}))
        assert _ok(served.get("/api/performance"))["test"] is None
        assert _ok(served.get("/api/model-info"))["test_evaluation"] is None
    finally:
        path.unlink(missing_ok=True)


# --- batch scoring ----------------------------------------------------------------------------


def _upload(client, frame: pd.DataFrame, path: str = "/api/batch/score"):
    payload = frame.to_csv(index=False).encode()
    return client.post(path, files={"file": ("customers.csv", payload, "text/csv")})


def test_a_batch_is_scored_by_the_served_model(served, model, raw_frame):
    upload = raw_frame.head(30)
    body = _ok(_upload(served, upload))
    expected = model.predict_frame(_ok_frame(upload))
    assert [r["row"] for r in body["rows"]] == list(range(30))
    for row, p in zip(body["rows"], expected["churn_probability"], strict=True):
        assert row["churn_probability"] == pytest.approx(p, abs=1e-12)
    assert body["summary"]["rows"] == 30
    assert sum(b["customers"] for b in body["summary"]["bands"]) == 30


def _ok_frame(raw: pd.DataFrame) -> pd.DataFrame:
    from churnsense.models.predict import validate_upload

    return validate_upload(raw.to_csv(index=False).encode())


def test_a_broken_batch_lists_every_problem(served):
    broken = pd.DataFrame({"tenure": ["abc"], "Contract": ["Weekly"]})
    response = _upload(served, broken)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_input" and len(body["problems"]) >= 2


# --- every response: no test outcome, no small-cell rate, a clean error shape ------------------------


@pytest.fixture
def recorded(served, environment, model, dashboard_parts, cfg: Config):
    """A recorded test evaluation of the served artifact, for the length of one test."""
    from churnsense.evaluation.report import final_record, run_final_evaluation

    _, reports = environment
    evaluation = run_final_evaluation(model.model, dashboard_parts, model.threshold, cfg)
    record = final_record(evaluation, model.meta, "2026-10-21T00:00:00+00:00")
    path = reports / "final_evaluation.json"
    path.write_text(json.dumps(record, allow_nan=False))
    yield record
    path.unlink(missing_ok=True)


#: Where the published final evaluation's aggregates appear. They are the
#: figures reports/final_evaluation.md prints - a recorded report, not a view of
#: test rows - and are the one documented exception to "no test figure".
RECORDED_TEST_BLOCKS = {
    ("performance", "test"),
    ("model-info", "test_evaluation"),
    ("model-info", "test_metrics"),
}


def _every_dashboard_response(client, ids, raw_frame) -> Iterator[tuple[str, object]]:
    yield "overview", client.get("/api/overview").json()
    for partition in ("train", "validation", "test"):
        page = client.get("/api/customers", params={"partition": partition, "page_size": 100})
        yield f"customers {partition}", page.json()
    for customer_id in sorted(ids["test"])[:15]:
        yield customer_id, client.get(f"/api/customers/{customer_id}").json()
    chosen = sorted(ids["test"])[:100] + sorted(ids["train"])[:100]
    selection = client.get("/api/customers", params={"ids": chosen, "page_size": 100})
    yield "customers by ids", selection.json()
    yield "examples", client.get("/api/customers/examples").json()
    yield "performance", client.get("/api/performance").json()
    yield "sweep", client.get("/api/threshold/sweep").json()
    yield "global", client.get("/api/explain/global").json()
    yield "model-info", client.get("/api/model-info").json()
    for rows in insights.DIMENSIONS:
        yield f"segments {rows}", client.get("/api/segments", params={"rows": rows}).json()
    yield (
        "segments x",
        client.get("/api/segments", params={"rows": "monthly", "cols": "tenure"}).json(),
    )
    lower = raw_frame.assign(customerID=raw_frame["customerID"].str.lower())
    for name, upload in (
        ("batch", raw_frame),
        ("batch without ids", raw_frame.drop(columns=["customerID"])),
        ("batch lower-cased ids", lower),
    ):
        yield name, _upload(client, upload).json()


def _objects(node, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict]]:
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _objects(value, (*path, key))
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value, path)


def _scanned(client, ids, raw_frame) -> Iterator[tuple[str, dict]]:
    """Every object of every response, the recorded test report excepted."""
    for name, body in _every_dashboard_response(client, ids, raw_frame):
        for path, obj in _objects(body):
            if not any((name, key) in RECORDED_TEST_BLOCKS for key in path[:1]):
                yield name, obj


def test_no_response_carries_a_test_customers_outcome(served, ids, raw_frame, recorded):
    test_ids = ids["test"] | {i.lower() for i in ids["test"]}
    seen_test = labelled_test = 0
    for name, obj in _scanned(served, ids, raw_frame):
        if obj.get("id") in test_ids:
            seen_test += 1
            assert obj["outcome"] == "withheld", name
        # A batch row of a held-out customer (rows without an ID are matched on
        # their inputs: test_a_batch_never_compares_or_returns_held_out_labels).
        if "label" in obj and "features" in obj and obj["customer_id"] in test_ids:
            labelled_test += obj["label"] is not None
    assert seen_test, "the scan never met a test customer"
    assert labelled_test == 0, "a batch echoed a held-out customer's label"


#: Keys that state an outcome rate or count of a group.
_OUTCOME_KEYS = ("rate", "churned", "churn_rate", "churners", "precision", "recall")


def test_no_response_reports_an_outcome_rate_for_fewer_than_twenty_customers(
    served, ids, raw_frame, recorded
):
    small = 0
    for name, obj in _scanned(served, ids, raw_frame):
        size = obj.get("customers", obj.get("n"))
        if isinstance(size, int) and size < 20 and any(k in obj for k in _OUTCOME_KEYS):
            small += 1
            assert all(obj.get(k) is None for k in _OUTCOME_KEYS), (name, obj)
    assert small, "the scan never met a small group"


def test_the_recorded_test_segments_lose_their_rates_below_twenty(served, recorded):
    segments = _ok(served.get("/api/performance"))["test"]["segments"]
    for segment in segments:
        if segment["customers"] < 20:
            assert segment["churn_rate"] is None and segment["recall"] is None


def test_a_small_selection_shows_no_observed_rate(served, ids):
    customer_id = sorted(ids["train"])[0]
    summary = _ok(served.get("/api/customers", params={"q": customer_id}))["summary"]
    assert summary["observed"] == {"customers": 1, "churned": None, "rate": None}


@pytest.mark.parametrize(
    "variant",
    [
        "as is",
        "without the id column",
        "with lower-cased ids",
        "with padded ids",
        "with renamed ids",
    ],
)
def test_a_batch_never_compares_or_returns_held_out_labels(served, raw_frame, ids, shown, variant):
    """Renaming, re-casing or dropping the ID does not hide a test customer:
    the rows are matched on the inputs the model scores as well."""
    upload = {
        "as is": raw_frame,
        "without the id column": raw_frame.drop(columns=["customerID"]),
        "with lower-cased ids": raw_frame.assign(customerID=raw_frame["customerID"].str.lower()),
        "with padded ids": raw_frame.assign(customerID=" " + raw_frame["customerID"] + " "),
        "with renamed ids": raw_frame.assign(customerID="X" + raw_frame["customerID"]),
    }[variant]
    body = _ok(_upload(served, upload))
    labels = body["summary"]["labels"]
    assert labels["excluded_test"] >= len(ids["test"])
    assert labels["labelled"] <= len(shown)
    test_rows = {i for i, cid in enumerate(raw_frame["customerID"]) if cid in ids["test"]}
    for row in body["rows"]:
        if row["row"] in test_rows:
            assert row["label"] is None


def test_untrusted_partitions_withhold_every_outcome_everywhere(
    environment, clean_frame, monkeypatch, served
):
    """A book whose provenance fails shows scores, and no outcome anywhere."""
    from churnsense.api import state

    def untrusted(model):
        broken = replace(model, meta={**model.meta, "split_digest": "0" * 64})
        return score_book(clean_frame, SYNTHETIC_SHA256, broken)

    monkeypatch.setattr(state, "BOOK_LOADER", untrusted)
    overview = _ok(served.get("/api/overview"))
    assert overview["withheld_reason"] and overview["observed"] is None
    assert overview["campaign"] is None and overview["segments"] == []
    page = _ok(served.get("/api/customers", params={"page_size": 100}))
    assert {r["outcome"] for r in page["rows"]} == {"withheld"}
    assert {r["partition"] for r in page["rows"]} == {"unknown"}
    for path in ("/api/segments", "/api/threshold/sweep"):
        response = served.get(path)
        assert response.status_code == 503 and response.json()["error"] == "outcomes_withheld"
    response = served.post("/api/explain", json={"customer_ids": ["DEMO-00001"]})
    assert response.json()["error"] == "explanations_unavailable"
    assert _ok(served.get("/api/model-info"))["provenance"]["verified"] is False


def test_without_a_dataset_the_dashboard_says_so_and_scoring_still_works(served, monkeypatch):
    from churnsense.api import state
    from churnsense.exceptions import DataError

    def missing(_model):
        raise DataError("no file at /Users/someone/data/raw")

    monkeypatch.setattr(state, "BOOK_LOADER", missing)
    response = served.get("/api/overview")
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "dataset_unavailable" and "/Users" not in response.text
    assert _ok(served.get("/api/model-info"))["provenance"] is None
    assert served.post("/api/predict", json=dict(schema.EXAMPLE_HIGH_RISK)).status_code == 200


def test_every_error_has_the_documented_shape_and_no_path(served):
    responses = [
        served.get("/api/customers", params={"page_size": 0}),
        served.get("/api/customers/NOPE-00000"),
        served.post("/api/explain", json={}),
        served.get("/api/segments", params={"rows": "nope"}),
        served.get("/api/nope"),
        served.delete("/api/overview"),
    ]
    for response in responses:
        assert response.status_code >= 400
        assert set(response.json()) == {"error", "detail", "problems"}
        assert "/Users/" not in response.text and "Traceback" not in response.text


# --- the front end's own routes ---------------------------------------------------------------------


@pytest.fixture
def built(tmp_path, monkeypatch):
    from churnsense.api import spa

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>ChurnSense AI</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "assets" / "notes.txt").write_text("not a bundle file")
    (tmp_path / "secret.txt").write_text("outside the assets folder")
    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path)
    return tmp_path


@pytest.mark.parametrize("path", ["/", "/explorer", "/customer", "/batch"])
def test_every_page_route_serves_the_app_shell(served, built, path):
    response = served.get(path, params={"id": "DEMO-00001"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_bundle_files_are_served_immutable_and_only_from_assets(served, built):
    ok = served.get("/assets/index-abc123.js")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/javascript")
    assert "immutable" in ok.headers["cache-control"]
    for name in ("notes.txt", "..%2Fsecret.txt", ".hidden.js", "missing.js"):
        assert served.get(f"/assets/{name}").status_code == 404, name


def test_without_a_build_the_pages_say_how_to_make_one(served, tmp_path, monkeypatch):
    from churnsense.api import spa

    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path / "missing")
    response = served.get("/explorer")
    assert response.status_code == 404
    assert response.json()["error"] == "frontend_not_built"


def test_page_routes_keep_405_and_json_404(served, built):
    assert served.post("/").status_code == 405
    assert served.get("/no-such-page").json()["error"] == "not_found"


# --- security layers -----------------------------------------------------------------------------------


def test_every_response_carries_the_hardening_headers(served):
    for response in (
        served.get("/api/overview"),
        served.get("/api/customers/NOPE-00000"),
        served.get("/api/customers", params={"page": 0}),
    ):
        headers = response.headers
        assert headers["content-security-policy"].startswith("default-src 'self'")
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "camera=()" in headers["permissions-policy"]
        assert headers["cross-origin-opener-policy"] == "same-origin"
        assert "strict-transport-security" not in headers  # plain http


def test_the_docs_pages_get_their_own_narrow_policy(served):
    policy = served.get("/docs").headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in policy and "frame-ancestors 'none'" in policy
    assert "cdn.jsdelivr.net" not in served.get("/api/overview").headers["content-security-policy"]


def test_https_requests_get_hsts(served):
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url="https://localhost", client=LOOPBACK) as client:
        assert "max-age" in client.get("/health").headers["strict-transport-security"]


@pytest.mark.parametrize("peer", [("10.0.0.5", 1), ("192.168.1.9", 2), ("testclient", 3)])
def test_a_peer_that_is_not_this_machine_is_refused(peer):
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url=LOCAL, client=peer) as client:
        response = client.get("/health")
    assert response.status_code == 403
    assert response.json()["error"] == "loopback_only"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "peer", [("127.0.0.1", 1), ("127.8.9.10", 2), ("::1", 3), ("::ffff:127.0.0.1", 4)]
)
def test_every_loopback_peer_is_answered(peer):
    from churnsense.api import main as api_main

    with TestClient(api_main.app, base_url=LOCAL, client=peer) as client:
        assert client.get("/health").status_code == 200


def test_a_request_with_no_peer_is_refused():
    import asyncio

    from churnsense.api.middleware import LoopbackOnly

    sent: list[dict] = []

    async def app(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("the request reached the app")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "client": None}
    asyncio.run(LoopbackOnly(app)(scope, receive, send))
    assert sent[0]["status"] == 403


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_an_api_request_another_site_triggered_is_refused(served, site):
    """A page on another localhost port is 'same-site': it must not read or export."""
    response = served.get("/api/customers/export", headers={"sec-fetch-site": site})
    assert response.status_code == 403
    assert response.json()["error"] == "cross_site"


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_an_api_request_from_this_site_or_the_address_bar_is_served(served, site):
    assert served.get("/api/overview", headers={"sec-fetch-site": site}).status_code == 200


def test_a_post_from_another_port_is_refused(served):
    response = served.post(
        "/api/explain",
        json={"record": dict(schema.EXAMPLE_HIGH_RISK)},
        headers={"origin": "http://localhost:8501"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "cross_origin"


def test_the_expensive_endpoints_are_rate_limited(environment, clean_frame, monkeypatch, raw_frame):
    """A budget per client: past it, 429 with Retry-After; the legacy batch path
    spends from the same bucket as /api, so the alias is no way around it."""
    from churnsense.api import main as api_main
    from churnsense.api import middleware

    monkeypatch.setattr(
        middleware,
        "RATE_LIMITS",
        {
            "explain": RateLimit(burst=2, per_seconds=3600),
            "batch": RateLimit(burst=1, per_seconds=3600),
        },
    )
    with TestClient(api_main.app, base_url=LOCAL, client=("127.0.0.77", 1)) as client:
        record = {"record": dict(schema.EXAMPLE_HIGH_RISK)}
        assert [client.post("/api/explain", json=record).status_code for _ in range(3)][-1] == 429
        refused = client.post("/api/explain", json=record)
        assert refused.json()["error"] == "rate_limited"
        assert int(refused.headers["retry-after"]) > 0
        assert _upload(client, raw_frame.head(5)).status_code == 200
        assert _upload(client, raw_frame.head(5), "/predict/batch").status_code == 429
        assert client.get("/api/overview").status_code == 200, "cheap reads are never throttled"


def test_the_openapi_schema_declares_the_dashboard_errors_and_dimensions(served):
    schema_ = _ok(served.get("/openapi.json"))
    paths = schema_["paths"]
    for path in ("/api/overview", "/api/customers", "/api/segments", "/api/explain"):
        operation = next(iter(paths[path].values()))
        assert {"403", "422", "503"} <= set(operation["responses"]), path
    rows = next(p for p in paths["/api/segments"]["get"]["parameters"] if p["name"] == "rows")
    assert set(rows["schema"]["enum"]) == set(insights.DIMENSIONS)
    for legacy in ("/health", "/model-info", "/predict", "/predict/batch"):
        assert legacy in paths and f"/api{legacy}" in paths
    assert not any(p in paths for p in ("/", "/explorer", "/assets/{name}"))


def test_an_explanation_that_does_not_add_up_is_never_served(served, monkeypatch):
    """A broken explanation is an internal error - a generic 500 - not a figure on a page."""
    from churnsense.api import main as api_main
    from churnsense.api import state
    from churnsense.explainability.shap_explain import CustomerExplanation, FeatureContribution

    def off_by_a_point(self, record):
        return CustomerExplanation(
            probability=0.5,
            base_value=0.3,
            contributions=[FeatureContribution("tenure", "Tenure (months)", 3, 0.21)],
        )

    monkeypatch.setattr(state.Dashboard, "explain", off_by_a_point)
    with TestClient(
        api_main.app, base_url=LOCAL, client=LOOPBACK, raise_server_exceptions=False
    ) as client:
        response = client.post("/api/explain", json={"record": dict(schema.EXAMPLE_HIGH_RISK)})
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert response.headers["content-security-policy"].startswith("default-src 'self'")


# --- broken files and busy servers degrade, they do not take a page down ---------------------


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        "not json",
        '{"model_sha256": "%s", "evaluated_at": "x"}',
        '{"model_sha256": "%s", "ece": NaN}',
    ],
)
def test_a_malformed_evaluation_record_reads_as_not_recorded(served, environment, model, text):
    _, reports = environment
    path = reports / "final_evaluation.json"
    sha = model.meta["model_sha256"]
    try:
        path.write_text(text.replace("%s", sha))
        for route in ("/model-info", "/api/model-info"):
            assert _ok(served.get(route))["test_evaluation"] is None
        assert _ok(served.get("/api/performance"))["test"] is None
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("damage", ["not json", "[1, 2]", "mismatch"])
def test_a_damaged_shap_cache_is_ignored_not_fatal(served, environment, damage):
    artifacts, _ = environment
    path = artifacts / "shap_global.json"
    original = path.read_text()
    try:
        if damage == "mismatch":
            cache = json.loads(original)
            cache["values"] = [row[:-1] for row in cache["values"]]
            path.write_text(json.dumps(cache))
        else:
            path.write_text(damage)
        overview = _ok(served.get("/api/overview"))
        if damage != "mismatch":
            assert overview["importance"] is None
            assert overview["importance_basis"] is None
        response = served.get("/api/explain/global")
        assert response.status_code == 503
        assert response.json()["error"] == "explanations_not_cached"
    finally:
        path.write_text(original)


@pytest.mark.parametrize("path", ["/api/customers", "/api/customers/export"])
def test_a_listing_takes_ids_or_filters_not_both(served, path):
    response = served.get(path, params={"ids": ["DEMO-00001"], "flagged": "true"})
    assert response.status_code == 422
    assert any(p.startswith("ids") for p in response.json()["problems"])


def test_a_listing_by_ids_shows_exactly_those_customers_and_totals_them(served):
    """The overview's selection bar reads its revenue at risk from here, not the browser."""
    chosen = ["DEMO-00003", "DEMO-00001", "DEMO-00002"]
    body = _ok(served.get("/api/customers", params={"ids": chosen, "page_size": 100}))
    assert sorted(row["id"] for row in body["rows"]) == sorted(chosen)
    assert body["total"] == body["summary"]["customers"] == 3
    assert body["summary"]["at_risk"] == pytest.approx(sum(r["at_risk"] for r in body["rows"]))


def test_a_selection_of_test_customers_reports_no_outcome(served, ids):
    chosen = sorted(ids["test"])[:25]
    body = _ok(served.get("/api/customers", params={"ids": chosen, "page_size": 100}))
    assert {row["outcome"] for row in body["rows"]} == {"withheld"}
    assert body["summary"]["observed"] is None


@pytest.mark.parametrize(
    "chosen",
    [
        [f"DEMO-{i:05d}" for i in range(201)],
        ["../etc/passwd"],
    ],
    ids=["too many", "not an id"],
)
def test_a_listing_refuses_ids_it_cannot_take(served, chosen):
    response = served.get("/api/customers", params={"ids": chosen})
    assert response.status_code == 422
    assert any(p.startswith("ids") for p in response.json()["problems"])


def test_explanations_turn_requests_away_when_every_slot_is_busy(served, monkeypatch):
    import threading

    from churnsense.api import state

    monkeypatch.setattr(state, "_explain_slots", threading.BoundedSemaphore(1))
    assert state._explain_slots.acquire(blocking=False)
    try:
        response = served.post("/api/explain", json={"record": dict(schema.EXAMPLE_HIGH_RISK)})
    finally:
        state._explain_slots.release()
    assert response.status_code == 503
    assert response.json()["error"] == "explanations_busy"
    assert response.headers["retry-after"] == "2"


def test_a_missing_dataset_is_not_reloaded_or_logged_on_every_request(served, monkeypatch, caplog):
    from churnsense.api import state
    from churnsense.exceptions import DataError

    calls = []

    def missing(_model):
        calls.append(1)
        raise DataError("no dataset")

    monkeypatch.setattr(state, "BOOK_LOADER", missing)
    for _ in range(5):
        served.get("/api/model-info")
        served.get("/api/overview")
    assert len(calls) == 1
    assert caplog.text.count("dataset unavailable") == 1
