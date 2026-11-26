"""The Retention Action Center's API contracts: typed and validated, and not served yet.

**Contracts only.** No route uses these models, and nothing stores a task. They
fix the shapes a future authenticated, multi-tenant service will accept and
return (``docs/V2_RETENTION_ACTION_CENTER.md``), and every rule a shape can hold
on its own is checked here, so that service cannot return a task in a state the
lifecycle (``action_lifecycle``) would never reach.

**The trust boundary is in the shapes.** A request holds only what a person
chooses: an account, a playbook, a due date, an assignee, a status to move to
and a reason. It has no field for an organization, an actor, a creator, an
approver, a timestamp, a status it starts in, a playbook version, a risk level,
an amount or any of M2's evidence (``SERVER_DERIVED_FIELDS``), and an unknown
field is refused rather than ignored, so none can be slipped in. The service
takes the organization and the actor from the authenticated principal, and the
evidence from the account's current assessment (``check_proposal``).

**Evidence travels with the task.** A task keeps the M2 indicators that made its
playbook apply, in M2's own shape (``workbench_schemas.Indicator``), with the
assessment's date and rules version: why the task exists stays readable after
the account's data moves on.

Every model is frozen and refuses unknown fields; every list is bounded; every
timestamp must carry a time zone; a refusal never quotes the value it refused.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from itertools import pairwise
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from churnsense.workspace import action_lifecycle as lifecycle
from churnsense.workspace import action_playbooks as playbooks
from churnsense.workspace import contract, risk, workbench
from churnsense.workspace import workbench_schemas as m2
from churnsense.workspace.action_lifecycle import ActionRefused, TaskStatus, TransitionTarget
from churnsense.workspace.action_playbooks import PlaybookId
from churnsense.workspace.risk import IndicatorCode

#: Fields only the server sets. No request model has any of them (a test holds it to that);
#: a request is never told its starting status either - every task starts proposed.
SERVER_DERIVED_FIELDS: Final = frozenset(
    {
        "organization_id",
        "tenant_id",
        "actor_id",
        "created_by",
        "approved_by",
        "completed_by",
        "task_id",
        "playbook_version",
        "playbook",
        "evidence",
        "indicators",
        "trigger_codes",
        "level",
        "risk_level",
        "account_level",
        "mrr",
        "created",
        "created_at",
        "updated_at",
        "approved",
        "started",
        "completed",
        "closed",
    }
)
REASON_MAX_LENGTH: Final = 500
MAX_PAGE_SIZE: Final = workbench.MAX_PAGE_SIZE
DEFAULT_PAGE_SIZE: Final = workbench.DEFAULT_PAGE_SIZE
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(value: object) -> object:
    """A calendar date, as ``YYYY-MM-DD`` text or a ``date``: never a number or a timestamp."""
    if isinstance(value, datetime):
        raise ValueError("expected a date (YYYY-MM-DD), not a date and time")
    if isinstance(value, date) or (isinstance(value, str) and _ISO_DATE.fullmatch(value)):
        return value
    raise ValueError("expected a date as YYYY-MM-DD")


#: The M2 account identifier (``account_external_id``): as M1 accepts it, already trimmed.
AccountId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=contract.ID_MAX_LENGTH,
        pattern=r"^[^\x00-\x20\x7f](?:[^\x00-\x1f\x7f]*[^\x00-\x20\x7f])?$",
    ),
]
#: A person's words: trimmed, not blank, bounded, no control character but a line break.
Reason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=REASON_MAX_LENGTH,
        pattern=r"^[^\x00-\x09\x0b-\x1f\x7f]+$",
    ),
]
IsoDate = Annotated[date, BeforeValidator(_iso_date)]
TaskSortKey = Literal["due_date_asc", "created_desc"]
AuditAction = Literal["task_proposed", "task_assigned", "task_unassigned", "status_changed"]


class _Model(BaseModel):
    # Frozen, and a validation error never quotes the value it refused.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


# --- the playbook catalog ----------------------------------------------------------------------


class PlaybookStep(_Model):
    position: int = Field(ge=1, description="The step's place in the playbook, from 1.")
    title: str
    detail: str


class Playbook(_Model):
    """One built-in playbook: guidance a person follows, never run automatically."""

    playbook_id: PlaybookId
    version: int = Field(ge=1)
    title: str
    description: str
    triggers: tuple[IndicatorCode, ...] = Field(
        min_length=1, description="The M2 indicator codes this playbook applies to."
    )
    steps: tuple[PlaybookStep, ...] = Field(min_length=1)
    execution: Literal["manual_only"] = Field(
        description="Every step is done by a person: nothing is sent to a customer or run "
        "automatically."
    )
    requires_human_approval: Literal[True] = Field(
        description="A task from this playbook is approved by a person before work starts."
    )

    @model_validator(mode="after")
    def _steps_in_order(self) -> Playbook:
        if [s.position for s in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("steps must be numbered 1, 2, 3... in order")
        return self


class PlaybookCatalog(_Model):
    """``GET`` the playbooks: the whole built-in catalog, in a fixed order."""

    catalog_version: str
    indicator_rules_version: str = Field(
        description="The M2 rules version whose indicator codes the triggers name."
    )
    playbooks: tuple[Playbook, ...]
    notes: tuple[str, ...]


# --- requests: only what a person chooses ------------------------------------------------------


class CreateTaskRequest(_Model):
    """Propose a task for an account from a playbook.

    The server checks that the account is in the caller's organization, that the
    playbook applies to the account's current assessment, and that the assignee
    is a member of the same organization; it records the evidence, the playbook
    version, the creator and the time itself. A task starts ``proposed``.
    """

    account_id: AccountId
    playbook_id: PlaybookId
    due_date: IsoDate | None = Field(
        default=None, description="A calendar date, from today to 366 days ahead."
    )
    assignee_id: UUID | None = Field(
        default=None, description="A member of the caller's organization, or none."
    )


class AssignTaskRequest(_Model):
    """Assign the task to a member of the caller's organization, or unassign it."""

    assignee_id: UUID | None = Field(
        description="Required; null unassigns, so an empty body is refused, not read as an "
        "unassignment."
    )


class TransitionTaskRequest(_Model):
    """Move a task to another status. Whoever asks is the authenticated principal."""

    to_status: TransitionTarget
    reason: Reason | None = Field(
        default=None,
        description="Required to dismiss or cancel; otherwise an optional note for the audit "
        "trail.",
    )

    @model_validator(mode="after")
    def _reason_when_closing(self) -> TransitionTaskRequest:
        if self.to_status in lifecycle.REASON_REQUIRED and self.reason is None:
            raise ValueError(f"a reason is required to move a task to {self.to_status}")
        return self


class TaskFilters(_Model):
    status: tuple[TaskStatus, ...] = ()
    account_id: AccountId | None = None
    assignee_id: UUID | None = None
    playbook_id: PlaybookId | None = None

    @field_validator("status")
    @classmethod
    def _each_status_once(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        wanted = set(value)
        return tuple(s for s in lifecycle.STATUSES if s in wanted)


class TaskListQuery(TaskFilters):
    """List the caller's organization's tasks: filtered, sorted, one page at a time."""

    sort: TaskSortKey = "due_date_asc"
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


# --- responses: what the server recorded -------------------------------------------------------


class PlaybookRef(_Model):
    playbook_id: PlaybookId
    version: int = Field(ge=1)
    title: str


class TaskEvidence(_Model):
    """Why the task exists: the M2 assessment and the indicators that made its playbook apply.

    Recorded from the server's assessment when the task is proposed, never from a request.
    """

    assessment_as_of_date: date = Field(description="The snapshot the assessment read.")
    method: Literal["rules"]
    method_version: str
    account_level: Literal["attention", "watch"] = Field(
        description="The account's M2 level on that date."
    )
    trigger_codes: tuple[IndicatorCode, ...] = Field(min_length=1)
    indicators: tuple[m2.Indicator, ...] = Field(
        min_length=1, description="The triggering indicators, in M2's shape and order."
    )

    @model_validator(mode="after")
    def _one_story(self) -> TaskEvidence:
        codes = tuple(i.code for i in self.indicators)
        if len(set(codes)) != len(codes) or codes != self.trigger_codes:
            raise ValueError("trigger_codes must name each indicator once, in the same order")
        if any(i.observed_on != self.assessment_as_of_date for i in self.indicators):
            raise ValueError("every indicator must be from the assessed snapshot")
        if self.account_level == "watch" and any(
            i.severity == "attention" for i in self.indicators
        ):
            raise ValueError("an account with an attention indicator is at the attention level")
        return self


class Stamp(_Model):
    """Who did something, and when. ``by`` is the authenticated principal's member ID."""

    by: UUID
    at: AwareDatetime


class Completion(Stamp):
    note: Reason | None = None


class Closure(Stamp):
    reason: Reason


class RetentionTask(_Model):
    """One retention task, consistent with its status: every stamp its path required, no other."""

    task_id: UUID
    account_id: AccountId
    playbook: PlaybookRef
    evidence: TaskEvidence
    status: TaskStatus
    assignee_id: UUID | None
    due_date: date | None
    created: Stamp
    updated_at: AwareDatetime
    approved: Stamp | None = None
    started: Stamp | None = None
    completed: Completion | None = None
    closed: Closure | None = None

    @model_validator(mode="after")
    def _consistent_with_status(self) -> RetentionTask:
        status = self.status
        _present(
            "approved",
            self.approved,
            required=status in lifecycle.APPROVED_STATUSES,
            allowed=status == "cancelled",
        )
        _present(
            "started",
            self.started,
            required=status in lifecycle.STARTED_STATUSES,
            allowed=status == "cancelled" and self.approved is not None,
        )
        _present("completed", self.completed, required=status == "completed", allowed=False)
        _present("closed", self.closed, required=status in lifecycle.REASON_REQUIRED, allowed=False)
        times = [
            s.at
            for s in (self.created, self.approved, self.started, self.completed, self.closed)
            if s is not None
        ]
        if times != sorted(times) or self.updated_at < times[-1]:
            raise ValueError(
                "created, approved, started and completed or closed must be in time order, "
                "and updated_at no earlier than the last"
            )
        current = playbooks.BY_ID[self.playbook.playbook_id]
        if self.playbook.version == current.version and not set(self.evidence.trigger_codes) <= set(
            current.triggers
        ):
            raise ValueError("the evidence names an indicator the playbook does not cover")
        return self


def _present(name: str, value: object, *, required: bool, allowed: bool) -> None:
    if required and value is None:
        raise ValueError(f"{name} is required for a task in this status")
    if value is not None and not required and not allowed:
        raise ValueError(f"{name} must be null for a task in this status")


class TaskDetail(_Model):
    """One task, the playbook version it follows, and the statuses it may move to next."""

    task: RetentionTask
    playbook: Playbook
    next_statuses: tuple[TransitionTarget, ...] = Field(
        description="The lifecycle's allowed moves from the task's status; empty when final."
    )
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def _matches_task(self) -> TaskDetail:
        ref = self.task.playbook
        if (self.playbook.playbook_id, self.playbook.version) != (ref.playbook_id, ref.version):
            raise ValueError("the playbook must be the task's playbook version")
        if self.next_statuses != lifecycle.next_statuses(self.task.status):
            raise ValueError("next_statuses must be the lifecycle's moves from the task's status")
        return self


class TaskPage(_Model):
    """One page of the caller's organization's tasks."""

    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    pages: int = Field(ge=1)
    sort: TaskSortKey
    filters: TaskFilters
    rows: tuple[RetentionTask, ...] = Field(max_length=MAX_PAGE_SIZE)
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def _bounded(self) -> TaskPage:
        if len(self.rows) > self.page_size or self.page > self.pages:
            raise ValueError("a page holds at most page_size rows and is within pages")
        return self


class TaskAuditEvent(_Model):
    """One recorded change to a task, written by the server, never sent by a client."""

    event_id: UUID
    task_id: UUID
    sequence: int = Field(ge=1, description="The event's place in the task's history, from 1.")
    action: AuditAction
    actor_id: UUID = Field(description="The authenticated principal who acted.")
    occurred_at: AwareDatetime
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    assignee_id: UUID | None = None
    reason: Reason | None = None

    @model_validator(mode="after")
    def _shape_of_action(self) -> TaskAuditEvent:
        match self.action:
            case "task_proposed":
                if self.sequence != 1 or (self.from_status, self.to_status) != (None, "proposed"):
                    raise ValueError("a task's first event proposes it: from null to proposed")
                if self.reason is not None:
                    raise ValueError("proposing a task records no reason")
            case "task_assigned" | "task_unassigned":
                if self.from_status is not None or self.to_status is not None:
                    raise ValueError("an assignment event records no status change")
                if (self.assignee_id is None) != (self.action == "task_unassigned"):
                    raise ValueError("task_assigned names the assignee; task_unassigned names none")
                if self.reason is not None:
                    raise ValueError("an assignment event records no reason")
            case "status_changed":
                if self.from_status is None or self.to_status is None:
                    raise ValueError("a status change records the status before and after")
                if self.assignee_id is not None:
                    raise ValueError("a status change records no assignee")
                try:
                    lifecycle.validate_transition(self.from_status, self.to_status, self.reason)
                except ActionRefused as refused:
                    raise ValueError(str(refused)) from None
        return self


class TaskEventPage(_Model):
    """One page of a task's audit trail, oldest first."""

    task_id: UUID
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    pages: int = Field(ge=1)
    events: tuple[TaskAuditEvent, ...] = Field(max_length=MAX_PAGE_SIZE)

    @model_validator(mode="after")
    def _one_task_in_order(self) -> TaskEventPage:
        if any(e.task_id != self.task_id for e in self.events):
            raise ValueError("every event must be this task's")
        sequences = [e.sequence for e in self.events]
        if any(b <= a for a, b in pairwise(sequences)):
            raise ValueError("events must be in increasing sequence")
        if len(self.events) > self.page_size or self.page > self.pages:
            raise ValueError("a page holds at most page_size events and is within pages")
        return self


class TaskProposal(_Model):
    """What a future service would record for a ``CreateTaskRequest``, checked. Nothing is stored."""

    account_id: AccountId
    playbook: PlaybookRef
    evidence: TaskEvidence
    due_date: date | None
    assignee_id: UUID | None = Field(
        description="As requested; the service must still confirm the organization membership."
    )


# --- from the domain ---------------------------------------------------------------------------

NOTE_GUIDANCE: Final = (
    "A playbook is guidance for a human-led review. It is not evidence that any action will "
    "keep an account or recover revenue."
)
NOTE_MANUAL: Final = (
    "Every playbook is manual: nothing is sent to a customer or run automatically, and no work "
    "starts before a person approves the task."
)
NOTE_RULES: Final = (
    f"Playbooks apply to the indicators of the transparent rules ({risk.METHOD_VERSION}). "
    "Trialing, paused and canceled accounts are not assessed by those rules and get none."
)


def playbook_out(p: playbooks.Playbook) -> Playbook:
    return Playbook(
        playbook_id=p.playbook_id,
        version=p.version,
        title=p.title,
        description=p.description,
        triggers=p.triggers,
        steps=tuple(
            PlaybookStep(position=n, title=s.title, detail=s.detail)
            for n, s in enumerate(p.steps, start=1)
        ),
        execution=p.execution,
        requires_human_approval=p.requires_human_approval,
    )


def catalog_out() -> PlaybookCatalog:
    return PlaybookCatalog(
        catalog_version=playbooks.CATALOG_VERSION,
        indicator_rules_version=playbooks.INDICATOR_RULES_VERSION,
        playbooks=tuple(playbook_out(p) for p in playbooks.CATALOG),
        notes=(NOTE_GUIDANCE, NOTE_MANUAL, NOTE_RULES),
    )


def task_evidence(match: playbooks.Match) -> TaskEvidence:
    """The match's assessment and triggering indicators, in M2's own response shape."""
    assessment = match.assessment
    codes = tuple(i.code for i in match.indicators)
    shown = {i.code: i for i in workbench.assessment_out(assessment).indicators}
    return TaskEvidence(
        assessment_as_of_date=assessment.as_of_date,
        method=assessment.method,
        method_version=assessment.method_version,
        account_level=assessment.level,
        trigger_codes=codes,
        indicators=tuple(shown[code] for code in codes),
    )


def check_proposal(
    request: CreateTaskRequest, assessment: risk.Assessment, today: date
) -> TaskProposal:
    """A request checked against the account's current assessment: the evidence it would record.

    ``assessment`` is the server's own for ``request.account_id`` in the caller's
    organization; nothing the request says about risk is read, because it can
    say nothing. Raises ``ActionRefused`` when the playbook does not apply or the
    due date is out of range. Creates, stores and changes nothing.
    """
    if assessment.account_id != request.account_id:
        raise ValueError("the assessment is not the requested account's")
    match = playbooks.require_applicable(assessment, request.playbook_id)
    if request.due_date is not None:
        lifecycle.validate_due_date(request.due_date, today)
    p = match.playbook
    return TaskProposal(
        account_id=request.account_id,
        playbook=PlaybookRef(playbook_id=p.playbook_id, version=p.version, title=p.title),
        evidence=task_evidence(match),
        due_date=request.due_date,
        assignee_id=request.assignee_id,
    )
