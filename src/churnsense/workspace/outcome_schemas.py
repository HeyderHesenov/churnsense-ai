"""Measured retention outcomes (M4): the outcome record, the metric results, the task link.

**An outcome is a recorded fact, never an inference.** An ``AccountOutcome`` says
what an authoritative source (a billing or customer-management system) showed
for one account at the end of one measurement window. Nothing here derives an
outcome from a snapshot's status, an M2 level or indicator, a synthetic
account or an M3 task: only an explicit, validated record establishes one.

**Three outcomes** (``OUTCOME_MEANINGS``), each with the evidence it needs:

* ``retained`` - ending MRR is given and above zero;
* ``churned`` - ending MRR is given and exactly zero;
* ``unknown`` - the source could not establish the state, so ending MRR is
  null. It is never read as zero, and never counted as retained or churned.

**Money** is ``Decimal`` in the Data Hub's form - digits, at most 12 before the
point and 4 after, no sign - and leaves as a JSON string ("1250.50"). A JSON
number is refused rather than passed through a binary float. Currency is three
uppercase letters, as M1 accepts it, and one record holds one currency.

**The trust boundary is M3's** (``action_schemas``): a client submits only the
facts (``OutcomeSubmission``); the outcome ID, the source system and who
recorded it are the server's (``OutcomeProvenance``), and so is every link.

**Association is not attribution.** ``TaskOutcomeAssociation`` records that a
completed M3 task came before an observed outcome, in time. It has no field for
impact, revenue saved, churn avoided or attribution, because none was measured.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date
from decimal import Decimal
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import action_schemas, contract, privacy
from churnsense.workspace.action_lifecycle import ActionRefused
from churnsense.workspace.action_schemas import IsoDate, PlaybookRef, RetentionTask, Stamp

OutcomeStatus = Literal["retained", "churned", "unknown"]
OUTCOME_STATUSES: Final[tuple[str, ...]] = ("retained", "churned", "unknown")
OUTCOME_MEANINGS: Final[dict[str, str]] = {
    "retained": "At the end of the measurement window the authoritative source shows at least "
    "one active paid subscription: ending MRR is given and above zero.",
    "churned": "At the end of the measurement window the authoritative source shows no active "
    "paid subscription: ending MRR is given and exactly zero.",
    "unknown": "The authoritative source could not establish the account's state at the end of "
    "the window: ending MRR is not given, and the outcome counts as neither retained nor "
    "churned.",
}
OBSERVED_STATUSES: Final = frozenset({"retained", "churned"})
SourceSystem = Literal["billing_system", "crm", "manual_import"]
TaskOutcomeRelationship = Literal["task_completed_before_window", "task_completed_during_window"]

#: Fields only the server sets: M3's, and M4's own. No M4 request has any of them.
SERVER_DERIVED_FIELDS: Final = action_schemas.SERVER_DERIVED_FIELDS | frozenset(
    {
        "outcome_id",
        "provenance",
        "source_system",
        "recorded",
        "recorded_by",
        "recorded_at",
        "association_id",
        "relationship",
        "interpretation",
        "experiment_id",
        "experiment_version",
        "version",
        "assignment_id",
        "arm",
        "assigned_at",
        "assignment_method",
        "eligibility",
        "snapshot_date",
        "baseline_mrr",
    }
)
#: Server-issued IDs a request may *name*, to point at an existing record: it never sets one.
REFERENCE_FIELDS: Final = frozenset({"task_id", "outcome_id"})
SOURCE_REFERENCE_MAX_LENGTH: Final = 128
MAX_OUTCOMES_PER_IMPORT: Final = 1000

_MONEY_TEXT = re.compile(
    rf"\d{{1,{contract.MRR_INTEGER_DIGITS}}}(?:\.\d{{1,{contract.MRR_FRACTION_DIGITS}}})?",
    re.ASCII,
)


def _money(value: object) -> Decimal:
    """An amount in the Data Hub's form, from text or a ``Decimal``: never from a float."""
    if isinstance(value, Decimal):
        text = format(value, "f") if value.is_finite() else ""
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError('send an amount as a decimal string such as "1250.50", not a number')
    if not _MONEY_TEXT.fullmatch(text):
        raise ValueError(
            "an amount is digits with at most "
            f"{contract.MRR_FRACTION_DIGITS} after the point: no sign, symbol, separator or "
            "exponent"
        )
    return Decimal(text)


def _not_personal(value: str) -> str:
    if privacy.looks_like_personal_identifier(value):
        raise ValueError("looks like an email address or a phone number")
    return value


def _in_contract_range(value: date) -> date:
    if not contract.EARLIEST_DATE <= value <= contract.LATEST_DATE:
        raise ValueError(
            f"a date must fall between {contract.EARLIEST_DATE} and {contract.LATEST_DATE}"
        )
    return value


Money = Annotated[Decimal, BeforeValidator(_money)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
#: M3's account identifier, which also may not be a person's contact detail (as M1 refuses).
AccountId = Annotated[action_schemas.AccountId, AfterValidator(_not_personal)]
OutcomeDate = Annotated[IsoDate, AfterValidator(_in_contract_range)]
SourceReference = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=SOURCE_REFERENCE_MAX_LENGTH,
        pattern=r"^[^\x00-\x20\x7f](?:[^\x00-\x1f\x7f]*[^\x00-\x20\x7f])?$",
    ),
    AfterValidator(_not_personal),
]


class _Model(BaseModel):
    # Frozen, and a validation error never quotes the value it refused.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


# --- the outcome record ------------------------------------------------------------------------


class _OutcomeFacts(_Model):
    """The facts of one outcome, and the rules that hold them together."""

    account_id: AccountId
    window_start: OutcomeDate = Field(
        description="First day of the measurement window: the starting cohort is set here."
    )
    window_end: OutcomeDate = Field(description="Last day of the measurement window.")
    observed_on: OutcomeDate = Field(
        description="The day the source was read for the state at window_end: on or after it."
    )
    status: OutcomeStatus
    currency: Currency
    starting_mrr: Money = Field(
        description="MRR on window_start. Zero means the account was not a paying customer "
        "then, so it is not in that window's starting cohort."
    )
    ending_mrr: Money | None = Field(
        description="MRR on window_end. Required: null exactly when the status is unknown."
    )
    source_reference: SourceReference | None = Field(
        default=None, description="The record's reference in the source system, if any."
    )

    @model_validator(mode="after")
    def _consistent(self) -> _OutcomeFacts:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.observed_on < self.window_end:
            raise ValueError("an outcome is observed on or after the end of its window")
        match self.status:
            case "retained":
                if self.ending_mrr is None or self.ending_mrr == 0:
                    raise ValueError("a retained outcome needs an ending_mrr above zero")
            case "churned":
                if self.ending_mrr != 0:
                    raise ValueError("a churned outcome needs an ending_mrr of exactly zero")
            case "unknown":
                if self.ending_mrr is not None:
                    raise ValueError(
                        "an unknown outcome has no ending_mrr: a known amount decides the status"
                    )
        return self

    @property
    def key(self) -> tuple[str, date, date]:
        """One outcome per account and measurement window."""
        return (self.account_id, self.window_start, self.window_end)


class OutcomeSubmission(_OutcomeFacts):
    """One outcome as a client submits it: the facts only. Everything else is the server's."""


class OutcomeImportRequest(_Model):
    """Several outcomes at once, each account and window at most once."""

    outcomes: tuple[OutcomeSubmission, ...] = Field(
        min_length=1, max_length=MAX_OUTCOMES_PER_IMPORT
    )

    @model_validator(mode="after")
    def _each_key_once(self) -> OutcomeImportRequest:
        repeated = [k for k, n in Counter(o.key for o in self.outcomes).items() if n > 1]
        if repeated:
            raise ValueError(
                f"{len(repeated)} account and measurement window pair(s) appear more than once"
            )
        return self


class OutcomeProvenance(_Model):
    """Where the outcome came from and who recorded it: set by the server, never by a client."""

    source_system: SourceSystem
    recorded: Stamp


class AccountOutcome(_OutcomeFacts):
    """One recorded outcome, as the server keeps it."""

    outcome_id: UUID
    provenance: OutcomeProvenance

    @model_validator(mode="after")
    def _recorded_after_observation(self) -> AccountOutcome:
        if self.provenance.recorded.at.astimezone(UTC).date() < self.observed_on:
            raise ValueError("an outcome cannot be recorded before the day it was observed")
        return self


def check_unique_outcomes(outcomes: Iterable[_OutcomeFacts]) -> None:
    """Refuse two outcomes for one account and window, or two records with one outcome ID.

    Every repeat is listed by position, so all of them can be fixed at once.
    """
    problems: list[str] = []
    first_key: dict[tuple[str, date, date], int] = {}
    first_id: dict[UUID, int] = {}
    for position, outcome in enumerate(outcomes, start=1):
        if (seen := first_key.setdefault(outcome.key, position)) != position:
            problems.append(
                f"outcome {position} repeats the account and measurement window of outcome {seen}"
            )
        if (
            isinstance(outcome, AccountOutcome)
            and (seen := first_id.setdefault(outcome.outcome_id, position)) != position
        ):
            problems.append(f"outcome {position} repeats the outcome ID of outcome {seen}")
    if problems:
        raise SchemaValidationError("duplicate outcome records", problems)


# --- the metric results ------------------------------------------------------------------------


def _check_ratio(numerator, denominator, percent, reason, *, at_most_whole: bool) -> None:
    if denominator == 0:
        if numerator != 0 or percent is not None or not reason:
            raise ValueError("with no denominator there is no percent, and a reason is given")
        return
    if percent is None or reason is not None:
        raise ValueError("with a denominator the percent is given and no reason")
    if at_most_whole and numerator > denominator:
        raise ValueError("the numerator cannot exceed the denominator")


class CountRatio(_Model):
    """A share of accounts. ``percent`` is null, with a reason, when there is no denominator."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    percent: Decimal | None = Field(
        description="numerator / denominator x 100, cut toward zero to one decimal place."
    )
    unavailable_reason: str | None

    @model_validator(mode="after")
    def _consistent(self) -> CountRatio:
        _check_ratio(
            self.numerator,
            self.denominator,
            self.percent,
            self.unavailable_reason,
            at_most_whole=True,
        )
        return self


class AmountRatio(_Model):
    """A ratio of amounts in one currency. NRR may exceed 100; GRR and coverage may not."""

    numerator: Decimal = Field(ge=0)
    denominator: Decimal = Field(ge=0)
    percent: Decimal | None = Field(
        description="numerator / denominator x 100, cut toward zero to one decimal place."
    )
    unavailable_reason: str | None
    can_exceed_100: bool

    @model_validator(mode="after")
    def _consistent(self) -> AmountRatio:
        _check_ratio(
            self.numerator,
            self.denominator,
            self.percent,
            self.unavailable_reason,
            at_most_whole=not self.can_exceed_100,
        )
        return self


class OutcomeCounts(_Model):
    """Where every starting-cohort account stands as of ``as_of``: none is left out.

    ``eligible = immature + not_yet_observed + matured``;
    ``matured = retained + churned + unknown``; ``observed = retained + churned``.
    """

    eligible: int = Field(ge=0, description="Starting-cohort accounts: starting MRR above zero.")
    immature: int = Field(ge=0, description="The window ends after as_of.")
    not_yet_observed: int = Field(
        ge=0, description="The window has ended, but the outcome was observed after as_of."
    )
    matured: int = Field(ge=0, description="The window has ended and the outcome is known.")
    retained: int = Field(ge=0)
    churned: int = Field(ge=0)
    unknown: int = Field(ge=0)
    observed: int = Field(ge=0, description="retained + churned.")
    coverage: CountRatio = Field(description="observed / matured.")

    @model_validator(mode="after")
    def _adds_up(self) -> OutcomeCounts:
        if (
            self.eligible != self.immature + self.not_yet_observed + self.matured
            or self.matured != self.retained + self.churned + self.unknown
            or self.observed != self.retained + self.churned
            or (self.coverage.numerator, self.coverage.denominator) != (self.observed, self.matured)
        ):
            raise ValueError("the outcome counts must add up")
        return self


class MeasurementWindow(_Model):
    start: OutcomeDate
    end: OutcomeDate

    @model_validator(mode="after")
    def _ordered(self) -> MeasurementWindow:
        if self.end <= self.start:
            raise ValueError("the window must end after it starts")
        return self


class MeasurementCoverage(_Model):
    """Every record given, and why each did or did not count."""

    window: MeasurementWindow
    as_of: date
    records: int = Field(ge=0, description="Outcome records given.")
    other_window: int = Field(ge=0, description="Records for a different window: not used.")
    not_in_starting_cohort: int = Field(
        ge=0, description="Records in this window with zero starting MRR, such as new customers."
    )
    counts: OutcomeCounts

    @model_validator(mode="after")
    def _every_record(self) -> MeasurementCoverage:
        if self.records != self.other_window + self.not_in_starting_cohort + self.counts.eligible:
            raise ValueError("every record must be accounted for")
        return self


class CurrencyRevenueRetention(_Model):
    """GRR and NRR of one currency's starting cohort. Never added to another currency's."""

    currency: str
    counts: OutcomeCounts
    matured_starting_mrr: Decimal = Field(description="Starting MRR of the matured accounts.")
    unknown_starting_mrr: Decimal = Field(
        description="Starting MRR of the matured accounts whose outcome is unknown: in neither "
        "GRR nor NRR."
    )
    grr: AmountRatio = Field(
        description="Sum of min(starting, ending) MRR / starting MRR, over observed accounts."
    )
    nrr: AmountRatio = Field(
        description="Sum of ending MRR / starting MRR, over the same observed accounts."
    )
    revenue_coverage: AmountRatio = Field(
        description="Starting MRR of observed accounts / starting MRR of matured accounts."
    )


class RetentionMetrics(_Model):
    """Logo retention, GRR and NRR for one measurement window, with full coverage."""

    metrics_version: str
    coverage: MeasurementCoverage
    logo_retention: CountRatio = Field(description="retained / (retained + churned), matured.")
    revenue: tuple[CurrencyRevenueRetention, ...] = Field(description="One entry per currency.")
    definitions: dict[str, str]
    notes: tuple[str, ...]


class RetentionMetricsQuery(_Model):
    """The future metrics request: a window, and the date maturity is judged on."""

    window_start: OutcomeDate
    window_end: OutcomeDate
    as_of: OutcomeDate = Field(description="Required: nothing reads the server's clock.")


# --- a task and an outcome, in time ------------------------------------------------------------

ASSOCIATION_NOTE: Final = (
    "An association records that a completed retention task came before an observed outcome. "
    "It is a temporal and operational link: it does not show that the task changed the outcome, "
    "and it is not revenue saved or churn avoided."
)


class LinkTaskOutcomeRequest(_Model):
    """Link a completed task to an observed outcome. The server reads both and checks them."""

    task_id: UUID
    outcome_id: UUID


class TaskOutcomeAssociation(_Model):
    """A completed M3 task and an observed outcome of the same account, ordered in time."""

    association_id: UUID
    task_id: UUID
    outcome_id: UUID
    account_id: AccountId
    playbook: PlaybookRef
    relationship: TaskOutcomeRelationship = Field(
        description="When the task was completed (UTC date) relative to the outcome's window."
    )
    interpretation: Literal["observed_temporal_association"]
    task_completed_on: date
    window_start: date
    window_end: date
    outcome_status: Literal["retained", "churned"]
    created: Stamp

    @model_validator(mode="after")
    def _relationship_matches_dates(self) -> TaskOutcomeAssociation:
        if self.relationship != _relationship(
            self.task_completed_on, self.window_start, self.window_end
        ):
            raise ValueError("the relationship must follow from the task's completion date")
        return self


def _relationship(
    completed_on: date, window_start: date, window_end: date
) -> TaskOutcomeRelationship | None:
    if completed_on < window_start:
        return "task_completed_before_window"
    if completed_on <= window_end:
        return "task_completed_during_window"
    return None


def check_association(task: RetentionTask, outcome: AccountOutcome) -> TaskOutcomeRelationship:
    """How a task and an outcome relate in time, or ``ActionRefused`` saying why they cannot.

    The task must be completed, the outcome observed (retained or churned), both of
    one account, and the task completed no later than the window's last day.
    """
    if task.account_id != outcome.account_id:
        raise ActionRefused("account_mismatch", "the task and the outcome are different accounts'")
    if task.status != "completed" or task.completed is None:
        raise ActionRefused("task_not_completed", "only a completed task can be linked")
    if outcome.status not in OBSERVED_STATUSES:
        raise ActionRefused("outcome_not_observed", "an unknown outcome cannot be linked to a task")
    completed_on = task.completed.at.astimezone(UTC).date()
    relationship = _relationship(completed_on, outcome.window_start, outcome.window_end)
    if relationship is None:
        raise ActionRefused(
            "task_after_window",
            "the task was completed after the outcome's measurement window ended",
        )
    return relationship


def associate(
    task: RetentionTask, outcome: AccountOutcome, association_id: UUID, created: Stamp
) -> TaskOutcomeAssociation:
    """The association record for a checked pair; the server supplies its ID and stamp."""
    relationship = check_association(task, outcome)
    assert task.completed is not None and outcome.status != "unknown"
    return TaskOutcomeAssociation(
        association_id=association_id,
        task_id=task.task_id,
        outcome_id=outcome.outcome_id,
        account_id=outcome.account_id,
        playbook=task.playbook,
        relationship=relationship,
        interpretation="observed_temporal_association",
        task_completed_on=task.completed.at.astimezone(UTC).date(),
        window_start=outcome.window_start,
        window_end=outcome.window_end,
        outcome_status=outcome.status,
        created=created,
    )
