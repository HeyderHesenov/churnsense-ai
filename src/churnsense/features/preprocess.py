"""Feature preprocessing: one ``ColumnTransformer``, built from the schema.

Design decisions that matter here:

**Everything lives inside a ``Pipeline``.** The encoder and scaler are steps,
not a separate "transform the dataframe first" phase. That makes fitting on
anything other than the current training fold structurally impossible, which
is a stronger guarantee than a code review promising it.

**One-hot for every model, including the gradient-booster.**
``HistGradientBoostingClassifier`` supports native categorical splits and would
likely gain a little AUC from them, but it would need its own column contract
and its own serialization path. A single uniform contract is what lets the
dashboard, the API and batch scoring be *provably* identical, which is worth
more to this project than a fraction of a point of AUC.

**Scaling is conditional.** Only the linear model needs it. Trees are
invariant to monotone rescaling, so scaling them would add a fitted parameter
set that changes nothing - noise in the artifact, and one more thing to explain.
"""

from __future__ import annotations

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from churnsense.data import schema


def build_preprocessor(
    numeric: list[str],
    categorical: list[str],
    *,
    scale_numeric: bool,
) -> ColumnTransformer:
    """Build the transformer for the given column split.

    Unknown categories never reach this encoder in normal use: ``Predictor``
    validates every input against the column contract and rejects them.
    ``handle_unknown="ignore"`` is the defence-in-depth setting for a caller
    that bypasses ``Predictor`` - an all-zero encoding rather than a crash.
    Median imputation on the numeric side is the same kind of safety net; the
    cleaned training data has no numeric gaps.
    """
    numeric_steps: list[tuple[str, object]] = [("impute", SimpleImputer(strategy="median"))]
    if scale_numeric:
        numeric_steps.append(("scale", StandardScaler()))

    return ColumnTransformer(
        transformers=[
            ("num", Pipeline(numeric_steps), numeric),
            (
                "cat",
                OneHotEncoder(
                    handle_unknown="ignore",
                    sparse_output=False,
                    dtype=np.float32,
                ),
                categorical,
            ),
        ],
        remainder="drop",  # anything outside the contract never reaches the model
        verbose_feature_names_out=False,
    )


def build_preprocessor_for(columns: list[str], *, scale_numeric: bool) -> ColumnTransformer:
    """Convenience wrapper that derives the numeric/categorical split from the schema."""
    numeric, categorical = schema.split_feature_types(columns)
    return build_preprocessor(numeric, categorical, scale_numeric=scale_numeric)
