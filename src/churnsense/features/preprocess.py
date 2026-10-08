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
import pandas as pd
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

    ``handle_unknown="infrequent_if_exist"`` is chosen over ``"error"`` so a
    production row carrying an unseen category degrades to the infrequent
    bucket instead of taking the service down; the API separately *reports*
    unknown categories so the caller still learns about it. Median imputation
    on the numeric side is a safety net for serving, not for training - the
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
                    handle_unknown="infrequent_if_exist",
                    min_frequency=1,
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


def align_to_contract(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Reindex an incoming frame onto the trained column order.

    Serving inputs arrive in whatever order the caller used, sometimes with
    extra columns. ``ColumnTransformer`` selects by name so order is not
    strictly required, but reindexing makes the contract explicit and gives a
    clear error for a genuinely missing column rather than a NaN column that
    silently imputes to the median.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise schema.SchemaValidationError("input is missing required feature column(s)", missing)
    return df.loc[:, columns]
