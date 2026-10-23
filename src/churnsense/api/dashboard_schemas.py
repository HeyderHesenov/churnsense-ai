"""Response models for the dashboard endpoints under ``/api``.

Every figure a page shows is a field here, computed on the server: the browser
formats and positions numbers, it never derives one. The models double as the
contract the front end's TypeScript types are generated from.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from churnsense.api.schemas import (
    BusinessAssumptions,
    CustomerFeatures,
)

Probability = Annotated[float, Field(ge=0, le=1)]
Share = Annotated[float, Field(ge=0, le=1)]
Outcome = Literal["churned", "stayed", "withheld"]
Partition = Literal["train", "validation", "test", "unknown"]


class _Model(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# --- shared pieces -----------------------------------------------------------------


class Observed(_Model):
    """Outcomes counted over training and validation customers only.

    ``churned`` and ``rate`` are null for fewer than 20 customers: too few to report.
    """

    customers: int
    churned: int | None
    rate: Share | None


class Segment(_Model):
    customers: int
    share: Share | None
    monthly_charges: float


class BandSummary(_Model):
    name: str
    min: float
    max: float
    customers: int
    share: Share | None
    monthly_charges: float
    monthly_share: Share | None
    flagged: int
    observed: Observed | None


class ReportedCell(_Model):
    """A level (or pair of levels) with enough customers to report a rate."""

    row: str
    column: str | None
    suppressed: Literal[False] = False
    n: int
    churned: int
    rate: Share
    lift: float | None


class SuppressedCell(_Model):
    """Fewer than 20 customers: the count only, never a rate or a churn count."""

    row: str
    column: str | None
    suppressed: Literal[True] = True
    n: int


#: Told apart by ``suppressed`` (a boolean, so not an OpenAPI discriminator,
#: which must be a string): a client narrows on it like any tagged union.
Cell = ReportedCell | SuppressedCell


class Association(_Model):
    feature: str
    measure: str
    strength: Share


# --- overview -------------------------------------------------------------------------


class HistogramBin(_Model):
    low: float
    high: float
    band: str
    customers: int
    monthly_charges: float
    observed: Observed | None


class SegmentTab(_Model):
    """One breakdown of the training partition, as the overview's tabs show it."""

    key: str
    partition_customers: int
    base_rate: Share | None
    levels: list[Cell]
    #: Highest reported rate over the lowest, and the level with the highest.
    spread: float | None
    peak: str | None


class Lifecycle(_Model):
    partition_customers: int
    base_rate: Share | None
    buckets: list[Cell]
    median_tenure_churned: float | None
    median_tenure_stayed: float | None


class Importance(_Model):
    feature: str
    label: str
    mean_abs_shap: float
    share: float


class ImportanceBasis(_Model):
    """The customers a cached importance ranking was computed on."""

    partition: str
    customers: int


class CurvePoint(_Model):
    threshold: float
    net_benefit: float


class Campaign(_Model):
    """The shipped threshold applied to the validation partition. SIMULATED."""

    threshold: float
    customers: int
    flagged: int
    precision: float
    recall: float
    intervention_cost: float
    retained_value: float
    net_benefit: float
    peak_threshold: float
    peak_net_benefit: float
    recommended_threshold: float
    assumptions: BusinessAssumptions
    curve: list[CurvePoint]


class OverviewResponse(_Model):
    customers: int
    monthly_charges: float
    threshold: Probability
    observed: Observed | None
    high_or_critical: Segment
    flagged: Segment
    bands: list[BandSummary]
    histogram: list[HistogramBin]
    segments: list[SegmentTab]
    lifecycle: Lifecycle | None
    importance: list[Importance] | None
    #: The sample behind ``importance``; ``None`` exactly when it is.
    importance_basis: ImportanceBasis | None
    association: list[Association] | None
    campaign: Campaign | None
    #: Why no outcome is shown, when the partitions could not be verified.
    withheld_reason: str | None


# --- customers ------------------------------------------------------------------------


class CustomerRow(_Model):
    id: str
    contract: str
    internet_service: str
    payment_method: str
    tenure: int
    monthly_charges: float
    churn_probability: Probability
    risk_band: str
    flagged: bool
    partition: Partition
    outcome: Outcome
    #: churn_probability x monthly_charges: expected monthly revenue at risk.
    at_risk: float


class SelectionSummary(_Model):
    customers: int
    share_of_book: Share | None
    flagged: int
    flagged_share: Share | None
    mean_probability: Probability | None
    expected_churners: float
    observed: Observed | None
    monthly_charges: float
    at_risk: float
    #: Customers per 0.05 of probability, 20 bins.
    histogram: list[int]


class CustomerPage(_Model):
    total: int
    page: int
    page_size: int
    pages: int
    rows: list[CustomerRow]
    summary: SelectionSummary
    withheld_reason: str | None


class CustomerDetail(_Model):
    id: str
    features: CustomerFeatures
    churn_probability: Probability
    risk_band: str
    flagged: bool
    threshold: Probability
    partition: Partition
    outcome: Outcome


class ExamplesResponse(_Model):
    #: The README's example record, which is not a customer in the book.
    readme_example: CustomerFeatures
    #: One validation customer per case the single-customer page compares.
    customers: list[CustomerDetail]


# --- explanations -------------------------------------------------------------------------


class ExplainRequest(_Model):
    """Explain book customers by ID, or one record that is not in the book - not both."""

    customer_ids: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,32}$")]] | None,
        Field(min_length=1, max_length=8),
    ] = None
    record: CustomerFeatures | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ExplainRequest:
        if (self.customer_ids is None) == (self.record is None):
            raise ValueError("give either customer_ids or record, not both and not neither")
        if self.customer_ids is not None and len(set(self.customer_ids)) != len(self.customer_ids):
            raise ValueError("customer_ids must not repeat an ID")
        return self


class Contribution(_Model):
    feature: str
    label: str
    value: str | float
    #: Signed contribution to the probability (a fraction; x100 for percentage points).
    shap_value: float


class Explanation(_Model):
    customer_id: str | None
    churn_probability: Probability
    base_value: Probability
    #: Every feature, strongest first. base_value + their sum = churn_probability.
    contributions: list[Contribution]


class ExplainResponse(_Model):
    permutations: int
    seed: int
    background_customers: int
    explanations: list[Explanation]
    caveat: str


class GlobalExplanation(_Model):
    """``make explain``'s attributions for a validation sample, for the artifact served."""

    model_sha256: str
    partition: str
    n_explained: int
    n_background: int
    permutations: int
    seed: int | None
    base_value: Probability
    max_additivity_gap: float
    features: list[str]
    labels: dict[str, str]
    importance: list[Importance]
    #: One row per explained customer, one column per feature, as fractions.
    values: list[list[float]]
    x: list[dict[str, str | float]]
    association_rank: dict[str, int] | None
    #: Pearson r of TotalCharges against tenure x MonthlyCharges, training partition.
    total_charges_r: float | None
    caveat: str


# --- patterns ------------------------------------------------------------------------------


class SegmentsResponse(_Model):
    rows: str
    columns: str | None
    partition_customers: int
    partition_churned: int
    base_rate: Share | None
    #: One cell per level of ``rows``; ``row`` holds the level.
    row_totals: list[Cell]
    #: One cell per level of ``columns``; ``row`` holds the level here too.
    column_totals: list[Cell]
    cells: list[Cell]
    ranked: list[ReportedCell]
    association: list[Association] | None


# --- threshold economics ----------------------------------------------------------------------


class SweepPoint(_Model):
    threshold: float
    flagged: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    intervention_cost: float
    retained_value: float
    net_benefit: float


class Scenario(_Model):
    threshold: float
    flagged: int
    true_positives: int
    precision: float
    recall: float
    intervention_cost: float
    retained_value: float
    net_benefit: float


class IndifferenceBand(_Model):
    """Thresholds whose net benefit is within one offer's cost of the best."""

    low: float
    high: float
    floor: float


class Sensitivity(_Model):
    offer_success_rate: Share
    threshold: float
    flagged: int
    precision: float
    recall: float
    net_benefit: float


class SweepResponse(_Model):
    """The validation partition swept across thresholds. Every money figure is SIMULATED."""

    assumptions: BusinessAssumptions
    customers: int
    churners: int
    shipped_threshold: Probability
    points: list[SweepPoint]
    recommended: Scenario
    peak: Scenario
    band: IndifferenceBand
    sensitivity: list[Sensitivity]
    disclaimer: str


# --- performance -------------------------------------------------------------------------------


class Selection(_Model):
    rule: str
    metric: str
    folds: int
    leader: str
    standard_error: float
    cutoff: float
    eligible: list[str]


class Candidate(_Model):
    model: str
    cv_pr_auc: float
    cv_std: float
    cv_se: float
    within_one_se: bool
    val_pr_auc: float
    val_roc_auc: float
    selected: bool


class AtHalf(_Model):
    """Validation metrics at threshold 0.50."""

    precision: float
    recall: float
    brier: float


class Calibration(_Model):
    method: str | None
    epsilon: float | None
    uncalibrated: AtHalf | None
    calibrated: AtHalf | None
    uncalibrated_ece: float | None
    calibrated_ece: float | None


class ValidationCurve(_Model):
    customers: int
    churners: int
    points: list[SweepPoint]


class HoldoutMetrics(_Model):
    threshold: float
    n: int
    positives: int
    tn: int
    fp: int
    fn: int
    tp: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    average_precision: float
    brier: float


class HoldoutSegment(_Model):
    """One segment of the recorded test evaluation; rates are null below 20 customers."""

    dimension: str
    segment: str
    customers: int
    churners: int | None
    churn_rate: float | None
    flagged: int
    flagged_rate: float | None
    precision: float | None
    recall: float | None
    unscanned: bool


class HoldoutEvaluation(_Model):
    """Recorded by ``make evaluate CONFIRM=yes``; no page computes it."""

    evaluated_at: str
    metrics: HoldoutMetrics
    ece: float | None
    no_skill_accuracy: float
    test_optimal_threshold: float
    segments: list[HoldoutSegment]


class PerformanceResponse(_Model):
    display_name: str
    threshold: Probability
    selection: Selection | None
    candidates: list[Candidate] | None
    calibration: Calibration
    bands: list[BandSummary]
    validation: ValidationCurve | None
    test: HoldoutEvaluation | None
    #: Why ``test`` is empty, when it is.
    test_reason: str | None
    withheld_reason: str | None


# --- batch scoring --------------------------------------------------------------------------------


class BatchRow(_Model):
    row: int
    customer_id: str | None
    #: The file's own label, or null - always null for a held-out test customer.
    label: Literal[0, 1] | None
    #: The model's inputs exactly as they were scored.
    features: dict[str, str | float]
    churn_probability: Probability
    risk_band: str
    flagged: bool


class BandCount(_Model):
    name: str
    customers: int


class LabelCheck(_Model):
    """Precision and recall against the file's own labels, outside the held-out test rows."""

    labelled: int
    excluded_test: int
    precision: float | None
    recall: float | None


class BatchSummary(_Model):
    rows: int
    flagged: int
    flagged_share: Share | None
    mean_probability: Probability | None
    expected_churners: float
    at_risk: float
    bands: list[BandCount]
    labels: LabelCheck | None
    #: Why ``labels`` is empty although the file has a Churn column.
    labels_reason: str | None


class BatchScoreResponse(_Model):
    columns: int
    threshold: Probability
    rows: list[BatchRow]
    summary: BatchSummary
    notes: list[str]
