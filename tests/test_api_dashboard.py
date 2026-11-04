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
                "reason": "too_few" if cell["n"] < 20 else "complementary",
            }
        else:
            assert cell["n"] >= 20 and 0 <= cell["rate"] <= 1
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
    body = _ok(served.get("/api/segments", params={"rows": "Contract", "cols": "PaymentMethod"}))
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


def test_a_reported_group_counts_the_customers_the_explorer_lists_for_it(served):
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

    assert set(insights.SEGMENT_OUTCOMES) <= set(out.HoldoutSegment.model_fields)
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


def test_a_recorded_evaluation_carries_its_rate_and_intervals(served, recorded):
    test = _ok(served.get("/api/performance"))["test"]
    metrics = test["metrics"]
    assert test["churn_rate"] == pytest.approx(metrics["positives"] / metrics["n"])
    big = [s for s in test["segments"] if s["customers"] >= insights.MIN_CELL and s["flagged"]]
    assert big, "the record has no segment to read an interval from"
    for s in big:
        assert s["precision_low"] <= s["precision"] <= s["precision_high"]


def test_a_record_written_before_intervals_still_loads(served, recorded, environment):
    _, reports = environment
    older = {
        **recorded,
        "segments": [
            {k: v for k, v in s.items() if k not in (*_INTERVALS, "roc_auc")}
            for s in recorded["segments"]
        ],
    }
    (reports / "final_evaluation.json").write_text(json.dumps(older, allow_nan=False))
    test = _ok(served.get("/api/performance"))["test"]
    assert test is not None
    assert all(s[k] is None for s in test["segments"] for k in (*_INTERVALS, "roc_auc"))


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
    labels = _ok(_upload(served, upload))["summary"]["labels"]
    assert (labels["labelled"], labels["excluded_test"]) == (15, 10)
    assert labels["precision"] is None and labels["recall"] is None


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


def test_the_recorded_test_segments_lose_their_rates_below_twenty(served, recorded):
    segments = _ok(served.get("/api/performance"))["test"]["segments"]
    small = [s for s in segments if s["customers"] < insights.MIN_CELL]
    assert small, "the record has no small segment"
    for segment in small:
        assert all(segment[k] is None for k in insights.SEGMENT_OUTCOMES), segment


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
