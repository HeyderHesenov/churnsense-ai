"""Tests for SHAP explanations and the human-readable narratives."""

from __future__ import annotations

import numpy as np
import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.exceptions import DataError
from churnsense.explainability.shap_explain import (
    FeatureContribution,
    _shap_values,
    background_sample,
    explain_customer,
    global_importance,
    narrate,
)
from churnsense.models.train import train_all
from tests.conftest import pinned_split_digest, record_provenance

#: Structural tests do not need the production budget: additivity holds for
#: any number of permutations. Only the stability test uses the real one.
FAST = 3


@pytest.fixture(scope="module")
def trained(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    return outcome, splits


def test_global_importance_ranks_every_raw_feature_once(trained):
    """A reader thinks in 'Contract', not in 'Contract_One year'."""
    outcome, splits = trained
    importance = global_importance(outcome.model, splits.X_val.head(30), permutations=FAST)

    assert sorted(importance["feature"]) == sorted(splits.X_val.columns)
    assert list(importance["mean_abs_shap"]) == sorted(importance["mean_abs_shap"], reverse=True)
    assert (importance["mean_abs_shap"] >= 0).all()


def test_contributions_reconcile_with_the_served_probability(trained):
    """SHAP efficiency: base value plus every contribution is the probability
    the customer is actually shown - clamp and calibration included."""
    outcome, splits = trained
    sample = splits.X_val.head(30)
    for row in (0, 11, 29):
        result = explain_customer(
            outcome.model, sample, row=row, top_k=sample.shape[1], permutations=FAST
        )
        total = result.base_value + sum(c.shap_value for c in result.contributions)
        assert total == pytest.approx(result.probability, abs=1e-9)


def test_explanations_do_not_hinge_on_the_seed(trained):
    """Regression: with one permutation per customer, changing nothing but the
    seed reshuffled the top contributors for over a third of real customers.

    The instability comes from isotonic calibration's step function, so the
    model here is isotonic-calibrated like the shipped one (the fixture is too
    small for training to choose isotonic itself). Measured on this fixture:
    spread is ~0.21 of the typical contribution at one permutation and ~0.07
    at the production budget.
    """
    from sklearn.calibration import CalibratedClassifierCV

    from churnsense.models.calibration_clamp import ProbabilityClamp
    from churnsense.models.registry import build_candidate

    _, splits = trained
    stepwise = CalibratedClassifierCV(
        build_candidate("logistic_regression", list(splits.X_train.columns), seed=0),
        method="isotonic",
        cv=3,
    ).fit(splits.X_train, splits.y_train)
    model = ProbabilityClamp(stepwise, epsilon=0.001)
    sample, background = splits.X_val.head(8), background_sample(splits.X_train, 0)

    first = _shap_values(model, sample, background, seed=0)
    second = _shap_values(model, sample, background, seed=1)

    spread = (first - second).abs().to_numpy().mean()
    assert spread < 0.12 * first.abs().to_numpy().mean()


def test_local_explanation_contributions_are_signed(trained):
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0, permutations=FAST)

    assert result.contributions
    assert all(isinstance(c, FeatureContribution) for c in result.contributions)
    assert any(c.shap_value > 0 for c in result.contributions) or any(
        c.shap_value < 0 for c in result.contributions
    )


def test_local_contributions_are_ordered_by_magnitude(trained):
    outcome, splits = trained
    values = [
        abs(c.shap_value)
        for c in explain_customer(
            outcome.model, splits.X_val.head(60), row=0, permutations=FAST
        ).contributions
    ]
    assert values == sorted(values, reverse=True)


def test_local_explanation_reports_the_model_s_own_probability(trained):
    """The explanation must describe the prediction actually served."""
    outcome, splits = trained
    sample = splits.X_val.head(60)
    result = explain_customer(outcome.model, sample, row=3, permutations=FAST)
    expected = outcome.model.predict_proba(sample.iloc[[3]])[:, 1][0]
    assert result.probability == pytest.approx(expected, abs=1e-9)


def test_local_explanation_rejects_an_out_of_range_row(trained):
    outcome, splits = trained
    with pytest.raises(IndexError):
        explain_customer(outcome.model, splits.X_val.head(5), row=99, permutations=FAST)


def test_narratives_are_association_not_causation(trained):
    """Wording is a correctness requirement here, not a style preference."""
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0, permutations=FAST)
    text = " ".join(narrate(result)).lower()

    for forbidden in ("causes", "will prevent", "guarantees", "because of this feature"):
        assert forbidden not in text, forbidden
    assert any(word in text for word in ("associated", "contributes", "raises", "lowers"))


def test_narrative_covers_the_top_contributions(trained):
    outcome, splits = trained
    result = explain_customer(
        outcome.model, splits.X_val.head(60), row=0, top_k=4, permutations=FAST
    )
    assert len(result.contributions) == 4
    assert len(narrate(result)) == 4


def test_narrative_uses_human_labels_not_raw_column_names(trained):
    outcome, splits = trained
    result = explain_customer(
        outcome.model, splits.X_val.head(60), row=0, top_k=6, permutations=FAST
    )
    text = " ".join(narrate(result))
    assert "MonthlyCharges" not in text
    assert "OnlineSecurity" not in text


def test_explanations_are_deterministic(trained):
    outcome, splits = trained
    sample = splits.X_val.head(20)
    first = global_importance(outcome.model, sample, permutations=FAST)
    second = global_importance(outcome.model, sample, permutations=FAST)
    np.testing.assert_allclose(first["mean_abs_shap"], second["mean_abs_shap"])


# --- the cached global ranking -----------------------------------------------


@pytest.fixture
def no_shap(monkeypatch) -> list:
    """Every SHAP computation compute_and_cache makes; returns fixed contributions."""
    import pandas as pd

    from churnsense.evaluation import figures
    from churnsense.explainability import shap_explain

    calls: list = []

    def contributions(model, explained, background, **_):
        calls.append(explained)
        values = pd.DataFrame(0.01, index=explained.index, columns=explained.columns)
        values.attrs["base_value"] = 0.2
        return values

    monkeypatch.setattr(shap_explain, "_shap_values", contributions)
    monkeypatch.setattr(figures, "shap_importance", lambda *_, **__: None)
    return calls


@pytest.mark.parametrize(
    "provenance",
    [
        {"data_sha256": "0" * 64},  # another file
        {"data_sha256": None},  # no file recorded
        {"split_digest": "f" * 64},  # same file, another split (seed or sizes changed)
    ],
)
def test_the_cached_ranking_is_refused_for_partitions_the_model_was_not_trained_on(
    pipeline_cfg, no_shap, provenance
):
    """The cache says it explains validation customers. After a data or split
    change without a retrain, the rows rebuilt as "validation" are not the
    model's validation rows - some it was trained on."""
    from churnsense.data.download import sha256_of
    from churnsense.explainability.shap_explain import compute_and_cache

    recorded = {
        "data_sha256": sha256_of(pipeline_cfg.raw_data_file),
        "split_digest": pinned_split_digest(pipeline_cfg),
        **provenance,
    }
    record_provenance(pipeline_cfg, **recorded)

    with pytest.raises(DataError):
        compute_and_cache(pipeline_cfg)
    assert not no_shap
    assert not pipeline_cfg.paths.shap_cache_file.exists()


def test_the_ranking_is_cached_for_the_partitions_the_model_records(pipeline_cfg, no_shap):
    import json

    from churnsense.data.download import sha256_of
    from churnsense.explainability.shap_explain import compute_and_cache

    record_provenance(
        pipeline_cfg,
        data_sha256=sha256_of(pipeline_cfg.raw_data_file),
        split_digest=pinned_split_digest(pipeline_cfg),
    )
    compute_and_cache(pipeline_cfg)

    cached = json.loads(pipeline_cfg.paths.shap_cache_file.read_text())
    assert len(no_shap) == 1
    assert cached["partition"] == "validation"
    assert (
        cached["model_sha256"]
        == json.loads(pipeline_cfg.paths.model_meta_file.read_text())["model_sha256"]
    )


def test_make_explain_reports_a_refusal_instead_of_a_traceback(
    pipeline_cfg, no_shap, monkeypatch, caplog
):
    from churnsense.explainability import shap_explain

    record_provenance(pipeline_cfg, data_sha256="0" * 64, split_digest="f" * 64)
    monkeypatch.setattr(shap_explain, "load_config", lambda: pipeline_cfg)
    # The real one replaces the root handlers on first use, caplog's included.
    monkeypatch.setattr(shap_explain, "configure_logging", lambda: None)

    assert shap_explain.main([]) == 1
    assert "dataset differs" in caplog.text
    assert not no_shap


# --- the matrix the explainability page draws ---------------------------------------


def test_the_cache_payload_reproduces_the_served_probabilities(trained, cfg: Config):
    from churnsense.explainability.shap_explain import explanation_cache

    outcome, splits = trained
    explained = splits.X_val.head(8)
    meta = {"model_key": "lr", "trained_at": "t", "model_sha256": "a" * 64}
    payload = explanation_cache(
        outcome.model,
        meta,
        explained,
        background_sample(splits.X_train, cfg.random_seed),
        seed=cfg.random_seed,
        permutations=FAST,
    )
    assert payload["features"] == list(explained.columns)
    assert len(payload["values"]) == len(payload["x"]) == 8
    served = outcome.model.predict_proba(explained)[:, 1]
    for row, p in zip(payload["values"], served, strict=True):
        assert payload["base_value"] + sum(row) == pytest.approx(p, abs=1e-9)
    assert payload["max_additivity_gap"] < 1e-9
    assert {r["feature"] for r in payload["importance"]} == set(payload["features"])


def test_a_cache_for_another_artifact_or_without_the_matrix_is_not_served(tmp_path):
    import json

    from churnsense.explainability.shap_explain import load_cached_explanations, read_cache

    path = tmp_path / "shap_global.json"
    assert read_cache(path, "a" * 64) is None
    path.write_text(json.dumps({"model_sha256": "a" * 64, "importance": []}))
    assert read_cache(path, "a" * 64) is not None
    assert read_cache(path, "b" * 64) is None
    assert load_cached_explanations(path, "a" * 64) is None  # an older cache: ranking only
    path.write_text(
        json.dumps({"model_sha256": "a" * 64, "features": ["x"], "values": [[0.1, 0.2]], "x": [{}]})
    )
    assert load_cached_explanations(path, "a" * 64) is None  # a matrix that does not fit
    for damaged in ("not json", "[1, 2]"):
        path.write_text(damaged)
        assert read_cache(path, "a" * 64) is None


# --- dependence summaries: what the page draws behind the dots ------------------------


def _book(n: int, **columns) -> tuple[list[str], list[list[float]], list[dict]]:
    """A cache-shaped matrix: ``columns`` maps a feature to (its values, its contributions)."""
    features = list(columns)
    x = [{f: columns[f][0][i] for f in features} for i in range(n)]
    values = [[columns[f][1][i] for f in features] for i in range(n)]
    return features, values, x


def test_dependence_groups_account_for_every_customer_and_keep_the_feature_order():
    from churnsense.explainability.shap_explain import dependence

    tenure = [float(t) for t in range(0, 73, 3)]  # 25 customers
    contract = ["Month-to-month", "One year", "Two year", "One year", "Month-to-month"] * 5
    features, values, x = _book(
        25,
        tenure=(tenure, [t / 1000 for t in tenure]),
        Contract=(contract, [0.01 * (i % 7) for i in range(25)]),
    )
    summary = dependence(features, values, x)
    assert [s.feature for s in summary] == ["tenure", "Contract"]
    assert sum(b.customers for b in summary[0].bins) == 25
    assert sum(level.customers for level in summary[1].levels) == 25


def test_a_category_mean_is_the_mean_of_its_unrounded_contributions():
    from churnsense.explainability.shap_explain import dependence

    contributions = [0.1234564, 0.1234566, 0.2, 0.2, 0.2, -0.05, -0.05, -0.05, -0.05, -0.05]
    contract = ["Month-to-month"] * 2 + ["One year"] * 3 + ["Two year"] * 5
    (summary,) = dependence(*_book(10, Contract=(contract, contributions)))
    by_level = {level.category: level for level in summary.levels}
    assert by_level["Two year"].mean == -0.05
    assert by_level["Two year"].customers == 5
    assert by_level["One year"].mean is None  # 3 customers: too few for a stable mean
    assert by_level["Month-to-month"].mean is None


def test_every_vocabulary_level_is_listed_in_contract_order_even_when_absent():
    from churnsense.data import schema
    from churnsense.explainability.shap_explain import dependence

    (summary,) = dependence(*_book(6, PaymentMethod=(["Mailed check"] * 6, [0.01] * 6)))
    assert [level.category for level in summary.levels] == list(
        schema.ALLOWED_CATEGORIES["PaymentMethod"]
    )
    absent = [level for level in summary.levels if level.category != "Mailed check"]
    assert all(level.customers == 0 and level.mean is None for level in absent)


def test_five_customers_carry_a_mean_and_four_do_not():
    from churnsense.explainability.shap_explain import DEPENDENCE_MIN_CUSTOMERS, dependence

    assert DEPENDENCE_MIN_CUSTOMERS == 5
    partner = ["Yes"] * 5 + ["No"] * 4
    contributions = [0.02, 0.04, 0.06, 0.08, 0.1, 0.3, 0.3, 0.3, 0.3]
    (summary,) = dependence(*_book(9, Partner=(partner, contributions)))
    by_level = {level.category: level for level in summary.levels}
    assert by_level["Yes"].mean == pytest.approx(0.06, abs=1e-12)
    assert (by_level["No"].customers, by_level["No"].mean) == (4, None)


def test_numeric_bins_are_right_closed_and_the_first_holds_the_minimum():
    from churnsense.explainability.shap_explain import DEPENDENCE_BINS, dependence

    assert DEPENDENCE_BINS == 12
    tenure = [0.0, 6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0, 48.0, 54.0, 60.0, 66.0, 72.0]
    (summary,) = dependence(*_book(13, tenure=(tenure, [0.0] * 13)))
    first, second, last = summary.bins[0], summary.bins[1], summary.bins[-1]
    assert (first.low, first.high, first.customers) == (0.0, 6.0, 2)  # 0 and 6
    assert (second.low, second.high, second.customers) == (6.0, 12.0, 1)  # 12 only
    assert (last.low, last.high, last.customers) == (66.0, 72.0, 1)  # 72 only
    assert len(summary.bins) == 12


def test_empty_bins_are_dropped_and_the_kept_ones_sit_on_one_grid_from_min_to_max():
    from churnsense.explainability.shap_explain import DEPENDENCE_BINS, dependence

    charges = [20.0] * 6 + [21.0] * 5 + [116.0] * 5
    (summary,) = dependence(*_book(16, MonthlyCharges=(charges, [0.01] * 16)))
    width = (116.0 - 20.0) / DEPENDENCE_BINS
    assert summary.bins[0].low == 20.0
    assert summary.bins[-1].high == 116.0
    assert [b.customers for b in summary.bins] == [11, 5]
    for b in summary.bins:
        steps = (b.low - 20.0) / width
        assert steps == pytest.approx(round(steps), abs=1e-9)
        assert b.high - b.low == pytest.approx(width, abs=1e-9)


def test_a_numeric_bin_mean_is_rounded_and_withheld_below_five():
    from churnsense.explainability.shap_explain import dependence

    tenure = [1.0] * 5 + [72.0] * 4
    contributions = [0.1000004] * 5 + [0.2] * 4
    (summary,) = dependence(*_book(9, tenure=(tenure, contributions)))
    assert summary.bins[0].mean == 0.1
    assert (summary.bins[-1].customers, summary.bins[-1].mean) == (4, None)


def test_a_group_mean_is_rounded_after_averaging_not_before():
    from churnsense.explainability.shap_explain import dependence

    # Rounded first, each 4e-7 is 0 and the mean is 2e-7 -> 0.0; the true mean is 6e-7 -> 1e-6.
    contributions = [4e-7] * 4 + [1.4e-6]
    (numeric,) = dependence(*_book(5, tenure=([3.0] * 5, contributions)))
    (categorical,) = dependence(*_book(5, Partner=(["Yes"] * 5, contributions)))
    assert numeric.bins[0].mean == 1e-6
    assert {level.category: level.mean for level in categorical.levels}["Yes"] == 1e-6


def test_no_explained_customers_summarise_to_empty_groups():
    from churnsense.data import schema
    from churnsense.explainability.shap_explain import dependence

    numeric, categorical = dependence(["tenure", "Contract"], [], [])
    assert numeric.bins == ()
    assert [(level.category, level.customers, level.mean) for level in categorical.levels] == [
        (category, 0, None) for category in schema.ALLOWED_CATEGORIES["Contract"]
    ]


def test_a_constant_numeric_column_is_one_bin():
    from churnsense.explainability.shap_explain import dependence

    (summary,) = dependence(*_book(7, tenure=([12.0] * 7, [0.03] * 7)))
    assert [(b.low, b.high, b.customers, b.mean) for b in summary.bins] == [(12.0, 12.0, 7, 0.03)]


def test_the_shipped_matrix_summarises_without_error(trained, cfg: Config):
    from churnsense.explainability.shap_explain import dependence, explanation_cache

    outcome, splits = trained
    payload = explanation_cache(
        outcome.model,
        {"model_key": "lr", "trained_at": "t", "model_sha256": "a" * 64},
        splits.X_val.head(20),
        background_sample(splits.X_train, cfg.random_seed),
        seed=cfg.random_seed,
        permutations=FAST,
    )
    summary = dependence(payload["features"], payload["values"], payload["x"])
    assert [s.feature for s in summary] == payload["features"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing key",
        "extra key",
        "unknown category",
        "number as text",
        "infinite number",
        "boolean number",
        "text contribution",
        "infinite contribution",
        "contribution past 1",
        "contribution far past 1",
        "row count",
        "unknown feature",
        "repeated feature",
        "row not an object",
        "unhashable feature",
        "huge integer",
        "empty matrix",
        "explained count off",
        "partition not text",
        "base value outside 0..1",
        "background not a count",
        "importance not a list",
        "no recorded gap",
        "gap too large",
    ],
)
def test_a_cache_whose_rows_disagree_with_its_features_is_not_served(tmp_path, damage):
    import json

    from churnsense.explainability.shap_explain import load_cached_explanations

    cache = {
        "model_sha256": "a" * 64,
        "partition": "validation",
        "n_explained": 2,
        "n_background": 100,
        "permutations": 10,
        "seed": 42,
        "base_value": 0.25,
        "importance": [],
        "max_additivity_gap": 1e-15,
        "features": ["tenure", "Contract"],
        "values": [[0.1, -0.2], [0.0, 0.05]],
        "x": [{"tenure": 3.0, "Contract": "One year"}, {"tenure": 40.0, "Contract": "Two year"}],
    }
    path = tmp_path / "shap_global.json"
    path.write_text(json.dumps(cache))
    assert load_cached_explanations(path, "a" * 64) is not None  # the undamaged control

    if damage == "missing key":
        del cache["x"][1]["Contract"]
    elif damage == "extra key":
        cache["x"][0]["Partner"] = "Yes"
    elif damage == "unknown category":
        cache["x"][0]["Contract"] = "Weekly"
    elif damage == "number as text":
        cache["x"][0]["tenure"] = "3"
    elif damage == "infinite number":
        cache["x"][0]["tenure"] = float("inf")
    elif damage == "boolean number":
        cache["x"][0]["tenure"] = True
    elif damage == "text contribution":
        cache["values"][1][0] = "0.1"
    elif damage == "infinite contribution":
        cache["values"][1][1] = float("nan")
    elif damage == "contribution past 1":
        cache["values"][0][1] = -1.0000001
    elif damage == "contribution far past 1":
        cache["values"][0][0] = 1e308  # its group mean would overflow to infinity
    elif damage == "row count":
        cache["values"].pop()
    elif damage == "unknown feature":
        cache["features"][1] = "customerID"
        cache["x"] = [{"tenure": 3.0, "customerID": "A"}, {"tenure": 40.0, "customerID": "B"}]
    elif damage == "repeated feature":
        cache["features"] = ["tenure", "tenure"]
        cache["x"] = [{"tenure": 3.0}, {"tenure": 40.0}]
    elif damage == "row not an object":
        cache["x"][1] = [40.0, "Two year"]
    elif damage == "unhashable feature":
        cache["features"][0] = ["tenure"]
    elif damage == "huge integer":
        cache["x"][0]["tenure"] = 10**400
    elif damage == "empty matrix":
        cache.update(values=[], x=[], n_explained=0)
    elif damage == "explained count off":
        cache["n_explained"] = 300
    elif damage == "partition not text":
        cache["partition"] = 1
    elif damage == "base value outside 0..1":
        cache["base_value"] = 1.5
    elif damage == "background not a count":
        cache["n_background"] = True
    elif damage == "importance not a list":
        cache["importance"] = {"tenure": 0.1}
    elif damage == "no recorded gap":
        del cache["max_additivity_gap"]
    elif damage == "gap too large":
        cache["max_additivity_gap"] = 2e-9
    path.write_text(json.dumps(cache))
    assert load_cached_explanations(path, "a" * 64) is None


def test_a_contribution_of_a_whole_probability_is_served(tmp_path):
    """A permutation-SHAP value of a probability is a mean of differences in [-1, 1]: both ends are real."""
    import json

    from churnsense.explainability.shap_explain import load_cached_explanations

    cache = {
        "model_sha256": "a" * 64,
        "partition": "validation",
        "n_explained": 2,
        "n_background": 100,
        "permutations": 10,
        "seed": 42,
        "base_value": 0.25,
        "importance": [],
        "max_additivity_gap": 1e-15,
        "features": ["tenure"],
        "values": [[1.0], [-1.0]],
        "x": [{"tenure": 3.0}, {"tenure": 40.0}],
    }
    path = tmp_path / "shap_global.json"
    path.write_text(json.dumps(cache))
    assert load_cached_explanations(path, "a" * 64) is not None


def test_a_ranking_whose_attributions_do_not_add_up_is_not_read(tmp_path):
    """The overview reads the same file: a recorded gap past tolerance is refused there too."""
    import json

    from churnsense.explainability.shap_explain import ADDITIVITY_TOLERANCE, read_cache

    path = tmp_path / "shap_global.json"
    for gap, served in [(1e-15, True), (ADDITIVITY_TOLERANCE, True), (2e-9, False), (-1.0, False)]:
        path.write_text(json.dumps({"model_sha256": "a" * 64, "max_additivity_gap": gap}))
        assert (read_cache(path, "a" * 64) is not None) is served, gap
    path.write_text('{"model_sha256": "' + "a" * 64 + '", "max_additivity_gap": NaN}')
    assert read_cache(path, "a" * 64) is None
    # A cache written before the gap was recorded keeps its ranking.
    path.write_text(json.dumps({"model_sha256": "a" * 64, "importance": []}))
    assert read_cache(path, "a" * 64) is not None
