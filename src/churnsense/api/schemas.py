"""Request and response models for the API.

Pydantic earns its place here and nowhere else in the project: this is the
boundary where untrusted input arrives, and the validation error messages are
part of the published contract.

The category vocabularies are written out as explicit ``Literal`` aliases
rather than generated from ``churnsense.data.schema`` at import time.
Generating them read well but produced unresolvable forward references under
``from __future__ import annotations``, and it hid the contract from both the
reader and the type checker. Drift is prevented by a test that compares every
alias against ``schema.ALLOWED_CATEGORIES`` instead - the guarantee is the
same, and the failure is a clear assertion rather than a startup crash.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from churnsense.data import schema as columns

# Mirrors churnsense.data.schema.ALLOWED_CATEGORIES; tests/test_api.py asserts
# the two stay identical.
YesNo = Literal["No", "Yes"]
YesNoNoInternet = Literal["No", "No internet service", "Yes"]
SeniorFlag = Literal["0", "1"]
MultipleLinesValue = Literal["No", "No phone service", "Yes"]
InternetServiceValue = Literal["DSL", "Fiber optic", "No"]
ContractValue = Literal["Month-to-month", "One year", "Two year"]
PaymentMethodValue = Literal[
    "Bank transfer (automatic)",
    "Credit card (automatic)",
    "Electronic check",
    "Mailed check",
]

#: Field name -> the alias that must match the training contract.
CATEGORY_ALIASES: dict[str, object] = {
    "SeniorCitizen": SeniorFlag,
    "Partner": YesNo,
    "Dependents": YesNo,
    "PhoneService": YesNo,
    "MultipleLines": MultipleLinesValue,
    "InternetService": InternetServiceValue,
    "OnlineSecurity": YesNoNoInternet,
    "OnlineBackup": YesNoNoInternet,
    "DeviceProtection": YesNoNoInternet,
    "TechSupport": YesNoNoInternet,
    "StreamingTV": YesNoNoInternet,
    "StreamingMovies": YesNoNoInternet,
    "Contract": ContractValue,
    "PaperlessBilling": YesNo,
    "PaymentMethod": PaymentMethodValue,
}


class CustomerFeatures(BaseModel):
    """One customer's features, exactly as the trained pipeline expects them."""

    model_config = ConfigDict(
        extra="ignore",  # callers may pass customerID or Churn; ignore, don't reject
        # The same record the dashboard form and the CSV template use.
        json_schema_extra={"example": dict(columns.EXAMPLE_HIGH_RISK)},
    )

    SeniorCitizen: SeniorFlag = Field(description="1 if the customer is a senior citizen")
    Partner: YesNo
    Dependents: YesNo
    tenure: Annotated[int, Field(ge=0, le=120, description="Months with the company")]
    PhoneService: YesNo
    MultipleLines: MultipleLinesValue
    InternetService: InternetServiceValue
    OnlineSecurity: YesNoNoInternet
    OnlineBackup: YesNoNoInternet
    DeviceProtection: YesNoNoInternet
    TechSupport: YesNoNoInternet
    StreamingTV: YesNoNoInternet
    StreamingMovies: YesNoNoInternet
    Contract: ContractValue
    PaperlessBilling: YesNo
    PaymentMethod: PaymentMethodValue
    MonthlyCharges: Annotated[float, Field(ge=0, le=1000)]
    TotalCharges: Annotated[float, Field(ge=0, le=100_000)]


class PredictionResponse(BaseModel):
    """A scored customer."""

    churn_probability: Annotated[float, Field(ge=0, le=1)]
    risk_band: str
    flagged: bool = Field(description="True when the probability meets the decision threshold")
    threshold: Annotated[float, Field(ge=0, le=1)]
    model_key: str
    caveat: str = Field(
        default=(
            "A churn probability is an estimate of similarity to past churners, not a "
            "prediction of an individual's intent, and not a causal statement."
        )
    )


class BatchPredictionRow(PredictionResponse):
    """One row of a batch result, carrying its position in the uploaded file."""

    row: int = Field(description="0-based position of the data row in the uploaded file")


class BatchResponse(BaseModel):
    n_scored: int
    threshold: Annotated[float, Field(ge=0, le=1)]
    model_key: str
    predictions: list[BatchPredictionRow]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_loaded: bool
    detail: str | None = None


class ModelInfoResponse(BaseModel):
    """What the service is serving. Deliberately excludes any filesystem path."""

    model_key: str
    display_name: str
    trained_at: str
    threshold: float
    threshold_source: str
    calibrated: bool
    calibration_method: str | None
    feature_columns: list[str]
    dropped_columns: list[str]
    n_train: int
    n_validation: int
    validation_metrics: dict[str, Any]
    test_metrics: dict[str, Any] | None = None
    sklearn_version: str
    limitations: list[str]


class ErrorResponse(BaseModel):
    error: str
    detail: str
    problems: list[str] = Field(default_factory=list)
