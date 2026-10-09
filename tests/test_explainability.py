"""Tests for SHAP explanations and the human-readable narratives."""

from __future__ import annotations

import numpy as np
import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.explainability.shap_explain import (
    FeatureContribution,
    _shap_values,
    background_sample,
    explain_customer,
    global_importance,
    narrate,
)
from churnsense.models.train import train_all

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
    seed reshuffled the top drivers for over a third of real customers.

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
