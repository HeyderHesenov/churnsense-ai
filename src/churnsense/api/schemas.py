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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


def _bounded(column: str, *, description: str | None = None) -> Field:
    """The range the training contract enforces, read from it rather than restated.

    Python's json reads NaN and Infinity; they are refused as non-finite, not
    by whichever bound they happen to fail.
    """
    low, high = columns.NUMERIC_BOUNDS[column]
    return Field(ge=low, le=high, allow_inf_nan=False, description=description)


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
    tenure: Annotated[int, _bounded("tenure", description="Months with the company")]
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
    MonthlyCharges: Annotated[float, _bounded("MonthlyCharges")]
    TotalCharges: Annotated[float, _bounded("TotalCharges")]

    @field_validator(*columns.NUMERIC_COLUMNS, mode="before")
    @classmethod
    def _not_a_boolean(cls, value: Any) -> Any:
        """Lax mode reads JSON true as 1, because a bool is an int to Python: a
        one-month customer, or $1.00 a month, scored with full confidence. The
        CSV path refuses "True", and so does this."""
        if isinstance(value, bool):
            raise ValueError("Input should be a number, not a boolean")
        return value


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


class ReadinessResponse(BaseModel):
    """Whether every page can be served: 200 when ``ready``, 503 otherwise. No reasons or paths."""

    status: Literal["ready", "not_ready"]
    model: bool
    dataset: bool
    frontend: bool


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
    # Added for the dashboard; optional, so older callers see no change.
    model_sha256: str | None = None
    probability_epsilon: float | None = None
    risk_bands: list[RiskBand] | None = None
    business_defaults: BusinessAssumptions | None = None
    limits: UploadLimits | None = None
    numeric_bounds: NumericBounds | None = None
    assumption_bounds: AssumptionBounds | None = None
    upload_contract: UploadContract | None = None
    provenance: Provenance | None = None
    test_evaluation: HoldoutSummary | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str
    problems: list[str] = Field(default_factory=list)


# --- shared by /model-info and the dashboard endpoints ----------------------------


class NumericRange(BaseModel):
    min: float
    max: float


class AssumptionRange(NumericRange):
    model_config = ConfigDict(frozen=True)

    integer: bool


class AssumptionBounds(BaseModel):
    """What ``/api/threshold/sweep`` accepts for each assumption, served so a page
    refuses what the sweep would rather than a copy that could drift.

    Frozen: one instance (``ASSUMPTION_BOUNDS``) is shared by every request."""

    model_config = ConfigDict(frozen=True)

    retention_offer_cost: AssumptionRange
    offer_success_rate: AssumptionRange
    expected_horizon_months: AssumptionRange
    gross_margin: AssumptionRange


#: The one statement of those bounds: the sweep's query parameters read them
#: from here, and /model-info serves them.
ASSUMPTION_BOUNDS = AssumptionBounds(
    retention_offer_cost=AssumptionRange(min=0, max=10_000, integer=False),
    offer_success_rate=AssumptionRange(min=0, max=1, integer=False),
    expected_horizon_months=AssumptionRange(min=1, max=120, integer=True),
    gross_margin=AssumptionRange(min=0, max=1, integer=False),
)


class BusinessAssumptions(BaseModel):
    """SIMULATED economics: assumptions, never estimates from the data.

    The echo of a ``Business``, so it holds ``Business``'s own invariants, not
    what the sweep accepts (``ASSUMPTION_BOUNDS``): narrower bounds must refuse a
    request, never break ``/model-info``.
    """

    currency: str
    retention_offer_cost: float
    offer_success_rate: Annotated[float, Field(ge=0, le=1)]
    expected_horizon_months: int
    gross_margin: Annotated[float, Field(ge=0, le=1)]


class Provenance(BaseModel):
    """Whether the dataset and split are the ones the served model was trained on."""

    verified: bool
    data_sha256: str | None
    split_digest: str | None
    withheld_reason: str | None


class RiskBand(BaseModel):
    name: str
    min: float
    max: float


class UploadLimits(BaseModel):
    max_batch_rows: int
    max_upload_bytes: int
    max_upload_columns: int


class NumericBounds(BaseModel):
    """The data contract's hard bounds (``schema.NUMERIC_BOUNDS``): a record outside them is refused."""

    tenure: NumericRange
    MonthlyCharges: NumericRange
    TotalCharges: NumericRange

    @classmethod
    def of_contract(cls) -> NumericBounds:
        return cls.model_validate(
            {
                column: {"min": low, "max": high}
                for column, (low, high) in columns.NUMERIC_BOUNDS.items()
            }
        )


class UploadContract(BaseModel):
    """What an uploaded CSV must hold for the served model, beside ``feature_columns``,
    ``numeric_bounds`` and ``limits``. ``rules`` are those ``validate_upload`` applies,
    written beside it in ``churnsense.models.predict``."""

    #: The allowed values of each categorical column the model scores, in its column order.
    categories: dict[str, list[str]]
    integer_columns: list[str]
    id_column: str
    rules: list[str]

    @classmethod
    def of_served(cls, feature_columns: list[str], rules: list[str]) -> UploadContract:
        return cls(
            categories={
                c: list(columns.ALLOWED_CATEGORIES[c])
                for c in feature_columns
                if c in columns.ALLOWED_CATEGORIES
            },
            integer_columns=[c for c in columns.INTEGER_COLUMNS if c in feature_columns],
            id_column=columns.ID_COLUMN,
            rules=rules,
        )


class HoldoutSummary(BaseModel):
    """The recorded test evaluation of the served artifact, from ``make evaluate``."""

    evaluated_at: str
    precision: float
    recall: float
