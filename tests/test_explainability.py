"""Tests for SHAP explanations and the human-readable narratives."""

from __future__ import annotations

import numpy as np
import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.explainability.shap_explain import (
    FeatureContribution,
    explain_customer,
    global_importance,
    narrate,
)
from churnsense.models.train import train_all


@pytest.fixture(scope="module")
def trained(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    return outcome, splits


def test_global_importance_ranks_every_input_feature(trained):
    outcome, splits = trained
    importance = global_importance(outcome.model, splits.X_val.head(120))

    assert set(importance["feature"]) <= set(splits.X_val.columns)
    assert list(importance["mean_abs_shap"]) == sorted(importance["mean_abs_shap"], reverse=True)
    assert (importance["mean_abs_shap"] >= 0).all()


def test_global_importance_aggregates_one_hot_columns_back_to_the_source(trained):
    """A reader thinks in 'Contract', not in 'Contract_One year'."""
    outcome, splits = trained
    importance = global_importance(outcome.model, splits.X_val.head(120))
    assert "Contract" in set(importance["feature"])
    assert not any("_" in f and f not in splits.X_val.columns for f in importance["feature"])


def test_local_explanation_contributions_are_signed(trained):
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0)

    assert result.contributions
    assert all(isinstance(c, FeatureContribution) for c in result.contributions)
    assert any(c.shap_value > 0 for c in result.contributions) or any(
        c.shap_value < 0 for c in result.contributions
    )


def test_local_contributions_are_ordered_by_magnitude(trained):
    outcome, splits = trained
    values = [
        abs(c.shap_value)
        for c in explain_customer(outcome.model, splits.X_val.head(60), row=0).contributions
    ]
    assert values == sorted(values, reverse=True)


def test_local_explanation_reports_the_model_s_own_probability(trained):
    """The explanation must describe the prediction actually served."""
    outcome, splits = trained
    sample = splits.X_val.head(60)
    result = explain_customer(outcome.model, sample, row=3)
    expected = outcome.model.predict_proba(sample.iloc[[3]])[:, 1][0]
    assert result.probability == pytest.approx(expected, abs=1e-9)


def test_local_explanation_rejects_an_out_of_range_row(trained):
    outcome, splits = trained
    with pytest.raises(IndexError):
        explain_customer(outcome.model, splits.X_val.head(5), row=99)


def test_narratives_are_association_not_causation(trained):
    """Wording is a correctness requirement here, not a style preference."""
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0)
    text = " ".join(narrate(result)).lower()

    for forbidden in ("causes", "will prevent", "guarantees", "because of this feature"):
        assert forbidden not in text, forbidden
    assert any(word in text for word in ("associated", "contributes", "raises", "lowers"))


def test_narrative_covers_the_top_contributions(trained):
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0, top_k=4)
    assert len(result.contributions) == 4
    assert len(narrate(result)) == 4


def test_narrative_uses_human_labels_not_raw_column_names(trained):
    outcome, splits = trained
    result = explain_customer(outcome.model, splits.X_val.head(60), row=0, top_k=6)
    text = " ".join(narrate(result))
    assert "MonthlyCharges" not in text
    assert "OnlineSecurity" not in text


def test_explanations_are_deterministic(trained):
    outcome, splits = trained
    sample = splits.X_val.head(80)
    first = global_importance(outcome.model, sample)
    second = global_importance(outcome.model, sample)
    np.testing.assert_allclose(first["mean_abs_shap"], second["mean_abs_shap"])
