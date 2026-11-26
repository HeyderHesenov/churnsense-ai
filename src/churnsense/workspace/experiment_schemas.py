"""Retention experiments (M4): the definition, the assignment, the analysis result.

**One design, deliberately simple.** An experiment randomizes **accounts** into
exactly two arms, ``control`` and ``treatment``, in whole-percent allocations
that are each positive and add up to 100. Its one primary metric is
``logo_retention_rate``, and its analysis principle is **intention to treat**:
every account is analysed in the arm it was assigned to, whatever happened
after. No multi-arm, cluster, adaptive or bandit design exists.

**Declared before it starts.** A definition is versioned and immutable, and is
recorded no later than its enrollment start. Each account's outcome window
starts on the UTC date it was assigned and lasts ``outcome_window_days``.

**Eligible before assignment.** ``logo_retention_rate`` is the retention of
existing paying accounts, so only an account whose trusted M2 baseline snapshot
(``snapshot.Snapshot``) shows MRR above zero, in a valid currency, dated no later
than the assignment, can be assigned. Each assignment carries that evidence
(``EligibilityEvidence``), taken by the server from the snapshot
(``eligibility_evidence``) and never from a request. An account with zero MRR
cannot be assigned, so it can never count as retained because it started paying
during the window.

**Assignments are the server's.** An ``ExperimentAssignment`` records an arm the
server assigned; no request chooses an arm or states a baseline
(``EnrollAccountRequest`` names only the account). M4 represents assignments and
does not make them: there is no randomizer, enrollment or execution here.

**Intention to treat, without imputation.** Each arm's matured population is
every account assigned to it whose outcome window has ended. Its ITT retention
rate (``IttRetentionRate``) is given only when every one of them has a recorded
retained or churned outcome; a missing or unknown outcome makes it null, never a
guess. The observed-outcome rate (``ObservedOutcomeDiagnostic``) is a separate,
complete-case diagnostic and never the primary result.

**The analysis is descriptive, and says so in its shape.**
``ExperimentAnalysis`` has fixed fields declaring the analysis ``descriptive``,
the causal interpretation ``not_established`` and statistical inference
``not_performed``. It has no field for a p-value, a confidence interval,
significance, a winner, an effect, a lift or revenue: none was computed.
"""

from __future__ import annotations

from datetime import UTC, date
from decimal import Decimal
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from churnsense.workspace import privacy
from churnsense.workspace.action_schemas import Stamp
from churnsense.workspace.outcome_schemas import (
    AccountId,
    CountRatio,
    Currency,
    Money,
    OutcomeDate,
)
from churnsense.workspace.snapshot import Snapshot

ArmName = Literal["control", "treatment"]
ARMS: Final[tuple[str, ...]] = ("control", "treatment")
PrimaryMetric = Literal["logo_retention_rate"]
RandomizationUnit = Literal["account"]
AnalysisPrinciple = Literal["intention_to_treat"]
AssignmentMethod = Literal["server_randomized"]

MIN_OUTCOME_WINDOW_DAYS: Final = 7
MAX_OUTCOME_WINDOW_DAYS: Final = 366
#: The longest enrollment period, from its first day to its last.
MAX_ENROLLMENT_DAYS: Final = 366
NAME_MAX_LENGTH: Final = 120
TEXT_MAX_LENGTH: Final = 1000
MAX_EXPERIMENTS_LISTED: Final = 100

DESCRIPTIVE_STATEMENT: Final = (
    "Descriptive comparison of observed logo retention by assigned arm. It does not establish "
    "a causal effect of the treatment, no statistical test was performed, and no arm is "
    "selected as better."
)


def _not_personal(value: str) -> str:
    if privacy.looks_like_personal_identifier(value):
        raise ValueError("must not contain an email address or a phone number")
    return value


def _text(max_length: int):
    """Trimmed words: not blank, bounded, no control character but a line break."""
    return Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=max_length,
            pattern=r"^[^\x00-\x09\x0b-\x1f\x7f]+$",
        ),
        AfterValidator(_not_personal),
    ]


Name = _text(NAME_MAX_LENGTH)
Text = _text(TEXT_MAX_LENGTH)


class _Model(BaseModel):
    # Frozen, and a validation error never quotes the value it refused.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


# --- the definition ----------------------------------------------------------------------------


class ArmDesign(_Model):
    arm: ArmName
    allocation_percent: int = Field(ge=1, le=99, description="Whole percent of accounts.")
    description: Text = Field(description="What this arm's accounts receive.")


class _Design(_Model):
    """Everything a person declares about an experiment, and the rules that hold it together."""

    name: Name
    objective: Text = Field(description="The business question the experiment informs.")
    hypothesis: Text = Field(description="The testable statement, declared before enrollment.")
    primary_metric: PrimaryMetric = "logo_retention_rate"
    randomization_unit: RandomizationUnit = "account"
    analysis_principle: AnalysisPrinciple = "intention_to_treat"
    arms: tuple[ArmDesign, ArmDesign] = Field(description="control first, then treatment.")
    enrollment_start: OutcomeDate
    enrollment_end: OutcomeDate = Field(description="Last day an account may be assigned.")
    outcome_window_days: int = Field(
        ge=MIN_OUTCOME_WINDOW_DAYS,
        le=MAX_OUTCOME_WINDOW_DAYS,
        description="Days from an account's assignment date to the end of its outcome window.",
    )

    @model_validator(mode="after")
    def _valid_design(self) -> _Design:
        if tuple(a.arm for a in self.arms) != ARMS:
            raise ValueError("the arms are exactly control and then treatment, each once")
        if sum(a.allocation_percent for a in self.arms) != 100:
            raise ValueError("the allocations must add up to exactly 100 percent")
        if self.enrollment_end < self.enrollment_start:
            raise ValueError("enrollment_end must not be before enrollment_start")
        if (self.enrollment_end - self.enrollment_start).days + 1 > MAX_ENROLLMENT_DAYS:
            raise ValueError(f"enrollment lasts at most {MAX_ENROLLMENT_DAYS} days")
        return self

    def allocation(self, arm: str) -> int:
        return next(a.allocation_percent for a in self.arms if a.arm == arm)


class CreateExperimentRequest(_Design):
    """Declare an experiment. Its ID, version and creator are the server's."""


class ExperimentDefinition(_Design):
    """One immutable version of an experiment, recorded before its enrollment starts."""

    experiment_id: UUID
    version: int = Field(ge=1, description="A changed design is a new version, never an edit.")
    created: Stamp

    @model_validator(mode="after")
    def _declared_before_enrollment(self) -> ExperimentDefinition:
        if self.created.at.astimezone(UTC).date() > self.enrollment_start:
            raise ValueError("an experiment is defined no later than its enrollment start")
        return self


class ExperimentList(_Model):
    experiments: tuple[ExperimentDefinition, ...] = Field(max_length=MAX_EXPERIMENTS_LISTED)


# --- assignments and their outcomes ------------------------------------------------------------


class EnrollAccountRequest(_Model):
    """Ask the server to assign an account. The arm and the eligibility evidence are the
    server's: a client names the account and nothing else."""

    account_id: AccountId


class EligibilityEvidence(_Model):
    """Why the account could be assigned: its trusted M2 baseline snapshot, as the server read it.

    Server-derived and immutable. ``baseline_mrr`` must be above zero: the primary
    metric measures the retention of accounts that were paying at the baseline.
    """

    account_id: AccountId
    snapshot_date: OutcomeDate = Field(description="The baseline snapshot's as_of_date.")
    baseline_mrr: Money = Field(description="The baseline snapshot's MRR: above zero.")
    currency: Currency

    @model_validator(mode="after")
    def _paying(self) -> EligibilityEvidence:
        if self.baseline_mrr <= 0:
            raise ValueError(
                "the baseline MRR must be above zero: only accounts paying at the baseline are "
                "eligible for logo_retention_rate"
            )
        return self


def eligibility_evidence(snapshot: Snapshot) -> EligibilityEvidence:
    """The evidence a trusted M2 account snapshot gives, checked; a ``ValidationError`` if the
    account is not eligible. For the future enrollment service, which reads the snapshot."""
    return EligibilityEvidence.model_validate(
        {
            "account_id": snapshot.account_external_id,
            "snapshot_date": snapshot.as_of_date,
            "baseline_mrr": snapshot.mrr,
            "currency": snapshot.currency,
        }
    )


class ExperimentAssignment(_Model):
    """One account's arm in one experiment version, as the server assigned it, with the
    eligibility evidence that admitted it."""

    assignment_id: UUID
    experiment_id: UUID
    experiment_version: int = Field(ge=1)
    account_id: AccountId
    arm: ArmName
    assigned_at: AwareDatetime
    assignment_method: AssignmentMethod
    eligibility: EligibilityEvidence

    @model_validator(mode="after")
    def _eligible_when_assigned(self) -> ExperimentAssignment:
        if self.eligibility.account_id != self.account_id:
            raise ValueError("the eligibility evidence is another account's")
        if self.eligibility.snapshot_date > self.assigned_on:
            raise ValueError("the baseline snapshot is dated after the assignment")
        return self

    @property
    def assigned_on(self) -> date:
        """The UTC calendar date of the assignment: the first day of its outcome window."""
        return self.assigned_at.astimezone(UTC).date()


class ExperimentOutcome(_Model):
    """An assignment and the recorded outcome of its account's outcome window."""

    experiment_id: UUID
    experiment_version: int = Field(ge=1)
    assignment_id: UUID
    outcome_id: UUID
    account_id: AccountId
    arm: ArmName = Field(description="The assigned arm: the one the outcome is analysed in.")


class ExperimentAnalysisQuery(_Model):
    as_of: OutcomeDate = Field(description="Required: nothing reads the server's clock.")


# --- the descriptive analysis ------------------------------------------------------------------


IttUnavailable = Literal["no_matured_assignments", "incomplete_outcome_data"]
DifferenceBasis = Literal["intention_to_treat_complete_data", "observed_outcomes_only"]


class IttRetentionRate(_Model):
    """The arm's intention-to-treat logo retention: retained / every matured assigned account.

    Given only when every account in the population has a recorded retained or
    churned outcome. A missing or unknown outcome is never imputed: the rate is then
    null, ``unavailable`` says why, and the population still counts every account.
    """

    population: int = Field(ge=0, description="Every matured account assigned to the arm.")
    retained: int = Field(ge=0)
    churned: int = Field(ge=0)
    unknown: int = Field(ge=0)
    missing: int = Field(ge=0)
    percent: Decimal | None = Field(
        description="retained / population x 100, cut toward zero to one decimal place; null "
        "unless every account in the population has a retained or churned outcome."
    )
    unavailable: IttUnavailable | None
    unavailable_reason: str | None

    @model_validator(mode="after")
    def _complete_or_explained(self) -> IttRetentionRate:
        if self.population != self.retained + self.churned + self.unknown + self.missing:
            raise ValueError("the population is every matured assigned account")
        expected: str | None = None
        if self.population == 0:
            expected = "no_matured_assignments"
        elif self.unknown or self.missing:
            expected = "incomplete_outcome_data"
        if self.unavailable != expected:
            raise ValueError(
                "the rate is unavailable exactly when the population is empty or "
                "an outcome is missing or unknown"
            )
        if (self.percent is None) != (expected is not None) or (
            (self.unavailable_reason is None) != (expected is None)
        ):
            raise ValueError("an unavailable rate has no percent and a reason")
        return self


class ObservedOutcomeDiagnostic(_Model):
    """A complete-case diagnostic: retention among the accounts whose outcome was observed.

    Not the intention-to-treat estimate: it leaves out every account whose outcome is
    missing or unknown, so it may differ from what the whole arm would show.
    """

    retained: int = Field(ge=0, description="The numerator.")
    observed: int = Field(ge=0, description="The denominator: retained + churned.")
    unknown: int = Field(ge=0)
    missing: int = Field(ge=0)
    observed_retention_rate: CountRatio = Field(description="retained / observed.")
    outcome_coverage: CountRatio = Field(description="observed / matured.")

    @model_validator(mode="after")
    def _consistent(self) -> ObservedOutcomeDiagnostic:
        rate, cover = self.observed_retention_rate, self.outcome_coverage
        if (rate.numerator, rate.denominator) != (self.retained, self.observed) or (
            cover.numerator != self.observed
            or (cover.denominator != self.observed + self.unknown + self.missing)
        ):
            raise ValueError("the diagnostic's counts must add up")
        return self


class ArmSummary(_Model):
    """One arm, every account as assigned. ``assigned = immature + matured``;
    ``matured = retained + churned + unknown + missing``: the matured ITT population."""

    arm: ArmName
    allocation_percent: int
    assigned: int = Field(ge=0)
    immature: int = Field(
        ge=0, description="Outcome window ends after as_of: not yet in the matured population."
    )
    matured: int = Field(ge=0, description="The matured intention-to-treat population.")
    retained: int = Field(ge=0)
    churned: int = Field(ge=0)
    unknown: int = Field(ge=0, description="An outcome was recorded as unknown.")
    missing: int = Field(ge=0, description="No outcome recorded and observed by as_of.")
    itt_retention_rate: IttRetentionRate = Field(description="The primary rate.")
    observed_outcome_diagnostic: ObservedOutcomeDiagnostic = Field(
        description="A complete-case diagnostic, never the primary rate."
    )

    @model_validator(mode="after")
    def _adds_up(self) -> ArmSummary:
        itt, diag = self.itt_retention_rate, self.observed_outcome_diagnostic
        counts = (self.retained, self.churned, self.unknown, self.missing)
        if (
            self.assigned != self.immature + self.matured
            or self.matured != sum(counts)
            or (itt.population, itt.retained, itt.churned, itt.unknown, itt.missing)
            != (self.matured, *counts)
            or (diag.retained, diag.observed, diag.unknown, diag.missing)
            != (self.retained, self.retained + self.churned, self.unknown, self.missing)
        ):
            raise ValueError("an arm's counts must add up")
        return self


class RateDifference(_Model):
    """Treatment's retention rate minus control's, in percentage points: a description.

    ``basis`` says which rates were compared. Only ``intention_to_treat_complete_data``
    is the primary comparison; ``observed_outcomes_only`` is a diagnostic.
    """

    metric: PrimaryMetric
    basis: DifferenceBasis
    treatment_minus_control_pp: Decimal | None = Field(
        description="The exact difference of the two rates x 100, cut toward zero to one "
        "decimal place. Null, with a reason, when either rate is unavailable."
    )
    unavailable_reason: str | None

    @model_validator(mode="after")
    def _value_or_reason(self) -> RateDifference:
        if (self.treatment_minus_control_pp is None) == (self.unavailable_reason is None):
            raise ValueError("a difference has a value or a reason, not both and not neither")
        return self


class Limitation(_Model):
    code: str
    message: str


class ExperimentAnalysis(_Model):
    """A descriptive summary of one experiment version. Not a causal or statistical result."""

    analysis_type: Literal["descriptive"]
    causal_interpretation: Literal["not_established"]
    statistical_inference: Literal["not_performed"]
    statement: str
    experiment_id: UUID
    experiment_version: int
    primary_metric: PrimaryMetric
    analysis_principle: AnalysisPrinciple
    as_of: date
    control: ArmSummary
    treatment: ArmSummary
    itt_retention_rate_difference: RateDifference = Field(
        description="The primary comparison: both arms' complete-data ITT rates."
    )
    observed_outcome_difference: RateDifference = Field(
        description="A diagnostic only: both arms' observed-outcome rates. Not the ITT comparison."
    )
    outcomes_not_used: int = Field(
        ge=0, description="Outcome records matching no assignment's account and window."
    )
    limitations: tuple[Limitation, ...]

    @model_validator(mode="after")
    def _arms_in_place(self) -> ExperimentAnalysis:
        if (self.control.arm, self.treatment.arm) != ARMS:
            raise ValueError("control and treatment summaries must be in their places")
        if self.statement != DESCRIPTIVE_STATEMENT:
            raise ValueError("the statement is the fixed descriptive statement")
        if (self.itt_retention_rate_difference.basis, self.observed_outcome_difference.basis) != (
            "intention_to_treat_complete_data",
            "observed_outcomes_only",
        ):
            raise ValueError(
                "the primary comparison is the ITT one; the observed one is a diagnostic"
            )
        return self
