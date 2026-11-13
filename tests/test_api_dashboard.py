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
import itertools
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
from churnsense.api.dashboard import TEST_WITHHELD_REASON
from churnsense.book import score_book
from churnsense.config import Config, RateLimit
from churnsense.data import schema
from churnsense.evaluation.metrics import wilson_interval
from churnsense.evaluation.threshold import recommend_threshold
from tests.conftest import SWEEP_ASSUMPTIONS, SYNTHETIC_SHA256
from tests.disclosure import fixed_groups, grid_equations, line_equation

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
        "status",
    ]
    is_test = frame[schema.ID_COLUMN].isin(ids["test"])
    assert is_test.sum() == len(ids["test"])
    assert frame.loc[is_test, "outcome"].eq("withheld").all()
    assert frame.loc[is_test, "status"].eq("unknown").all()
    assert set(frame.loc[~is_test, "outcome"]) <= {"churned", "stayed"}
    assert set(frame.loc[~is_test, "status"]) <= {"churned", "active"}


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


def test_the_global_explanation_summarises_each_feature_in_feature_order(served):
    body = _ok(served.get("/api/explain/global"))
    assert body["dependence_min_customers"] == 5
    assert [d["feature"] for d in body["dependence"]] == body["features"]
    for summary in body["dependence"]:
        if summary["feature"] in schema.NUMERIC_COLUMNS:
            assert summary["kind"] == "numeric"
            assert summary["bins"]
        else:
            assert summary["kind"] == "categorical"
            assert [lv["category"] for lv in summary["levels"]] == list(
                schema.ALLOWED_CATEGORIES[summary["feature"]]
            )


def test_the_dependence_summaries_match_the_served_matrix(served):
    """Recomputed here from the served (rounded) values: each mean within rounding."""
    body = _ok(served.get("/api/explain/global"))
    floor = body["dependence_min_customers"]

    def expected(members: list[float]) -> float | None:
        return sum(members) / len(members) if len(members) >= floor else None

    def check(group: dict, members: list[float]) -> None:
        assert group["customers"] == len(members)
        mean = expected(members)
        if mean is None:
            assert group["mean"] is None
        else:
            assert group["mean"] == pytest.approx(mean, abs=1.5e-6)

    for j, summary in enumerate(body["dependence"]):
        f = summary["feature"]
        pairs = [(row[f], values[j]) for row, values in zip(body["x"], body["values"], strict=True)]
        if summary["kind"] == "categorical":
            for level in summary["levels"]:
                check(level, [c for v, c in pairs if v == level["category"]])
            continue
        lowest = min(v for v, _ in pairs)
        for b in summary["bins"]:
            inside = [c for v, c in pairs if b["low"] < v <= b["high"] or v == lowest == b["low"]]
            check(b, inside)
        assert sum(b["customers"] for b in summary["bins"]) == len(pairs)


def test_the_dependence_means_come_from_the_unrounded_cache(served, environment):
    """The served values are rounded for the dots; the group means are not built from them."""
    from churnsense.explainability.shap_explain import dependence

    artifacts, _ = environment
    cached = json.loads((artifacts / "shap_global.json").read_text())
    body = _ok(served.get("/api/explain/global"))

    def means(summary) -> list[float | None]:
        groups = summary.bins if hasattr(summary, "bins") else summary.levels
        return [group.mean for group in groups]

    expected = [means(s) for s in dependence(cached["features"], cached["values"], cached["x"])]
    served_means = [
        [group["mean"] for group in s.get("bins", s.get("levels"))] for s in body["dependence"]
    ]
    assert any(mean is not None for group in expected for mean in group)
    assert served_means == expected


def test_one_customer_and_the_cached_figures_share_a_baseline(served, ids):
    """The page rings one customer on the cached dots only when both read the same baseline."""
    glob = _ok(served.get("/api/explain/global"))
    one = _ok(served.post("/api/explain", json={"customer_ids": [sorted(ids["validation"])[0]]}))
    assert one["explanations"][0]["base_value"] == pytest.approx(glob["base_value"], abs=1e-9)
    assert one["background_customers"] == glob["n_background"]


def test_an_explanation_names_the_artifact_it_describes(served, model):
    sha = model.meta["model_sha256"]
    one = _ok(served.post("/api/explain", json={"record": dict(schema.EXAMPLE_HIGH_RISK)}))
    assert one["model_sha256"] == sha
    assert _ok(served.get("/api/explain/global"))["model_sha256"] == sha


def test_the_overview_shows_no_ranking_from_attributions_that_do_not_add_up(served, environment):
    artifacts, _ = environment
    path = artifacts / "shap_global.json"
    original = path.read_text()
    try:
        cache = json.loads(original)
        cache["max_additivity_gap"] = 1e-6
        path.write_text(json.dumps(cache))
        overview = _ok(served.get("/api/overview"))
        assert overview["importance"] is None
        assert overview["importance_basis"] is None
    finally:
        path.write_text(original)


@pytest.mark.parametrize("gap", [1e-6, None, "0", -1e-12, True, False, float("nan")])
def test_a_cache_that_does_not_add_up_is_not_served(served, environment, gap):
    artifacts, _ = environment
    path = artifacts / "shap_global.json"
    original = path.read_text()
    try:
        cache = json.loads(original)
        if gap is None:
            del cache["max_additivity_gap"]
        else:
            cache["max_additivity_gap"] = gap
        path.write_text(json.dumps(cache))
        response = served.get("/api/explain/global")
        assert response.status_code == 503
        assert response.json()["error"] == "explanations_not_cached"
    finally:
        path.write_text(original)


# --- patterns -----------------------------------------------------------------------------


def test_a_cross_tab_reports_every_combination_and_suppresses_small_cells(
    served, clean_frame, dashboard_parts
):
    body = _ok(served.get("/api/segments", params={"rows": "Contract", "cols": "tenure"}))
    assert len(body["cells"]) == 3 * 6
    train = clean_frame.loc[dashboard_parts.X_train.index]
    assert body["partition_customers"] == len(train)
    assert body["partition_churned"] == int(train[schema.TARGET].sum())
    assert body["book_customers"] == len(clean_frame)
    assert body["min_customers"] == insights.MIN_CELL == 20
    assert sum(c["n"] for c in body["cells"]) == len(train)
    for cell in body["cells"]:
        if cell["suppressed"]:
            assert cell == {
                "row": cell["row"],
                "column": cell["column"],
                "suppressed": True,
                "n": cell["n"],
                "reason": cell["reason"],
            }
            assert (cell["reason"] == "too_few") == (cell["n"] < 20)
            assert cell["reason"] in {
                "too_few",
                "few_of_an_outcome",
                "cross_partition",
                "complementary",
            }
            if cell["row"] == "Two year" and cell["n"]:
                assert cell["reason"] in {"too_few", "cross_partition"}
        else:
            assert 20 <= cell["churned"] <= cell["n"] - 20 and 0 <= cell["rate"] <= 1
    rates = [c["rate"] for c in body["ranked"]]
    assert rates == sorted(rates, reverse=True) and len(rates) <= 7


def _published(body: dict) -> tuple[dict, dict, set, list]:
    """A segments response as the disclosure oracle reads it: published counts only."""
    rows = [c["row"] for c in body["row_totals"]]
    columns = [c["row"] for c in body["column_totals"]]
    sizes, values, hidden = {"all": body["partition_customers"]}, {}, set()
    values["all"] = body["partition_churned"]
    keyed = [(("row", c["row"]), c) for c in body["row_totals"]]
    if columns:
        keyed += [(("col", c["row"]), c) for c in body["column_totals"]]
        keyed += [((c["row"], c["column"]), c) for c in body["cells"]]
    for key, cell in keyed:
        sizes[key] = cell["n"]
        if not cell["suppressed"]:
            values[key] = cell["churned"]
        elif cell["n"]:
            hidden.add(key)
        else:  # an empty group's count is a known zero
            values[key] = 0
    equations = (
        grid_equations(rows, columns) if columns else line_equation([("row", r) for r in rows])
    )
    return sizes, values, hidden, equations


def _leaks(body: dict, *, largest: int | None) -> tuple[set, set]:
    """What a response gives away, and what it would give away with every cell hidden."""
    sizes, values, hidden, equations = _published(body)
    # The groups a table may hide: its levels, or with two dimensions its cells (never a margin).
    if body["columns"] is None:
        cells = [k for k in sizes if k != "all"]
    else:
        cells = [k for k in sizes if k != "all" and k[0] not in ("row", "col")]
    everything = hidden | {k for k in cells if sizes[k]}
    leaks = fixed_groups(sizes, values, hidden, equations, largest=largest)
    return leaks, fixed_groups(sizes, values, everything, equations, largest=largest)


_ORDERED_PAIRS = [
    *((rows, None) for rows in insights.DIMENSIONS),
    *itertools.permutations(insights.DIMENSIONS, 2),
]


@pytest.mark.parametrize(("rows", "cols"), _ORDERED_PAIRS)
def test_no_segments_response_gives_away_a_small_groups_churn(served, rows, cols):
    params = {"rows": rows} | ({"cols": cols} if cols else {})
    body = _ok(served.get("/api/segments", params=params))
    groups = [*body["cells"], *body["row_totals"], *body["column_totals"]]
    small = [c for c in groups if c["suppressed"] and 0 < c["n"] < 20]
    # Groups of every size when that stays cheap; otherwise singles and pairs.
    leaks, unavoidable = _leaks(body, largest=None if len(small) <= 8 else 2)
    assert leaks <= unavoidable, leaks - unavoidable


def test_a_cross_tab_hides_the_same_cells_whichever_way_round_it_is_asked_for(served):
    for rows, cols in (
        ("Contract", "tenure"),
        ("tenure", "monthly"),
        ("PaymentMethod", "TechSupport"),
    ):
        across = _ok(served.get("/api/segments", params={"rows": rows, "cols": cols}))
        down = _ok(served.get("/api/segments", params={"rows": cols, "cols": rows}))
        hidden = {(c["row"], c["column"]) for c in across["cells"] if c["suppressed"]}
        assert hidden == {(c["column"], c["row"]) for c in down["cells"] if c["suppressed"]}


def test_a_cross_tabs_margins_are_the_one_way_breakdowns_the_overview_shows(served):
    tabs = {t["key"]: t["levels"] for t in _ok(served.get("/api/overview"))["segments"]}
    for rows, cols in (("Contract", "PaymentMethod"), ("monthly", "InternetService")):
        crossed = _ok(served.get("/api/segments", params={"rows": rows, "cols": cols}))
        assert (
            crossed["row_totals"]
            == tabs[rows]
            == _ok(served.get("/api/segments", params={"rows": rows}))["row_totals"]
        )
        assert crossed["column_totals"] == tabs[cols]


def test_a_reported_cells_interval_is_its_wilson_interval(served):
    # The small book shows few groups of twenty of each outcome; these margins are among them.
    body = _ok(served.get("/api/segments", params={"rows": "PaperlessBilling", "cols": "Partner"}))
    reported = [c for c in [*body["cells"], *body["row_totals"]] if not c["suppressed"]]
    assert reported
    for cell in reported:
        low, high = wilson_interval(cell["churned"], cell["n"])
        assert (cell["rate_low"], cell["rate_high"]) == pytest.approx((low, high))
        assert 0 <= cell["rate_low"] <= cell["rate"] <= cell["rate_high"] <= 1


def test_each_tenure_level_names_the_months_it_holds():
    """The page reads a tenure level's months off its label to open it in the explorer."""
    months = pd.Series(range(0, 73))
    buckets = an.tenure_bucket(months).astype(str)
    for label in an.TENURE_LABELS:
        low, high = (int(part) for part in label.split("-"))
        assert ((months >= low) & (months <= high)).tolist() == (buckets == label).tolist()


def _explorer_filter(dimension: str, level: str) -> dict[str, object]:
    """The customer listing's filters that keep exactly one level of a dimension."""
    if dimension == "tenure":
        low, high = (int(part) for part in level.split("-"))
        return {"tenure_min": low, "tenure_max": high}
    return {
        {"Contract": "contract", "InternetService": "internet"}.get(dimension, "payment"): level
    }


#: The dimensions the customer listing can filter one level of.
_LISTED = ("Contract", "tenure", "InternetService", "PaymentMethod")


@pytest.fixture
def served_large(served, environment, public_frame, public_predictor) -> Iterator[TestClient]:
    """The dataset-backed service on the larger synthetic book: enough customers that groups
    of twenty churners and twenty stayers exist to compare. Restores ``served`` after the
    test - function-scoped, so no later test in the module reads the larger book."""
    from churnsense.api import state
    from tests.conftest import PUBLIC_SHA256

    _artifacts, reports = environment
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(state, "ARTIFACTS_DIR", public_predictor.source)
        patch.setattr(state, "REPORTS_DIR", reports)
        patch.setattr(
            state, "BOOK_LOADER", lambda model: score_book(public_frame, PUBLIC_SHA256, model)
        )
        state.reset()
        yield served
    state.reset()


def test_a_reported_group_counts_the_customers_the_explorer_lists_for_it(served_large):
    served = served_large
    compared = 0
    views = [(rows, None) for rows in _LISTED] + list(itertools.combinations(_LISTED, 2))
    for rows, cols in views:
        params = {"rows": rows} | ({"cols": cols} if cols else {})
        body = _ok(served.get("/api/segments", params=params))
        for cell in body["cells"] if cols else body["row_totals"]:
            if cell["suppressed"]:
                continue
            query = _explorer_filter(rows, cell["row"])
            if cols:
                query |= _explorer_filter(cols, cell["column"])
            listing = _ok(
                served.get("/api/customers", params=query | {"partition": "train", "page_size": 1})
            )
            observed = listing["summary"]["observed"]
            assert (observed["customers"], observed["churned"]) == (cell["n"], cell["churned"])
            assert observed["rate"] == pytest.approx(cell["rate"])
            compared += cols is not None
    assert compared, "no two-way cell was compared"


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


@pytest.mark.parametrize("params", [{"margin": "x"}, {"cost": "nan"}, {"success": "inf"}])
def test_assumptions_that_are_not_numbers_are_a_422(served, params):
    assert served.get("/api/threshold/sweep", params=params).status_code == 422


def test_each_served_bound_is_accepted_and_a_step_beyond_it_refused(served):
    """The bounds /model-info serves are the ones the sweep applies, at both ends."""
    bounds = _ok(served.get("/api/model-info"))["assumption_bounds"]
    for name, field in SWEEP_ASSUMPTIONS.items():
        low, high, whole = bounds[field]["min"], bounds[field]["max"], bounds[field]["integer"]
        step = 1 if whole else 0.01
        for value in (low, high):
            response = served.get("/api/threshold/sweep", params={name: value})
            assert _ok(response)["assumptions"][field] == value, name
        refused = [low - step, high + step] + ([low + 0.5] if whole else [])
        for value in refused:
            response = served.get("/api/threshold/sweep", params={name: value})
            assert response.status_code == 422, (name, value)


# --- performance ------------------------------------------------------------------------------


def test_performance_without_a_recorded_evaluation_says_so(served):
    body = _ok(served.get("/api/performance"))
    assert body["test"] is None
    assert body["test_reason"] == "No test evaluation is recorded for this model."
    assert body["candidates"][0]["model"] == "Logistic Regression"
    assert body["validation"]["customers"] > 0


def test_a_recorded_evaluation_is_shown_only_for_the_artifact_it_describes(served, recorded, model):
    body = _ok(served.get("/api/performance"))
    assert body["test"]["metrics"]["n"] == 1000
    info = _ok(served.get("/api/model-info"))
    assert info["test_evaluation"]["recall"] == recorded["metrics"]["recall"]
    assert info["test_evaluation"]["precision"] == recorded["metrics"]["precision"]

    for other in ({"model_sha256": "0" * 64}, {"threshold": 0.99}):
        _record_with(recorded, model, **other)
        assert _ok(served.get("/api/performance"))["test"] is None
        assert _ok(served.get("/api/model-info"))["test_evaluation"] is None
        assert _ok(served.get("/api/model-info"))["test_metrics"] is None


def _validation(book) -> pd.DataFrame:
    from churnsense import book as books

    return books.labelled(book.frame, "validation", shown=book.outcomes)


def test_the_served_validation_rows_are_the_ones_training_measured(book, model):
    """Scored by the served artifact, the book's validation rows reproduce what
    training recorded at 0.50 - the evidence that the page's live figures and
    the artifact's own describe the same customers and the same probabilities."""
    from churnsense.evaluation.metrics import evaluate

    rows = _validation(book)
    measured = evaluate(rows[schema.TARGET], rows["churn_probability"], 0.5).as_dict()
    assert measured == pytest.approx(model.meta["validation_metrics"])


def test_performance_measures_the_served_model_at_its_cut(served, model):
    body = _ok(served.get("/api/performance"))
    measured, curve = body["validation_evaluation"], body["validation"]
    metrics = measured["metrics"]
    assert metrics["threshold"] == body["threshold"] == model.threshold
    # Threshold-free figures do not move with the cut: they are training's own.
    recorded = model.meta["validation_metrics"]
    for key in ("roc_auc", "average_precision", "brier"):
        assert metrics[key] == pytest.approx(recorded[key]), key
    # The counts at the cut are the sweep's at the same label, which must exist.
    point = next(p for p in curve["points"] if p["threshold"] == body["threshold"])
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (
        point["true_positives"],
        point["false_positives"],
        point["false_negatives"],
    )
    assert (metrics["n"], metrics["positives"]) == (curve["customers"], curve["churners"])
    assert metrics["tn"] == metrics["n"] - metrics["positives"] - metrics["fp"]
    assert point["true_negatives"] == metrics["tn"]
    # Flagging everyone has the churn rate as its precision: the no-skill line.
    everyone = curve["points"][0]
    assert (everyone["threshold"], everyone["flagged"]) == (0.0, curve["customers"])
    assert measured["churn_rate"] == pytest.approx(everyone["precision"])
    assert measured["no_skill_accuracy"] == pytest.approx(1 - measured["churn_rate"])


def test_every_cut_of_the_curve_accounts_for_every_validation_customer(served):
    curve = _ok(served.get("/api/performance"))["validation"]
    for p in curve["points"]:
        counts = p["true_negatives"] + p["false_positives"] + p["false_negatives"]
        assert counts + p["true_positives"] == curve["customers"], p["threshold"]


def test_the_live_ece_is_trainings_within_the_clamp(served, model):
    """Training measured ECE on raw probabilities; the clamp moves none by more than ε."""
    measured = _ok(served.get("/api/performance"))["validation_evaluation"]
    meta = model.meta
    recorded = meta["calibrated_ece"] if meta["calibrated"] else meta["uncalibrated_ece"]
    assert abs(measured["ece"] - recorded) <= meta["probability_epsilon"] + 1e-12


def test_reliability_bins_cover_validation_and_small_bins_lose_their_rate(served):
    measured = _ok(served.get("/api/performance"))["validation_evaluation"]
    bins = measured["reliability"]
    assert sum(b["customers"] for b in bins) == measured["metrics"]["n"]
    assert [b["low"] for b in bins] == sorted(b["low"] for b in bins)
    for b in bins:
        assert b["high"] - b["low"] == pytest.approx(0.1)
        assert b["low"] <= b["mean_predicted"] <= b["high"]
        if b["customers"] < insights.MIN_CELL:
            assert b["churned"] is None and b["rate"] is None
        else:
            assert b["rate"] == pytest.approx(b["churned"] / b["customers"])
    assert any(b["rate"] is not None for b in bins)


def test_validation_segments_add_up_and_small_ones_say_nothing(served, model):
    measured = _ok(served.get("/api/performance"))["validation_evaluation"]
    metrics, segments = measured["metrics"], measured["segments"]
    assert {s["dimension"] for s in segments} == set(schema.VALIDATION_SEGMENTS)
    for dimension in schema.VALIDATION_SEGMENTS:
        rows = [s for s in segments if s["dimension"] == dimension]
        assert sum(s["customers"] for s in rows) == metrics["n"], dimension
        assert sum(s["flagged"] for s in rows) == metrics["tp"] + metrics["fp"], dimension
    hidden = ("churners", "churn_rate", "flagged_rate", "roc_auc", *_INTERVALS, "precision")
    for s in segments:
        if s["customers"] < insights.MIN_CELL:
            assert all(s[k] is None for k in (*hidden, "recall")), s
            continue
        assert s["unscanned"] == (s["flagged"] == 0 and s["churners"] > 0)
        if s["flagged"] == 0:
            assert s["precision"] is None, "undefined, not zero"
        else:
            assert s["precision_low"] <= s["precision"] <= s["precision_high"]
        if s["churners"]:
            assert s["recall_low"] <= s["recall"] <= s["recall_high"]


def test_an_undefined_segment_rate_is_null_not_a_failure(book, model):
    """At a cut above every score nothing is flagged: precision is undefined everywhere."""
    from churnsense.api import dashboard_schemas as out

    measured = insights.validation_evaluation(book, 1.0, schema.VALIDATION_SEGMENTS)
    body = out.ValidationEvaluation.model_validate(measured).model_dump()
    assert all(s["precision"] is None for s in body["segments"])
    # The NaN Wilson bounds of an empty denominator are null too.
    assert all(s["precision_low"] is None and s["precision_high"] is None for s in body["segments"])
    assert body["metrics"]["tp"] + body["metrics"]["fp"] == 0


def test_a_small_segment_does_not_say_whether_anyone_in_it_churned(book):
    """With nothing flagged, "not scanned" would tell churners > 0 from none."""
    measured = insights.validation_evaluation(book, 1.0, schema.VALIDATION_SEGMENTS)
    small = [s for s in measured.segments if s["customers"] < insights.MIN_CELL]
    assert small, "the fixture has no small segment"
    assert all(s["unscanned"] is None for s in small)
    big = [s for s in measured.segments if s["customers"] >= insights.MIN_CELL]
    assert all(s["unscanned"] == (s["churners"] > 0) for s in big)


def test_the_segment_rules_name_only_fields_the_tables_have():
    """A key nulled for small groups must be a field of both tables, and the
    validation table must carry exactly what segment_table measures."""
    import numpy as np

    from churnsense.api import dashboard_schemas as out
    from churnsense.evaluation.metrics import segment_table

    assert set(insights.SEGMENT_OUTCOMES) <= set(out.ValidationSegment.model_fields)
    y = np.array([0, 1, 0, 1])
    frame = pd.DataFrame({"Contract": ["A", "A", "B", "B"]})
    columns = set(segment_table(y, np.array([0.1, 0.9, 0.2, 0.8]), frame, ["Contract"], 0.5))
    assert set(out.ValidationSegment.model_fields) == columns | {"unscanned"}


def test_a_segment_list_absent_from_the_book_measures_no_segment(book, model):
    measured = insights.validation_evaluation(book, model.threshold, ("NotAColumn",))
    assert measured is not None and measured.segments == []


def test_validation_with_one_outcome_has_no_evaluation(book, model):
    """ROC-AUC needs both outcomes; a partition with one is not measured, nor a 500."""
    rows = _validation(book)
    stayed = book.outcomes.copy()
    stayed.loc[rows[schema.ID_COLUMN]] = 0
    one_class = book._replace(outcomes=stayed)
    assert insights.validation_evaluation(one_class, model.threshold, ("Contract",)) is None


def test_performance_names_the_leader_and_the_artifact(served, model):
    from churnsense.models.registry import DISPLAY_NAMES

    body = _ok(served.get("/api/performance"))
    selection = body["selection"]
    # The name `candidates` (model_comparison.csv) use for the same model.
    assert selection["leader_name"] == DISPLAY_NAMES[selection["leader"]]
    info = _ok(served.get("/api/model-info"))
    assert body["model_sha256"] == info["model_sha256"] == model.meta["model_sha256"]


def test_an_unknown_leader_is_named_by_its_key_not_a_failure(served, model, monkeypatch):
    monkeypatch.setitem(model.meta["selection"], "leader", "a_model_since_removed")
    selection = _ok(served.get("/api/performance"))["selection"]
    assert selection["leader_name"] == "a_model_since_removed"


def test_a_recorded_evaluation_carries_its_rate(served, recorded):
    test = _ok(served.get("/api/performance"))["test"]
    assert test["churn_rate"] == 0.3
    assert test["no_skill_accuracy"] == 0.7


#: What no response may say of the test partition, whatever the record holds.
_NEVER_SERVED = (
    "segments",
    "reliability",
    "ece",
    "metrics_at_half",
    "test_optimal_threshold",
    "unscanned",
)


def test_a_ledger_holding_more_than_the_rule_allows_is_withheld_whole(served, recorded, model):
    """A hand edit with a correct digest: the extra key refuses it, it is not just dropped."""
    for extra in (
        {"ece": 0.031},
        {"segments": [{"dimension": "Contract", "segment": "One year", "precision": 0.8}]},
        {"reliability": [{"count": 600, "observed_rate": 0.2}]},
        {"metrics_at_half": {"tp": 1}},
        {"test_optimal_threshold": 0.42},
        {"secret": 0.123456},
        {"roc_auc": 0.812345},
        {"average_precision": 0.612345},
        {"brier": 0.123456},
    ):
        _record_with(recorded, model, metrics={**recorded["metrics"], **extra})
        body = _ok(served.get("/api/performance"))
        assert body["test"] is None, extra
        assert body["test_reason"] == TEST_WITHHELD_REASON
        info = _ok(served.get("/api/model-info"))
        assert info["test_evaluation"] is None and info["test_metrics"] is None, extra


_SCALARS = {"roc_auc", "average_precision", "brier"}


def test_no_surface_serves_a_held_out_score_metric(served, recorded):
    """Not null, not zero: the fields are not there, on any route, and not in the contract."""
    test = _ok(served.get("/api/performance"))["test"]
    assert test["metrics"]["tp"] == 150, "the fixture must reach the release"
    assert not _SCALARS & set(test["metrics"])
    assert not _SCALARS & set(test)
    for route in ("/model-info", "/api/model-info"):
        info = _ok(served.get(route))
        assert not _SCALARS & set(info["test_metrics"])
        assert not _SCALARS & set(info["test_evaluation"])
    contract = served.get("/openapi.json").json()["components"]["schemas"]
    assert not _SCALARS & set(contract["HoldoutMetrics"]["properties"])
    assert not _SCALARS & set(contract["HoldoutSummary"]["properties"])


def test_validation_keeps_its_score_metrics_under_their_own_name(served, recorded):
    """A withheld test figure is not filled in from validation, and validation loses nothing."""
    body = _ok(served.get("/api/performance"))
    assert set(body["validation_evaluation"]["metrics"]) >= _SCALARS
    assert set(_ok(served.get("/api/model-info"))["validation_metrics"]) >= _SCALARS
    assert not _SCALARS & set(body["test"]["metrics"])


def test_a_sidecar_cannot_put_figures_back(served, model, monkeypatch):
    """The sidecar used to carry a copy, marked or not: it is not read any more."""
    monkeypatch.setitem(model.meta, "test_metrics", dict(_INVENTED_METRICS))
    monkeypatch.setitem(model.meta, "test_disclosure_policy", 2)
    monkeypatch.setitem(model.meta, "test_evaluated_at", "2026-10-21T00:00:00+00:00")
    info = _ok(served.get("/api/model-info"))
    assert info["test_metrics"] is None and info["test_evaluation"] is None


def test_a_record_file_beside_the_reports_cannot_put_figures_back(served, environment, model):
    _, reports = environment
    path = reports / "final_evaluation.json"
    path.write_text(
        json.dumps(
            {
                "model_sha256": model.meta["model_sha256"],
                "evaluated_at": "2026-10-21T00:00:00+00:00",
                "test_disclosure_policy": 2,
                "metrics": _INVENTED_METRICS,
            }
        )
    )
    try:
        assert _ok(served.get("/api/performance"))["test"] is None
        assert _ok(served.get("/api/model-info"))["test_evaluation"] is None
    finally:
        path.unlink()


def test_performance_and_model_info_never_disagree_about_a_release(served, recorded, model):
    """One file behind both: in every state, either both show it or neither does."""
    from churnsense.evaluation import release

    def both() -> tuple[bool, bool]:
        return (
            _ok(served.get("/api/performance"))["test"] is not None,
            _ok(served.get("/api/model-info"))["test_evaluation"] is not None,
        )

    assert both() == (True, True)
    _seal(model, None, state="reserved")
    assert both() == (False, False)
    _seal(model, None)
    assert both() == (False, False)
    release.LEDGER_FILE.write_text("{ half a ledger")
    assert both() == (False, False)
    release.LEDGER_FILE.unlink()
    assert both() == (False, False)
    release.LEDGER_FILE.write_text(release.open_ledger_text())
    assert both() == (False, False)


# --- revenue at risk ---------------------------------------------------------------------------


def _labels(shown: pd.DataFrame) -> pd.Series:
    return shown.set_index(schema.ID_COLUMN)[schema.TARGET]


def _at_risk_ids(ids, shown, count: int = 30) -> dict[str, list[str]]:
    train, labels = sorted(ids["train"]), _labels(shown)
    return {
        "churned": [i for i in train if labels[i] == 1][:count],
        "stayed": [i for i in train if labels[i] == 0][:count],
        "withheld": sorted(ids["test"])[:count],
    }


def test_a_customer_recorded_as_churned_has_no_revenue_at_risk(served, ids, shown):
    chosen = _at_risk_ids(ids, shown)
    everyone = list(itertools.chain.from_iterable(chosen.values()))
    body = _ok(served.get("/api/customers", params={"ids": everyone, "page_size": 100}))
    by_id = {r["id"]: r for r in body["rows"]}
    for customer_id in chosen["churned"]:
        assert by_id[customer_id]["outcome"] == "churned"
        assert by_id[customer_id]["at_risk"] is None
    for customer_id in chosen["stayed"]:
        row = by_id[customer_id]
        assert row["at_risk"] == pytest.approx(row["churn_probability"] * row["monthly_charges"])
    for customer_id in chosen["withheld"]:
        assert by_id[customer_id]["status"] == "unknown"
        assert by_id[customer_id]["at_risk"] is None, "not known to be paying, nor to have left"


def test_the_total_at_risk_counts_only_customers_recorded_active(served, ids, shown):
    chosen = _at_risk_ids(ids, shown)
    everyone = list(itertools.chain.from_iterable(chosen.values()))
    body = _ok(served.get("/api/customers", params={"ids": everyone, "page_size": 100}))
    expected = sum(
        r["churn_probability"] * r["monthly_charges"]
        for r in body["rows"]
        if r["status"] == "active"
    )
    assert expected > 0
    assert body["summary"]["at_risk"] == pytest.approx(expected)
    alone = _ok(served.get("/api/customers", params={"ids": chosen["churned"]}))
    assert alone["summary"]["at_risk"] == 0
    assert alone["summary"]["monthly_charges"] > 0, "their charges still count as charges"


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_customers_with_nothing_at_risk_sort_last_either_way(served, direction):
    pages = _ok(
        served.get(
            "/api/customers",
            params={"partition": "train", "sort": "at_risk", "dir": direction, "page_size": 100},
        )
    )["pages"]
    last = _ok(
        served.get(
            "/api/customers",
            params={
                "partition": "train",
                "sort": "at_risk",
                "dir": direction,
                "page_size": 100,
                "page": pages,
            },
        )
    )
    first = _ok(
        served.get(
            "/api/customers",
            params={"partition": "train", "sort": "at_risk", "dir": direction, "page_size": 100},
        )
    )
    assert all(r["at_risk"] is not None for r in first["rows"])
    assert last["rows"][-1]["at_risk"] is None
    values = [r["at_risk"] for r in first["rows"]]
    assert values == sorted(values, reverse=direction == "desc")


def test_excluding_churned_customers_leaves_those_who_can_still_be_retained(served, ids, shown):
    churned = int(_labels(shown).sum())
    everyone = _ok(served.get("/api/customers", params={"page_size": 1}))["total"]
    kept = _ok(served.get("/api/customers", params={"exclude_churned": "true", "page_size": 100}))
    assert kept["total"] == everyone - churned
    assert "churned" not in {r["outcome"] for r in kept["rows"]}


@pytest.mark.parametrize("band", ["Low", "Moderate", "High", "Critical"])
def test_each_bands_count_of_those_who_can_be_retained_is_the_queues(served, band):
    """A chip's number and the list it opens have to be the same customers."""
    summary = next(b for b in _ok(served.get("/api/overview"))["bands"] if b["name"] == band)
    params = {"bands": band, "flagged": "true", "page_size": 1}
    assert summary["flagged"] == _ok(served.get("/api/customers", params=params))["total"]
    params["exclude_churned"] = "true"
    kept = _ok(served.get("/api/customers", params=params))["total"]
    assert summary["flagged_not_churned"] == kept


def test_a_band_count_of_those_who_can_be_retained_keeps_every_flagged_test_customer(book, cfg):
    """It reads shown outcomes only: test customers count whatever became of them."""
    from churnsense import book as books

    frame = book.frame.assign(partition="test")
    blind = books.Book(frame, book.outcomes.iloc[:0], pd.Index([]), "not verified")
    for band in insights.band_summaries(blind, cfg):
        assert band.flagged_not_churned == band.flagged


def test_excluding_churned_customers_takes_no_test_customer_away(served):
    """The filter reads shown outcomes only: it cannot tell a test churner from a stayer."""
    base = _ok(served.get("/api/customers", params={"partition": "test", "page_size": 100}))
    kept = _ok(
        served.get(
            "/api/customers",
            params={"partition": "test", "exclude_churned": "true", "page_size": 100},
        )
    )
    assert kept["total"] == base["total"]
    assert [r["id"] for r in kept["rows"]] == [r["id"] for r in base["rows"]]
    assert kept["summary"]["at_risk"] == pytest.approx(base["summary"]["at_risk"])


def test_the_export_takes_the_same_filter(served):
    kept = served.get("/api/customers/export", params={"exclude_churned": "true"}).text
    frame = pd.read_csv(io.StringIO(kept), dtype=str)
    assert "churned" not in set(frame["outcome"])


# --- the overview's figures against the queue they open ----------------------------------------


def _queue(served, **params) -> dict:
    return _ok(
        served.get(
            "/api/customers", params={"flagged": "true", "exclude_churned": "true", **params}
        )
    )


def test_a_key_figure_is_the_count_of_the_queue_it_opens(served, cfg: Config):
    """Tiles and list: the same customers, each counted, churners left out of both."""
    book = _ok(served.get("/api/overview"))
    riskiest = [band.name for band in cfg.risk_bands][-2:]
    for figure, params in (
        (book["flagged"], {}),
        (book["high_or_critical"], {"bands": riskiest}),
    ):
        listing = _queue(served, **params)
        assert figure["in_queue"] == listing["total"]
        assert figure["in_queue_monthly"] == pytest.approx(
            listing["summary"]["active_monthly_charges"]
        )
        assert figure["in_queue_share"] == pytest.approx(listing["total"] / book["customers"])
    assert book["flagged"]["in_queue"] < book["flagged"]["customers"], "no churner to leave out"


def test_a_key_figure_leaves_out_who_the_threshold_does_not_flag(book, cfg: Config):
    """A band's edge can sit below the threshold: the queue lists flagged customers only."""
    top = book.frame["risk_band"].isin([band.name for band in cfg.risk_bands][-2:])
    assert top.sum() > 1, "the fixture has no customer in the top bands"
    unflagged = book._replace(frame=book.frame.assign(flagged=book.frame["flagged"] & ~top))
    figure = insights.high_or_critical(unflagged, cfg)
    assert figure.customers == top.sum()
    assert figure.in_queue == 0
    assert figure.in_queue_monthly == 0


def test_the_book_wide_flagged_count_is_what_the_explorer_lists(served):
    book = _ok(served.get("/api/overview"))
    listing = _ok(served.get("/api/customers", params={"flagged": "true"}))
    assert book["flagged"]["customers"] == listing["total"]


def test_recurring_charges_count_active_customers_and_name_the_unknown_apart(served, ids):
    book = _ok(served.get("/api/overview"))
    whole = _ok(served.get("/api/customers"))
    assert book["current_monthly_charges"] == pytest.approx(
        whole["summary"]["active_monthly_charges"]
    )
    test = _ok(served.get("/api/customers", params={"partition": "test"}))
    assert book["unknown_customers"] == test["total"] == len(ids["test"])
    assert book["unknown_monthly_charges"] == pytest.approx(test["summary"]["monthly_charges"])
    assert test["summary"]["active_monthly_charges"] == 0
    assert (
        book["current_monthly_charges"] + book["unknown_monthly_charges"] < book["monthly_charges"]
    ), "the rest is who is recorded as churned"
    kept = _ok(
        served.get("/api/customers", params={"partition": "test", "exclude_churned": "true"})
    )
    assert kept["total"] == test["total"], "an unknown status is not a churned one"


def test_each_row_says_its_status_and_it_follows_only_the_shown_outcome(served, ids):
    for partition in ("train", "validation", "test"):
        rows = _ok(served.get("/api/customers", params={"partition": partition, "page_size": 100}))[
            "rows"
        ]
        for row in rows:
            assert (
                row["status"]
                == {"stayed": "active", "churned": "churned", "withheld": "unknown"}[row["outcome"]]
            )
        if partition == "test":
            assert {row["status"] for row in rows} == {"unknown"}
            assert all(row["at_risk"] is None for row in rows)


def test_status_counts_add_up_under_every_filter(served, cfg: Config):
    names = [band.name for band in cfg.risk_bands]
    for params in (
        {},
        {"partition": "train"},
        {"partition": "test"},
        {"flagged": "true"},
        {"flagged": "true", "exclude_churned": "true"},
        {"flagged": "true", "exclude_churned": "true", "partition": "test"},
        {"flagged": "true", "exclude_churned": "true", "partition": "validation"},
        {"flagged": "true", "exclude_churned": "true", "bands": names[-1:]},
        {"q": "ZZZ-NOBODY"},
    ):
        page = _ok(served.get("/api/customers", params=params))
        counts = page["status_counts"]
        assert set(counts) == {"active", "churned", "unknown"}
        assert sum(counts.values()) == page["total"], params
        if params.get("exclude_churned"):
            assert counts["churned"] == 0, params


def test_a_key_figure_splits_the_queue_as_the_list_does(served, cfg: Config):
    book = _ok(served.get("/api/overview"))
    riskiest = [band.name for band in cfg.risk_bands][-2:]
    for figure, params in (
        (book["flagged"], {}),
        (book["high_or_critical"], {"bands": riskiest}),
    ):
        listing = _queue(served, **params)
        assert figure["in_queue_active"] == listing["status_counts"]["active"]
        assert figure["in_queue_unknown"] == listing["status_counts"]["unknown"]
        assert figure["in_queue"] == figure["in_queue_active"] + figure["in_queue_unknown"]
        assert figure["in_queue_unknown"] > 0, "the fixture must have flagged test customers"
        assert figure["in_queue_active"] > 0


def test_every_status_unknown_book_has_no_recurring_figure_at_all(book, cfg: Config):
    """Partitions that cannot be verified: nothing is known, so nothing is claimed as current."""
    from churnsense import book as books

    blind = books.Book(book.frame, book.outcomes.iloc[:0], pd.Index([]), "not verified")
    assert insights.current_monthly_charges(blind) is None
    assert insights.unknown_customers(blind) == len(book.frame)
    assert insights.unknown_monthly_charges(blind) == pytest.approx(
        book.frame["MonthlyCharges"].sum()
    )
    figure = insights.flagged_share(blind)
    assert figure.in_queue_active == 0
    assert figure.in_queue_monthly == 0
    assert figure.in_queue == figure.in_queue_unknown == int(book.frame["flagged"].sum())


def test_all_churned_is_nobody_current_and_nobody_unknown(book, cfg: Config):
    from churnsense import book as books

    every = pd.Series(1, index=book.frame[schema.ID_COLUMN], name=schema.TARGET)
    gone = books.Book(book.frame, every, book.train_index, None)
    assert insights.current_monthly_charges(gone) is None
    assert insights.unknown_customers(gone) == 0
    figure = insights.flagged_share(gone)
    assert (figure.in_queue, figure.in_queue_active, figure.in_queue_unknown) == (0, 0, 0)


@pytest.mark.parametrize(
    "narrowed",
    [{}, {"partition": "train"}, {"partition": "validation"}, {"partition": "test"}, {"q": "1"}],
    ids=["whole-book", "train", "validation", "test", "search"],
)
def test_a_chip_counts_what_choosing_its_band_would_list(served, cfg: Config, narrowed):
    """The chips and the list agree under every filter, whichever bands are chosen."""
    names = [band.name for band in cfg.risk_bands]
    whole = _queue(served, **narrowed)
    assert set(whole["band_counts"]) == set(names), "a band with nobody is named, with 0"
    assert sum(whole["band_counts"].values()) == whole["total"]
    for name in names:
        chosen = _queue(served, **narrowed, bands=[name])
        assert chosen["band_counts"] == whole["band_counts"], "the bands chosen change no chip"
        assert chosen["total"] == whole["band_counts"][name]


def test_every_flagged_test_customer_stays_in_the_chips(served, cfg: Config):
    """The chips read no held-out outcome: what is flagged is what is counted."""
    counts = _queue(served, partition="test")["band_counts"]
    flagged = _ok(served.get("/api/customers", params={"partition": "test", "flagged": "true"}))
    assert sum(counts.values()) == flagged["total"]


def test_a_histogram_rate_is_drawn_at_its_customers_mean_score_inside_its_bin(served):
    bins = _ok(served.get("/api/overview"))["histogram"]
    drawn = [b for b in bins if b["observed_mean"] is not None]
    assert drawn, "the fixture has no bin to draw"
    for b in bins:
        if b["observed_mean"] is None:
            assert b["observed"] is None or b["observed"]["rate"] is None
        else:
            assert b["low"] <= b["observed_mean"] <= b["high"]
            assert b["observed"]["rate"] is not None


def test_listing_by_ids_and_excluding_churned_is_refused_not_guessed(served, ids):
    response = served.get(
        "/api/customers", params={"ids": sorted(ids["train"])[:2], "exclude_churned": "true"}
    )
    assert response.status_code == 422


def test_with_outcomes_withheld_nobody_has_a_known_revenue_at_risk_and_nobody_is_dropped(book):
    """An unknown status is neither a confirmed customer nor a churned one."""
    from churnsense import book as books

    blind = books.Book(book.frame, book.outcomes.iloc[:0], pd.Index([]), "not verified")
    frame = insights.with_outcomes(blind)
    assert frame["at_risk"].isna().all()
    assert set(frame["status"]) == {"unknown"}
    kept = insights.select(frame, insights.CustomerFilter(exclude_churned=True))
    assert len(kept) == len(frame)


# --- calibration overlay and served differences --------------------------------------------------


def test_the_histogram_reads_observed_churn_from_validation_customers_only(
    served, ids, clean_frame
):
    body = _ok(served.get("/api/overview"))
    assert sum(h["customers"] for h in body["histogram"]) == len(clean_frame)
    assert sum(h["observed"]["customers"] for h in body["histogram"]) == len(ids["validation"])


def test_the_histogram_counts_the_outcomes_the_validation_customers_have(book, shown, ids):
    from churnsense.config import load_config

    bins = insights.probability_histogram(book, load_config())
    frame = book.frame[book.frame[schema.ID_COLUMN].isin(ids["validation"])]
    positions = insights.histogram_bins(frame["churn_probability"])
    labels, counted = _labels(shown), 0
    for i, histogram_bin in enumerate(bins):
        if histogram_bin.observed.churned is not None:
            members = frame[positions == i][schema.ID_COLUMN]
            assert histogram_bin.observed.churned == labels[members].sum()
            counted += 1
    assert counted, "no bin of the fixture is large enough to read"


def test_the_campaign_serves_what_the_peak_gains_over_the_shipped_cut(served, monkeypatch):
    """A cut made worse than the peak, as the fixture's own cut can sit on it and gain nothing."""
    from dataclasses import replace

    from churnsense.api import dashboard

    real = dashboard.scenario_at

    def worse(*args, **kwargs):
        shipped = real(*args, **kwargs)
        return replace(shipped, net_benefit=shipped.net_benefit - 100)

    monkeypatch.setattr(dashboard, "scenario_at", worse)
    campaign = _ok(served.get("/api/overview"))["campaign"]
    assert campaign["peak_gain"] > 0
    assert campaign["peak_gain"] == pytest.approx(
        campaign["peak_net_benefit"] - campaign["net_benefit"]
    )


def test_the_selection_serves_the_leaders_lead_over_the_selected_model(served, model, monkeypatch):
    from churnsense.models.registry import DISPLAY_NAMES

    candidates = _ok(served.get("/api/performance"))["candidates"]
    chosen = next(c for c in candidates if c["selected"])
    leader = next(c for c in candidates if not c["selected"] and c["model"] != "Baseline (prior)")
    key = next(k for k, name in DISPLAY_NAMES.items() if name == leader["model"])
    monkeypatch.setitem(model.meta["selection"], "leader", key)
    selection = _ok(served.get("/api/performance"))["selection"]
    assert selection["leader_gap"] == pytest.approx(leader["cv_pr_auc"] - chosen["cv_pr_auc"])


def test_without_a_matching_comparison_no_lead_is_stated(served, model, monkeypatch):
    monkeypatch.setitem(model.meta["selection"], "leader", "a_model_since_removed")
    selection = _ok(served.get("/api/performance"))["selection"]
    assert selection["leader_gap"] is None


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
    assert len(body["csv"].splitlines()) == 1 + 30
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


#: The scored file's columns before the model's inputs; never a label.
_SCORED_COLUMNS = ["row", schema.ID_COLUMN, "churn_probability", "risk_band", "flagged"]


def _scored(body: dict) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(body["csv"]), dtype=str, keep_default_na=False)


def test_the_scored_csv_holds_the_rows_the_page_shows(served, model, raw_frame):
    """One scoring makes both, so the downloaded file is the table, row for row,
    with a formula-shaped ID made inert for a spreadsheet."""
    upload = raw_frame.head(30).copy()
    upload.loc[0, schema.ID_COLUMN] = '=HYPERLINK("http://example.com")'
    body = _ok(_upload(served, upload))
    exported = _scored(body)
    assert list(exported.columns) == [*_SCORED_COLUMNS, *model.feature_columns]
    assert exported.loc[0, schema.ID_COLUMN] == """'=HYPERLINK("http://example.com")"""
    for line, row in zip(exported.to_dict("records"), body["rows"], strict=True):
        assert int(line["row"]) == row["row"]
        assert line[schema.ID_COLUMN].removeprefix("'") == row["customer_id"]
        assert float(line["churn_probability"]) == row["churn_probability"]
        assert line["risk_band"] == row["risk_band"]
        assert line["flagged"] == str(row["flagged"])
        for column, value in row["features"].items():
            read = line[column] if isinstance(value, str) else float(line[column])
            assert read == value, column


@pytest.mark.parametrize("with_ids", [True, False], ids=["with ids", "without ids"])
def test_the_scored_csv_never_carries_a_label(served, model, raw_frame, with_ids):
    """The upload holds every test customer with their Churn; the file sent back
    has exactly these columns, so no label can ride along."""
    upload = raw_frame if with_ids else raw_frame.drop(columns=[schema.ID_COLUMN])
    exported = _scored(_ok(_upload(served, upload)))
    expected = [c for c in _SCORED_COLUMNS if with_ids or c != schema.ID_COLUMN]
    assert list(exported.columns) == [*expected, *model.feature_columns]


def test_the_scored_csv_scores_the_same_when_uploaded_again(served, raw_frame):
    """Its extra columns are ignored and its inputs read back as scored. The
    forest's parallel sum can differ in the last bit between any two calls."""
    first = _ok(_upload(served, raw_frame.head(40)))
    again = served.post(
        "/api/batch/score", files={"file": ("again.csv", first["csv"].encode(), "text/csv")}
    )
    assert [r["churn_probability"] for r in _ok(again)["rows"]] == pytest.approx(
        [r["churn_probability"] for r in first["rows"]], abs=1e-12
    )


def _labelled_churners(raw_frame, ids, model, n: int) -> pd.DataFrame:
    """``n`` training customers who churned, highest scored first: labelled, flagged, not held out."""
    churned = raw_frame[
        raw_frame[schema.ID_COLUMN].isin(ids["train"]) & raw_frame[schema.TARGET].eq("Yes")
    ]
    scores = model.predict_frame(_ok_frame(churned))["churn_probability"].to_numpy()
    return churned.iloc[scores.argsort()[::-1][:n]]


@pytest.mark.parametrize(
    ("n", "reported"), [(insights.MIN_CELL - 1, False), (insights.MIN_CELL, True)]
)
def test_a_label_check_on_fewer_than_twenty_rows_reports_no_rate(
    served, raw_frame, ids, model, n, reported
):
    """The rule every page keeps: a rate over a handful of rows is noise, not a finding."""
    labels = _ok(_upload(served, _labelled_churners(raw_frame, ids, model, n)))["summary"]["labels"]
    assert labels["labelled"] == n
    assert (labels["precision"] is not None) is reported
    assert (labels["recall"] is not None) is reported
    small = f"A rate needs at least {insights.MIN_CELL} labelled rows."
    assert labels["reason"] == (None if reported else small)


def test_a_label_check_counts_only_the_rows_it_compares(served, raw_frame, ids, model):
    """The gate counts labelled rows outside the test partition, not the file's:
    15 compared and 10 held out are still too few for a rate."""
    held_out = raw_frame[raw_frame[schema.ID_COLUMN].isin(ids["test"])].head(10)
    upload = pd.concat([_labelled_churners(raw_frame, ids, model, 15), held_out])
    body = _ok(_upload(served, upload))
    labels = body["summary"]["labels"]
    assert (labels["labelled"], labels["excluded_test"]) == (15, 10)
    assert labels["precision"] is None and labels["recall"] is None
    # Beside its dataset the service does compare, and its note about the column says so.
    assert (
        "Churn is never used to score; it is kept only to compare against the predictions"
        in body["notes"]
    )


def _compared_customers(raw_frame, ids, model, *, flagged: bool, churned: bool) -> pd.DataFrame:
    """Customers outside the test partition the served model flags (or not) who
    churned (or not). The forest flags every training churner, so an unflagged
    churner comes from validation."""
    compared = raw_frame[~raw_frame[schema.ID_COLUMN].isin(ids["test"])]
    scored = model.predict_frame(_ok_frame(compared))["flagged"].to_numpy()
    return compared[(scored == flagged) & (compared[schema.TARGET].eq("Yes").to_numpy() == churned)]


@pytest.mark.parametrize(
    ("customers", "rates", "reason"),
    [
        (
            {(False, False): 19, (False, True): 1},
            (None, 0.0),
            "No labelled customer was flagged, so precision is not defined.",
        ),
        (
            {(True, False): 1, (False, False): 19},
            (0.0, None),
            "No labelled customer churned, so recall is not defined.",
        ),
        (
            {(False, False): 20},
            (None, None),
            "No labelled customer was flagged or churned, so neither rate is defined.",
        ),
    ],
    ids=["nobody flagged", "nobody churned", "neither"],
)
def test_a_label_check_says_why_a_rate_has_no_value(
    served, raw_frame, ids, model, customers, rates, reason
):
    """Enough rows, but a rate's denominator is empty: the page says so instead of a bare dash."""
    upload = pd.concat(
        [
            _compared_customers(raw_frame, ids, model, flagged=flagged, churned=churned).head(n)
            for (flagged, churned), n in customers.items()
        ]
    )
    labels = _ok(_upload(served, upload))["summary"]["labels"]
    assert labels["labelled"] == 20
    assert (labels["precision"], labels["recall"], labels["reason"]) == (*rates, reason)


# --- every response: no test outcome, no small-cell rate, a clean error shape ------------------------


#: Invented figures that add up: 1,000 customers, 300 churners, 350 flagged, 150 caught.
_INVENTED_METRICS = {
    "threshold": 0.36,
    "n": 1000,
    "positives": 300,
    "tn": 500,
    "fp": 200,
    "fn": 150,
    "tp": 150,
    "accuracy": 0.65,
    "precision": 0.4286,
    "recall": 0.5,
    "f1": 0.4615,
}


def _seal(model, metrics: dict | None, *, state: str = "published", **identity) -> None:
    """Put a ledger on disk as a release of the served artifact, or as ``identity`` says.

    Past the writer, which would refuse: these tests plant what a damaged file could hold.
    """
    from churnsense.evaluation import release

    threshold = model.meta["threshold"]
    release.LEDGER_FILE.write_text(
        release._encode(
            release.Ledger(
                state,
                {
                    **dict.fromkeys(release._HASH_KEYS, "a" * 64),
                    "model_sha256": model.meta["model_sha256"],
                    "threshold": threshold,
                    **identity,
                },
                "2026-10-21T00:00:00+00:00",
                None if metrics is None else {**metrics, "threshold": threshold},
            )
        )
    )


@pytest.fixture
def recorded(served, model):
    """The partition's one release, of the served artifact, for the length of one test."""
    _seal(model, _INVENTED_METRICS)
    return {"metrics": dict(_INVENTED_METRICS)}


#: Where the partition's one release appears: recorded figures, not a view of test
#: rows, and the one documented exception to "no test figure".
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
        ("batch of a few", raw_frame.head(10)),
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


#: A rate's 95 % interval: a rate in disguise, so it follows the rate.
_INTERVALS = ("precision_low", "precision_high", "recall_low", "recall_high")
#: Keys that state an outcome rate or count of a group.
_OUTCOME_KEYS = (
    "rate",
    "churned",
    "churn_rate",
    "churners",
    "precision",
    "recall",
    "roc_auc",
    # "Nobody flagged though someone churned" says whether anyone churned.
    "unscanned",
    *_INTERVALS,
    # A rate over the partition's, and a rate's interval: the rate in disguise.
    "lift",
    "rate_low",
    "rate_high",
)


def test_no_response_reports_an_outcome_rate_for_fewer_than_twenty_customers(
    served, ids, raw_frame, recorded
):
    small = 0
    for name, obj in _scanned(served, ids, raw_frame):
        size = next((obj[k] for k in ("customers", "n", "labelled") if k in obj), None)
        if isinstance(size, int) and size < 20 and any(k in obj for k in _OUTCOME_KEYS):
            small += 1
            assert all(obj.get(k) is None for k in _OUTCOME_KEYS), (name, obj)
    assert small, "the scan never met a small group"


def _record_with(recorded: dict, model, **changes) -> None:
    """A ledger holding other figures. The digest is right: only the figures can fail."""
    from churnsense.evaluation import release

    _seal(model, changes.pop("metrics", recorded["metrics"]), **changes)
    assert release.LEDGER_FILE.is_file()


@pytest.mark.parametrize(
    "metrics",
    [
        pytest.param({"n": 19, "positives": 5, "tn": 7, "fp": 7, "fn": 2, "tp": 3}, id="small"),
        pytest.param({"tp": 0, "fp": 350, "tn": 350, "fn": 300}, id="nobody-flagged-churned"),
        pytest.param(
            {"tp": 350, "fp": 0, "tn": 500, "fn": 150, "positives": 500}, id="all-churned"
        ),
        pytest.param({"fn": 0, "tp": 300, "fp": 200, "tn": 500}, id="no-churner-missed"),
        pytest.param({"tp": 19, "fp": 331, "fn": 281, "tn": 369}, id="nineteen-in-a-cell"),
        pytest.param({"tp": 150.5}, id="not-a-whole-count"),
        pytest.param({"n": 999}, id="does-not-add-up"),
    ],
)
def test_a_partition_the_rule_withholds_is_not_served(served, recorded, model, metrics):
    _record_with(recorded, model, metrics={**recorded["metrics"], **metrics})
    body = _ok(served.get("/api/performance"))
    assert body["test"] is None
    assert _ok(served.get("/api/model-info"))["test_evaluation"] is None


def test_every_cause_of_a_withheld_evaluation_reads_the_same(served, recorded, model):
    """A reader who knows the rule must not learn from the answer which case it was."""
    from churnsense.evaluation import release

    def answer() -> tuple:
        body = _ok(served.get("/api/performance"))
        return body["test"] is None, body["test_reason"]

    answers = set()
    for metrics in (
        None,
        {**recorded["metrics"], "tp": 0, "fp": 350, "tn": 350, "fn": 300},
        {**recorded["metrics"], "fn": 0, "tp": 300, "fp": 200, "tn": 500},
        {**recorded["metrics"], "brier": 0.1},
    ):
        _record_with(recorded, model, metrics=metrics)
        answers.add(answer())
    _seal(model, None, state="reserved")
    answers.add(answer())
    release.LEDGER_FILE.write_text("not json")
    answers.add(answer())
    assert answers == {(True, TEST_WITHHELD_REASON)}


#: What a reader might try. Unknown parameters are ignored, so they narrow nothing.
_TEST_LISTINGS = (
    {"partition": "test"},
    {"partition": "test", "flagged": "true"},
    {"partition": "test", "flagged": "false"},
    {"partition": "test", "flagged": "true", "contract": "Month-to-month"},
    {"partition": "test", "flagged": "true", "contract": "One year", "internet": "DSL"},
    {"partition": "test", "contract": "Two year", "tenure_min": 0, "tenure_max": 24},
    {"partition": "test", "payment": "Electronic check", "bands": ["High", "Critical"]},
    {"partition": "test", "sort": "at_risk", "dir": "asc", "page": 2, "page_size": 5},
    {"partition": "test", "sort": "id", "dir": "desc", "page_size": 100},
)


@pytest.mark.parametrize("params", _TEST_LISTINGS)
def test_a_test_listing_holds_no_outcome_whatever_narrows_it(served, ids, recorded, params):
    body = _ok(served.get("/api/customers", params=params))
    assert body["summary"]["observed"] is None
    assert {r["id"] for r in body["rows"]} <= ids["test"]
    for row in body["rows"]:
        assert row["outcome"] == "withheld"
        assert not {"churn", "Churn", "label", "churned"} & set(row)


@pytest.mark.parametrize("attempt", ["outcome=churned", "churn=1", "Churn=Yes", "label=1"])
def test_asking_for_an_outcome_narrows_nothing(served, attempt):
    base = _ok(served.get("/api/customers", params={"partition": "test"}))
    tried = _ok(served.get(f"/api/customers?partition=test&{attempt}"))
    assert tried["total"] == base["total"]
    assert [r["id"] for r in tried["rows"]] == [r["id"] for r in base["rows"]]


@pytest.mark.parametrize("key", ["outcome", "churn", "Churn", "churned", "label"])
def test_a_listing_cannot_be_sorted_by_an_outcome(served, key):
    response = served.get("/api/customers", params={"partition": "test", "sort": key})
    assert response.status_code == 422


def test_a_test_customers_id_opens_no_outcome(served, ids, recorded):
    chosen = sorted(ids["test"])[:5]
    for customer_id in chosen:
        assert _ok(served.get(f"/api/customers/{customer_id}"))["outcome"] == "withheld"
    selection = _ok(served.get("/api/customers", params={"ids": chosen}))
    assert selection["summary"]["observed"] is None
    assert {r["outcome"] for r in selection["rows"]} == {"withheld"}
    explained = _ok(served.post("/api/explain", json={"customer_ids": chosen[:1]}))
    assert "outcome" not in json.dumps(explained)
    assert "Churn" not in explained["explanations"][0]


def test_a_test_export_holds_no_outcome(served, ids):
    text = served.get("/api/customers/export", params={"partition": "test"}).text
    frame = pd.read_csv(io.StringIO(text), dtype=str)
    assert schema.TARGET not in frame.columns
    assert set(frame["outcome"]) == {"withheld"}


def _rounded(value):
    """``value`` with every float to nine places, at any depth."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


@pytest.mark.parametrize("with_ids", [True, False])
def test_a_batch_answers_alike_whatever_label_a_test_customer_is_given(
    served, ids, raw_frame, with_ids
):
    """Guessing a held-out customer's label and watching the response tells nothing."""
    rows = raw_frame[raw_frame["customerID"].isin(ids["test"])].head(40)
    if not with_ids:
        rows = rows.drop(columns=["customerID"])
    answers = [
        _ok(_upload(served, rows.assign(**{schema.TARGET: label}))) for label in ("Yes", "No")
    ]
    tables = [pd.read_csv(io.StringIO(answer.pop("csv"))) for answer in answers]
    pd.testing.assert_frame_equal(tables[0], tables[1])
    # Scoring may differ in the last bits between two calls; a label would move far more.
    assert _rounded(answers[0]) == _rounded(answers[1])
    assert answers[0]["summary"]["labels"]["labelled"] == 0
    assert answers[0]["summary"]["labels"]["excluded_test"] == 40
    assert all(row["label"] is None for row in answers[0]["rows"])


def test_a_group_of_exactly_twenty_keeps_its_figures_and_nineteen_loses_them():
    row = {"customers": 20, "churners": 3, "precision": 0.5, "unscanned": False}
    assert insights.small_group(row) == row
    small = insights.small_group({**row, "customers": 19})
    assert small["customers"] == 19
    assert all(small[k] is None for k in insights.SEGMENT_OUTCOMES)


def test_a_reliability_bin_of_twenty_has_a_rate_and_nineteen_has_none():
    import numpy as np

    y = np.array([1] * 5 + [0] * 15 + [1] * 2 + [0] * 17)
    p = np.array([0.35] * 20 + [0.75] * 19)
    bins = insights.reliability_bins(y, p)
    assert [(b["low"], b["customers"], b["churned"]) for b in bins] == [
        (pytest.approx(0.3), 20, 5),
        (pytest.approx(0.7), 19, None),
    ]
    assert bins[0]["rate"] == pytest.approx(0.25) and bins[1]["rate"] is None


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
    performance = _ok(served.get("/api/performance"))
    assert performance["withheld_reason"] == overview["withheld_reason"]
    assert performance["validation"] is None and performance["validation_evaluation"] is None
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


#: What a browser's address bar sends; fetch, curl and TestClient send */*.
NAVIGATION = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


@pytest.mark.parametrize(
    "path", ["/no-such-page", "/a/b/c", "/explorer/x", "/index.html", "/..%2F..%2Fsecret.txt"]
)
def test_an_unknown_page_address_gets_the_app_shell_with_404(served, built, path):
    from churnsense.api import middleware

    response = served.get(path, headers=NAVIGATION)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (built / "index.html").read_text()
    assert response.headers["content-security-policy"] == middleware.CONTENT_SECURITY_POLICY
    assert response.headers["cache-control"] == "no-store"


def test_a_page_address_with_a_trailing_slash_redirects_to_the_page(served, built):
    response = served.get("/explorer/", headers=NAVIGATION, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/explorer")


def test_a_head_request_for_an_unknown_page_gets_404_without_a_body(served, built):
    response = served.head("/no-such-page", headers=NAVIGATION)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == b""


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/api/nope", "not_found"),
        ("/api", "not_found"),
        ("/assets/missing.js", "not_found"),
        ("/docs/x", "not_found"),
        ("/redoc/x", "not_found"),
        ("/openapi.json/x", "not_found"),
        ("/favicon.svg/x", "not_found"),
        ("/health/x", "not_found"),
        ("/predict/x", "not_found"),
        ("/model-info/x", "not_found"),
        ("/api/customers/NOPE-00000", "not_found"),
    ],
)
def test_api_asset_and_docs_paths_keep_their_json_404_for_a_browser_too(served, built, path, error):
    response = served.get(path, headers=NAVIGATION)
    assert response.status_code == 404
    assert response.json()["error"] == error


def test_only_a_browser_navigation_gets_the_shell(served, built):
    # A program asking for JSON, or anything that is not a GET, keeps the JSON 404.
    as_json = served.get("/no-such-page", headers={"Accept": "application/json"})
    assert as_json.json()["error"] == "not_found"
    assert served.post("/no-such-page", headers=NAVIGATION).json()["error"] == "not_found"
    # A path some route claims for another method stays a 405, never a page.
    assert served.get("/predict", headers=NAVIGATION).status_code == 405
    assert served.post("/", headers=NAVIGATION).status_code == 405


def test_without_a_build_an_unknown_page_keeps_the_json_404(served, tmp_path, monkeypatch):
    from churnsense.api import spa

    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path / "missing")
    response = served.get("/no-such-page", headers=NAVIGATION)
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_without_a_build_a_page_tells_a_browser_how_to_make_one(served, tmp_path, monkeypatch):
    from churnsense.api import spa

    monkeypatch.setattr(spa, "FRONTEND_DIST", tmp_path / "missing")
    assert served.get("/explorer", headers=NAVIGATION).json()["error"] == "frontend_not_built"


@pytest.mark.parametrize("status", [400, 403, 405, 409, 429, 500, 503])
def test_only_a_404_ever_gets_the_shell(built, status):
    # Today every other error comes from a reserved path; the contract holds for a page too.
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.requests import Request

    from churnsense.api import main, spa

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/explorer",
        "query_string": b"",
        "headers": [(b"accept", NAVIGATION["Accept"].encode())],
        "app": main.app,
    }
    page = spa.not_found_page(Request(scope), StarletteHTTPException(status_code=404))
    assert page is not None and page.status_code == 404
    assert spa.not_found_page(Request(scope), StarletteHTTPException(status_code=status)) is None


def test_a_404_that_names_its_own_error_keeps_it_even_on_a_page_address(built):
    # frontend_not_built is raised by a page route; a build finishing meanwhile
    # must not turn it into the shell.
    from starlette.requests import Request

    from churnsense.api import main, spa

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/explorer",
        "query_string": b"",
        "headers": [(b"accept", NAVIGATION["Accept"].encode())],
        "app": main.app,
    }
    assert spa.not_found_page(Request(scope), spa.FrontendNotBuiltError()) is None


def test_the_accept_header_is_read_without_regard_to_case(served, built):
    # Media types are case-insensitive (RFC 9110 §8.3.1).
    response = served.get("/no-such-page", headers={"Accept": "TEXT/HTML"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")


def test_a_405_for_a_browser_is_the_json_error_response(served, built):
    response = served.get("/predict", headers=NAVIGATION)
    assert response.status_code == 405
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"] == "method_not_allowed"


def test_a_head_request_for_a_reserved_path_keeps_the_json_404(served, built):
    response = served.head("/api/nope", headers=NAVIGATION)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_the_reserved_first_segments_are_the_apps_own_non_page_routes():
    from churnsense.api import main, spa

    assert spa.reserved_segments(main.app) == {
        "api",
        "assets",
        "docs",
        "favicon.svg",
        "health",
        "model-info",
        "openapi.json",
        "predict",
        "ready",
        "redoc",
    }


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


def _directives(policy: str) -> dict[str, set[str]]:
    """A Content-Security-Policy as directive -> sources, order and spacing aside."""
    pairs = (part.split() for part in policy.split(";") if part.strip())
    return {name: set(sources) for name, *sources in pairs}


def test_the_app_policy_is_exactly_the_reviewed_one(served, built):
    # Written out, not read from middleware: loosening the policy (an added
    # 'unsafe-inline', a CDN, a dropped directive) has to change this test too.
    reviewed = {
        "default-src": {"'self'"},
        "script-src": {"'self'"},
        "style-src": {"'self'"},
        "img-src": {"'self'", "data:"},
        "font-src": {"'self'"},
        "connect-src": {"'self'"},
        "frame-ancestors": {"'none'"},
        "base-uri": {"'none'"},
        "form-action": {"'self'"},
    }
    for response in (served.get("/"), served.get("/api/overview")):
        assert _directives(response.headers["content-security-policy"]) == reviewed


def test_the_docs_policy_is_exactly_the_reviewed_one(served):
    cdn = "https://cdn.jsdelivr.net"
    reviewed = {
        "default-src": {"'none'"},
        "script-src": {cdn, "'unsafe-inline'"},
        "style-src": {cdn, "https://fonts.googleapis.com", "'unsafe-inline'"},
        "font-src": {"https://fonts.gstatic.com"},
        "img-src": {"'self'", "data:", "https://fastapi.tiangolo.com", "https://cdn.redoc.ly"},
        "connect-src": {"'self'"},
        "worker-src": {"blob:"},
        "frame-ancestors": {"'none'"},
        "base-uri": {"'none'"},
        "form-action": {"'none'"},
    }
    for path in ("/docs", "/redoc"):
        assert _directives(served.get(path).headers["content-security-policy"]) == reviewed


def test_the_docs_pages_get_their_own_narrow_policy(served):
    # Parsed, so the CDN has to be a whole source of the directives that load
    # from it, not a substring found anywhere in the header.
    docs = _directives(served.get("/docs").headers["content-security-policy"])
    cdn = "https://cdn.jsdelivr.net"
    assert cdn in docs["script-src"] and cdn in docs["style-src"]
    assert docs["frame-ancestors"] == {"'none'"}
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
        '{"state": "published", "metrics": NaN}',
        '{"state": "published", "threshold": 1e400}',
        "[" * 200_000 + "]" * 200_000,
        "[" + "9" * 5000 + "]",
    ],
    ids=["empty-object", "not-json", "nan", "overflow", "deep-nesting", "enormous-integer"],
)
def test_a_malformed_ledger_is_withheld_and_takes_no_page_down(served, recorded, text):
    from churnsense.evaluation import release

    release.LEDGER_FILE.write_text(text)
    for route in ("/model-info", "/api/model-info"):
        info = _ok(served.get(route))
        assert info["test_evaluation"] is None and info["test_metrics"] is None
    body = _ok(served.get("/api/performance"))
    assert body["test"] is None
    assert body["test_reason"] == TEST_WITHHELD_REASON


@pytest.mark.parametrize(
    "damage",
    ["not json", "[1, 2]", "mismatch", "missing value", "unknown category", "importance extra key"],
)
def test_a_damaged_shap_cache_is_ignored_not_fatal(served, environment, damage):
    artifacts, _ = environment
    path = artifacts / "shap_global.json"
    original = path.read_text()
    matrix_damage = {"mismatch", "missing value", "unknown category", "importance extra key"}
    try:
        if damage in matrix_damage:
            cache = json.loads(original)
            if damage == "mismatch":
                cache["values"] = [row[:-1] for row in cache["values"]]
            elif damage == "missing value":
                del cache["x"][0]["tenure"]
            elif damage == "importance extra key":
                cache["importance"][0]["note"] = "x"
            else:
                cache["x"][0]["Contract"] = "Weekly"
            path.write_text(json.dumps(cache))
        else:
            path.write_text(damage)
        overview = _ok(served.get("/api/overview"))
        if damage not in matrix_damage:
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
    assert body["summary"]["at_risk"] == pytest.approx(sum(r["at_risk"] or 0 for r in body["rows"]))


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


def _tab_with(monkeypatch, shown: list[tuple[str, int, int | None]]):
    """``_segment_tab`` of a Contract breakdown whose levels are (name, customers, churned)."""
    from churnsense.api import dashboard

    cells = [
        insights.Cell(name, None, n, None, None, None, "cross_partition")
        if c is None
        else insights.Cell(name, None, n, c, c / n, 1.0, None, *wilson_interval(c, n))
        for name, n, c in shown
    ]
    split = insights.Breakdown("Contract", None, 3000, 900, 0.3, cells, [], cells, [])
    monkeypatch.setattr(dashboard, "_breakdown", lambda _d, _key: split)
    return dashboard._segment_tab(None, "Contract")


def test_a_tab_with_one_shown_level_states_no_spread_and_no_peak(monkeypatch):
    """With only Month-to-month shown there is nothing to compare: not a 1.0x spread."""
    tab = _tab_with(
        monkeypatch,
        [("Month-to-month", 1500, 600), ("One year", 700, None), ("Two year", 800, None)],
    )
    assert tab.spread is None and tab.peak is None


def test_a_tab_with_two_shown_levels_compares_them(monkeypatch):
    tab = _tab_with(
        monkeypatch, [("Month-to-month", 1500, 600), ("One year", 700, 70), ("Two year", 800, None)]
    )
    assert tab.spread == pytest.approx(0.4 / 0.1) and tab.peak == "Month-to-month"
