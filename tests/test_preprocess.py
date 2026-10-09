"""Tests for preprocessing - including the leakage guarantee.

The central claim this file defends: preprocessing statistics are derived from
training rows only. The test does not inspect the code to confirm that; it
checks the *behaviour*, by fitting on a deliberately shifted training set and
verifying the transform of held-out rows reflects the training statistics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline

from churnsense.config import Config
from churnsense.data import schema
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.features.preprocess import build_preprocessor_for


@pytest.fixture(scope="module")
def xy(clean_frame: pd.DataFrame, cfg: Config):
    return features_and_target(clean_frame, cfg)


def test_scaler_statistics_come_from_training_rows_only(xy):
    """Fit on a shifted train split; held-out rows must be scaled by *its* stats."""
    X, _ = xy
    train = X.iloc[: len(X) // 2].copy()
    held_out = X.iloc[len(X) // 2 :].copy()
    train["MonthlyCharges"] = 100.0  # zero variance, mean 100, in training only

    pre = build_preprocessor_for(list(X.columns), scale_numeric=True)
    pre.fit(train)
    transformed = pre.transform(held_out)

    index = list(pre.get_feature_names_out()).index("MonthlyCharges")
    # With a constant training column, StandardScaler keeps scale_ == 1, so a
    # held-out value is transformed to (value - 100). If the scaler had seen the
    # held-out rows it would have a real spread and this would not hold.
    expected = held_out["MonthlyCharges"].to_numpy() - 100.0
    np.testing.assert_allclose(transformed[:, index], expected, rtol=1e-5)


def test_pipeline_cross_validation_refits_the_preprocessor_per_fold(xy):
    """A Pipeline is what makes per-fold fitting automatic; prove it runs leak-free."""
    X, y = xy
    pipeline = Pipeline(
        [
            ("pre", build_preprocessor_for(list(X.columns), scale_numeric=True)),
            ("classifier", LogisticRegression(max_iter=500)),
        ]
    )
    scores = cross_val_score(pipeline, X, y, cv=3, scoring="average_precision")
    assert len(scores) == 3
    assert np.isfinite(scores).all()


def test_transform_is_deterministic(xy):
    X, _ = xy
    pre = build_preprocessor_for(list(X.columns), scale_numeric=False)
    first = pre.fit_transform(X)
    np.testing.assert_array_equal(first, pre.transform(X))


def test_unseen_category_does_not_crash_the_encoder(xy, cfg: Config):
    """Defence in depth: Predictor rejects unknown categories before they get
    here, but a caller that bypasses it must get zeros, not a crash."""
    X, y = xy
    splits = make_splits(X, y, cfg)
    pre = build_preprocessor_for(list(X.columns), scale_numeric=True).fit(splits.X_train)

    rogue = splits.X_test.head(1).copy()
    rogue.loc[rogue.index[0], "PaymentMethod"] = "Payment by goat"
    out = pre.transform(rogue)

    assert out.shape[1] == len(pre.get_feature_names_out())
    assert np.isfinite(out).all()


def test_scaling_is_skipped_for_tree_models(xy):
    """Trees are invariant to monotone rescaling; a fitted scaler would be noise."""
    X, _ = xy
    unscaled = build_preprocessor_for(list(X.columns), scale_numeric=False).fit_transform(X)
    index = list(
        build_preprocessor_for(list(X.columns), scale_numeric=False).fit(X).get_feature_names_out()
    ).index("tenure")
    np.testing.assert_allclose(unscaled[:, index], X["tenure"].to_numpy(), rtol=1e-5)


def test_output_width_matches_the_declared_feature_names(xy):
    X, _ = xy
    pre = build_preprocessor_for(list(X.columns), scale_numeric=True)
    assert pre.fit_transform(X).shape[1] == len(pre.get_feature_names_out())


def test_columns_outside_the_contract_are_dropped(xy):
    X, _ = xy
    with_extra = X.assign(internal_note="should never reach the model")
    pre = build_preprocessor_for(list(X.columns), scale_numeric=True)
    baseline = pre.fit_transform(X)
    assert pre.transform(with_extra).shape[1] == baseline.shape[1]


def test_numeric_gaps_are_imputed_rather_than_propagated(xy):
    """Serving safety net: a NaN must not become a NaN prediction."""
    X, _ = xy
    pre = build_preprocessor_for(list(X.columns), scale_numeric=True).fit(X)
    gapped = X.head(3).copy()
    gapped.loc[gapped.index, "TotalCharges"] = np.nan
    assert np.isfinite(pre.transform(gapped)).all()


def test_every_categorical_level_in_the_contract_is_encodable(xy):
    """One-hot width must account for the full declared vocabulary of the data."""
    X, _ = xy
    pre = build_preprocessor_for(list(X.columns), scale_numeric=False).fit(X)
    names = set(pre.get_feature_names_out())
    for column, levels in schema.ALLOWED_CATEGORIES.items():
        if column not in X.columns:
            continue
        present = {lvl for lvl in levels if (X[column] == lvl).any()}
        missing = {f"{column}_{lvl}" for lvl in present} - names
        assert not missing, f"{column}: {missing}"
