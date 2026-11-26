"""The Retention Action Center's task lifecycle: six statuses and the moves between them.

**Contracts and checks only.** Nothing here creates, stores, changes or reads a
task. Each function takes the facts it needs and either returns or raises
``ActionRefused``. A future authenticated service calls them before it changes
anything, and writes the audit event itself.

**Human approval comes first.** A task is ``proposed`` and no work starts until
a person approves it:

=============== =========================================
current         allowed next
=============== =========================================
``proposed``    ``approved``, ``dismissed``, ``cancelled``
``approved``    ``in_progress``, ``cancelled``
``in_progress`` ``completed``, ``cancelled``
``completed``   none: terminal
``dismissed``   none: terminal
``cancelled``   none: terminal
=============== =========================================

A move to the status a task already has is refused, not ignored. Dismissing or
cancelling needs a reason. A terminal task is never reopened. There are no
custom statuses and no configurable workflow.

No function here takes the identity of whoever acts. A future service takes it
from the authenticated principal, never from a request.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import Final, Literal

from churnsense.exceptions import SchemaValidationError

TaskStatus = Literal["proposed", "approved", "in_progress", "completed", "dismissed", "cancelled"]
#: The statuses a transition may move a task to: nothing moves a task back to proposed.
TransitionTarget = Literal["approved", "in_progress", "completed", "dismissed", "cancelled"]

STATUSES: Final[tuple[str, ...]] = (
    "proposed",
    "approved",
    "in_progress",
    "completed",
    "dismissed",
    "cancelled",
)
#: Every allowed move, and nothing else. A status with no next status is terminal.
TRANSITIONS: Final = MappingProxyType(
    {
        "proposed": frozenset({"approved", "dismissed", "cancelled"}),
        "approved": frozenset({"in_progress", "cancelled"}),
        "in_progress": frozenset({"completed", "cancelled"}),
        "completed": frozenset(),
        "dismissed": frozenset(),
        "cancelled": frozenset(),
    }
)
TERMINAL: Final = frozenset(s for s, allowed in TRANSITIONS.items() if not allowed)
#: Moves that close a task without completing it, so a person must say why.
REASON_REQUIRED: Final = frozenset({"dismissed", "cancelled"})
#: Statuses a task reaches only after a person approved it.
APPROVED_STATUSES: Final = frozenset({"approved", "in_progress", "completed"})
#: Statuses a task reaches only after work started.
STARTED_STATUSES: Final = frozenset({"in_progress", "completed"})
#: A due date is at most this many days after the day it is set.
MAX_DUE_DAYS: Final = 366


class ActionRefused(SchemaValidationError):
    """A lifecycle, assignment, due-date or playbook rule refused the request.

    A ``SchemaValidationError``, so the service's existing handler answers it as a
    422 ``invalid_input`` with the reason in ``problems``. ``code`` names the rule,
    so a caller can tell the refusals apart without reading the text.
    """

    def __init__(self, code: str, message: str) -> None:
        # Not ``super().__init__(message, [message])``: that would repeat the message in
        # the text ("reason: reason").
        super().__init__(message)
        self.problems = [message]
        self.code = code


def _known(status: str, role: str) -> None:
    if status not in TRANSITIONS:
        raise ActionRefused("unknown_status", f"{role} status is not one of {', '.join(STATUSES)}")


def next_statuses(status: str) -> tuple[str, ...]:
    """The statuses ``status`` may move to, in ``STATUSES`` order; none for a terminal one."""
    _known(status, "the current")
    return tuple(s for s in STATUSES if s in TRANSITIONS[status])


def validate_transition(current: str, target: str, reason: str | None = None) -> None:
    """Refuse a move the lifecycle does not allow; return if it does.

    ``reason`` is checked only for being present and not blank: its length and
    characters are the request contract's to check.
    """
    _known(current, "the current")
    _known(target, "the requested")
    if target == current:
        raise ActionRefused(
            "no_op_transition", f"the task is already {current}: nothing would change"
        )
    if current in TERMINAL:
        raise ActionRefused(
            "terminal_status", f"the task is {current}, a final status: it cannot be reopened"
        )
    if target not in TRANSITIONS[current]:
        if target in STARTED_STATUSES and current == "proposed":
            raise ActionRefused(
                "approval_required",
                f"a proposed task must be approved before it can be {target}",
            )
        allowed = ", ".join(sorted(TRANSITIONS[current]))
        raise ActionRefused(
            "transition_not_allowed",
            f"a task that is {current} can move only to {allowed}, not to {target}",
        )
    if target in REASON_REQUIRED and (reason is None or not reason.strip()):
        raise ActionRefused("reason_required", f"a reason is required to move a task to {target}")


def validate_assignment(status: str, current: str | None, new: str | None) -> None:
    """Refuse an assignment change on a closed task, or one that changes nothing.

    ``current`` and ``new`` are member identifiers (``None``: unassigned). Whether
    ``new`` is a member of the task's organization is the future service's check:
    no membership exists here.
    """
    _known(status, "the current")
    if status in TERMINAL:
        raise ActionRefused(
            "terminal_status", f"the task is {status}: its assignee can no longer change"
        )
    if new == current:
        raise ActionRefused(
            "no_op_assignment",
            "the task is already unassigned"
            if new is None
            else "the task already has that assignee",
        )


def validate_due_date(due: date, today: date) -> None:
    """Refuse a due date before ``today`` or more than ``MAX_DUE_DAYS`` after it.

    ``today`` is the service's date for the organization, passed in: nothing
    here reads a clock.
    """
    days = (due - today).days
    if days < 0:
        raise ActionRefused("due_date_in_past", "the due date is before today")
    if days > MAX_DUE_DAYS:
        raise ActionRefused(
            "due_date_too_far", f"the due date is more than {MAX_DUE_DAYS} days from today"
        )
