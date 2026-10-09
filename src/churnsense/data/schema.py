"""The column contract for the Telco churn dataset.

Every allowed category below was *read off the real file* during Stage 1, not
assumed. Keeping the contract in code (rather than in config.yaml) is
deliberate: it is a structural fact about the data that the pipeline depends
on, so a change to it should be a reviewed code change, not a config tweak.

Two schemas live here and they are not the same thing:

* the **raw** schema  - what ``data/raw/*.csv`` must contain, including the
  target and the identifier;
* the **model-input** schema - the columns the fitted pipeline actually
  consumes, which excludes the target, the identifier and any configured
  drops.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

from churnsense.exceptions import SchemaValidationError

TARGET: Final = "Churn"
ID_COLUMN: Final = "customerID"
# The raw file says Yes/No; 0/1 is accepted so an already-encoded file is not
# misread. Nothing else is a label.
TARGET_ENCODING: Final[dict[str, int]] = {"No": 0, "Yes": 1, "0": 0, "1": 1}

# Numeric after cleaning. `SeniorCitizen` arrives as int64 0/1 but is a flag,
# not a quantity, so it is modelled as a category.
NUMERIC_COLUMNS: Final[tuple[str, ...]] = ("tenure", "MonthlyCharges", "TotalCharges")

# Hard, physically-impossible bounds - not the training min/max. A real new
# customer may legitimately sit outside the observed range; a negative tenure
# cannot exist. Being outside the *training* range is reported as a warning by
# the API instead, since it affects trust in the prediction but not validity.
NUMERIC_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "tenure": (0.0, 120.0),
    "MonthlyCharges": (0.0, 1000.0),
    "TotalCharges": (0.0, 100_000.0),
}

#: Counts, not measurements. The API's request model types tenure as int, so
#: a CSV upload must not be laxer: 5.5 months is a typo, not a customer.
INTEGER_COLUMNS: Final[tuple[str, ...]] = ("tenure",)

_YES_NO: Final = ("No", "Yes")
_YES_NO_NO_INTERNET: Final = ("No", "No internet service", "Yes")

ALLOWED_CATEGORIES: Final[dict[str, tuple[str, ...]]] = {
    "gender": ("Female", "Male"),
    "SeniorCitizen": ("0", "1"),
    "Partner": _YES_NO,
    "Dependents": _YES_NO,
    "PhoneService": _YES_NO,
    "MultipleLines": ("No", "No phone service", "Yes"),
    "InternetService": ("DSL", "Fiber optic", "No"),
    "OnlineSecurity": _YES_NO_NO_INTERNET,
    "OnlineBackup": _YES_NO_NO_INTERNET,
    "DeviceProtection": _YES_NO_NO_INTERNET,
    "TechSupport": _YES_NO_NO_INTERNET,
    "StreamingTV": _YES_NO_NO_INTERNET,
    "StreamingMovies": _YES_NO_NO_INTERNET,
    "Contract": ("Month-to-month", "One year", "Two year"),
    "PaperlessBilling": _YES_NO,
    "PaymentMethod": (
        "Bank transfer (automatic)",
        "Credit card (automatic)",
        "Electronic check",
        "Mailed check",
    ),
}

# Column order in the raw CSV, used to give a precise error when a file is
# missing columns or carries unexpected extras.
RAW_COLUMNS: Final[tuple[str, ...]] = (
    ID_COLUMN,
    "gender",
    "SeniorCitizen",
    "Partner",
    "Dependents",
    "tenure",
    "PhoneService",
    "MultipleLines",
    "InternetService",
    "OnlineSecurity",
    "OnlineBackup",
    "DeviceProtection",
    "TechSupport",
    "StreamingTV",
    "StreamingMovies",
    "Contract",
    "PaperlessBilling",
    "PaymentMethod",
    "MonthlyCharges",
    "TotalCharges",
    TARGET,
)

#: A month-to-month fibre customer with no add-ons - the profile the EDA
#: identified as highest-risk. One definition serves the dashboard form's
#: defaults, the downloadable CSV template and the OpenAPI example, so the
#: three cannot describe different "valid customers".
EXAMPLE_HIGH_RISK: Final[dict[str, object]] = {
    "SeniorCitizen": "0",
    "Partner": "No",
    "Dependents": "No",
    "tenure": 3,
    "PhoneService": "Yes",
    "MultipleLines": "No",
    "InternetService": "Fiber optic",
    "OnlineSecurity": "No",
    "OnlineBackup": "No",
    "DeviceProtection": "No",
    "TechSupport": "No",
    "StreamingTV": "Yes",
    "StreamingMovies": "Yes",
    "Contract": "Month-to-month",
    "PaperlessBilling": "Yes",
    "PaymentMethod": "Electronic check",
    "MonthlyCharges": 95.0,
    "TotalCharges": 285.0,
}

#: The contrast case: a long-tenure, fully-subscribed, two-year customer.
EXAMPLE_LOW_RISK: Final[dict[str, object]] = {
    "SeniorCitizen": "1",
    "Partner": "Yes",
    "Dependents": "Yes",
    "tenure": 64,
    "PhoneService": "Yes",
    "MultipleLines": "Yes",
    "InternetService": "DSL",
    "OnlineSecurity": "Yes",
    "OnlineBackup": "Yes",
    "DeviceProtection": "Yes",
    "TechSupport": "Yes",
    "StreamingTV": "No",
    "StreamingMovies": "No",
    "Contract": "Two year",
    "PaperlessBilling": "No",
    "PaymentMethod": "Credit card (automatic)",
    "MonthlyCharges": 68.3,
    "TotalCharges": 4371.2,
}

# Human-readable labels, shared by the dashboard, the SHAP narratives and the
# EDA report so a feature is never named two different ways to the user.
FEATURE_LABELS: Final[dict[str, str]] = {
    "tenure": "Tenure (months)",
    "MonthlyCharges": "Monthly charges",
    "TotalCharges": "Total charges to date",
    "SeniorCitizen": "Senior citizen",
    "PhoneService": "Phone service",
    "MultipleLines": "Multiple lines",
    "InternetService": "Internet service",
    "OnlineSecurity": "Online security add-on",
    "OnlineBackup": "Online backup add-on",
    "DeviceProtection": "Device protection add-on",
    "TechSupport": "Tech support add-on",
    "StreamingTV": "Streaming TV",
    "StreamingMovies": "Streaming movies",
    "Contract": "Contract type",
    "PaperlessBilling": "Paperless billing",
    "PaymentMethod": "Payment method",
    "gender": "Gender",
    "Partner": "Has partner",
    "Dependents": "Has dependents",
}


def model_input_columns(
    drop_columns: tuple[str, ...] = (), include_total_charges: bool = True
) -> list[str]:
    """Columns the fitted pipeline consumes, in a stable order.

    Stable ordering matters: it is persisted in ``model_meta.json`` and used to
    reindex any incoming frame, which is what keeps the dashboard, the API and
    batch scoring in agreement.
    """
    dropped = set(drop_columns) | {ID_COLUMN, TARGET}
    if not include_total_charges:
        dropped.add("TotalCharges")
    return [c for c in RAW_COLUMNS if c not in dropped]


def split_feature_types(columns: list[str]) -> tuple[list[str], list[str]]:
    """Partition model-input columns into (numeric, categorical)."""
    numeric = [c for c in columns if c in NUMERIC_COLUMNS]
    categorical = [c for c in columns if c in ALLOWED_CATEGORIES]
    unknown = set(columns) - set(numeric) - set(categorical)
    if unknown:
        raise SchemaValidationError(
            "column is in neither the numeric nor the categorical contract",
            sorted(unknown),
        )
    return numeric, categorical


#: Problems quote uploaded values back to the caller, so how many are quoted
#: and how much of each is bounded: a file of long junk labels must not turn
#: into a multi-megabyte error message.
_MAX_QUOTED_VALUES = 5
_MAX_QUOTED_CHARS = 40


def _quoted(values: list[str]) -> str:
    """The first few offending values, each cut short, and how many were left out."""
    shown = [
        v if len(v) <= _MAX_QUOTED_CHARS else v[:_MAX_QUOTED_CHARS] + "…"
        for v in values[:_MAX_QUOTED_VALUES]
    ]
    hidden = len(values) - len(shown)
    return f"{shown}" + (f" and {hidden} more" if hidden else "")


def validate_frame(
    df: pd.DataFrame,
    *,
    columns: list[str] | None = None,
    require_target: bool = False,
) -> None:
    """Validate a frame against the contract, collecting *all* problems.

    Collecting rather than failing fast is a usability decision: a user fixing
    an uploaded CSV should see every issue at once, not one per attempt.

    Args:
        df: frame to check.
        columns: required columns. Defaults to the full raw contract.
        require_target: also require the target column to be present and binary.

    Raises:
        SchemaValidationError: if any check fails.
    """
    required = list(columns) if columns is not None else [c for c in RAW_COLUMNS if c != TARGET]
    problems: list[str] = []

    # len(), not .empty: rows with none of the expected columns are "missing
    # columns", which .empty would misreport as "no rows".
    if len(df) == 0:
        raise SchemaValidationError("input contains no rows")

    missing = [c for c in required if c not in df.columns]
    if missing:
        problems.append(f"missing columns: {', '.join(missing)}")

    if require_target:
        if TARGET not in df.columns:
            problems.append(f"missing target column '{TARGET}'")
        else:
            if n_missing := int(df[TARGET].isna().sum()):
                problems.append(f"target '{TARGET}' has {n_missing} missing label(s)")
            labels = set(df[TARGET].dropna().astype(str).unique())
            if unexpected := sorted(labels - set(_YES_NO) - {"0", "1"}):
                problems.append(f"target '{TARGET}' has unexpected labels: {_quoted(unexpected)}")
            elif not labels <= set(_YES_NO) and not labels <= {"0", "1"}:
                problems.append(f"target '{TARGET}' mixes Yes/No labels with 0/1")

    for col in (c for c in NUMERIC_COLUMNS if c in df.columns and c in required):
        values = pd.to_numeric(df[col], errors="coerce")
        # Every gap counts, whatever made it. clean_frame has already turned
        # unparseable text into NaN by the time it calls this, and the
        # pipeline's median imputer would fill any NaN in and score the guess
        # with full confidence: 'abc' for tenure became a 7% churn risk.
        if n_missing := int(values.isna().sum()):
            problems.append(f"'{col}' has {n_missing} missing or non-numeric value(s)")
        if col in INTEGER_COLUMNS and (n_frac := int((values.notna() & (values % 1 != 0)).sum())):
            problems.append(f"'{col}' has {n_frac} non-integer value(s)")
        low, high = NUMERIC_BOUNDS[col]
        n_out = int(((values < low) | (values > high)).sum())
        if n_out:
            problems.append(f"'{col}' has {n_out} value(s) outside the valid range [{low}, {high}]")

    # Unknown categories are always rejected: a typo must not be encoded into a
    # confident score. Nor may a gap: the encoder ignores what it has not seen,
    # so a missing Contract scored as if the customer had none.
    for col, allowed in ALLOWED_CATEGORIES.items():
        if col not in df.columns or col not in required:
            continue
        if n_missing := int(df[col].isna().sum()):
            problems.append(f"'{col}' has {n_missing} missing value(s)")
        seen = set(df[col].dropna().astype(str).str.strip().unique())
        if unexpected := sorted(seen - set(allowed)):
            problems.append(
                f"'{col}' has unexpected value(s) {_quoted(unexpected)}; allowed: {list(allowed)}"
            )

    if problems:
        raise SchemaValidationError("input data failed schema validation", problems)
