"""The Retention Action Center's playbooks: a fixed, versioned catalog keyed to M2's indicators.

**Built in, immutable, deterministic.** Five playbooks, written here and
nowhere else: no model, no language model, no external service and no
generated text. Each one is a frozen record whose ordered steps are a tuple;
the catalog is a tuple and its indexes are read-only mappings.

**M2's indicators, exactly.** Every indicator code the rule engine can produce
(``risk.RULE_ORDER``, rules ``risk.METHOD_VERSION``) is a trigger of exactly
one playbook, and no playbook has any other trigger. The import fails if that
ever stops being true. Nothing here re-reads a threshold, recalculates a level
or infers an indicator: ``applicable`` reads the indicators an assessment
already holds.

**Who gets a playbook.** Only an assessed account, and only for an indicator
that fired. A trialing, paused or canceled account (not assessed), an account
with no signal, and an indicator code the catalog does not know get none.

**Manual and approved, always.** Every playbook is ``manual_only`` and requires
a person's approval before its work starts (``action_lifecycle``). A playbook is
guidance for a human-led review: it is not evidence that any action will keep
an account or recover revenue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from churnsense.workspace import risk
from churnsense.workspace.action_lifecycle import ActionRefused
from churnsense.workspace.risk import IndicatorCode

CATALOG_VERSION: Final = "action_playbooks.v1"
#: The indicator rules this catalog's triggers were written against.
INDICATOR_RULES_VERSION: Final = risk.METHOD_VERSION

PlaybookId = Literal[
    "payment_resolution",
    "renewal_readiness",
    "customer_reengagement",
    "product_usage_investigation",
    "adoption_coverage_improvement",
]
Execution = Literal["manual_only"]


@dataclass(frozen=True, slots=True)
class Step:
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class Playbook:
    """One playbook: what triggers it, and the steps a person takes, in order."""

    playbook_id: PlaybookId
    version: int
    title: str
    description: str
    triggers: tuple[IndicatorCode, ...]
    steps: tuple[Step, ...]
    execution: Execution = "manual_only"
    requires_human_approval: bool = True

    def __post_init__(self) -> None:
        if self.execution != "manual_only" or self.requires_human_approval is not True:
            raise ValueError(f"{self.playbook_id}: every playbook is manual and approved first")
        if self.version < 1:
            raise ValueError(f"{self.playbook_id}: versions start at 1")
        if not self.triggers or len(set(self.triggers)) != len(self.triggers):
            raise ValueError(f"{self.playbook_id}: triggers must be given, each once")
        if not self.steps or not isinstance(self.steps, tuple):
            raise ValueError(f"{self.playbook_id}: steps must be a non-empty tuple")


CATALOG: Final[tuple[Playbook, ...]] = (
    Playbook(
        playbook_id="payment_resolution",
        version=1,
        title="Payment resolution",
        description="Review an overdue payment through the company's authorized billing "
        "process and agree on a human follow-up.",
        triggers=("payment_past_due",),
        steps=(
            Step(
                "Review the billing status",
                "Check the account's billing and payment status through the company's "
                "authorized billing process.",
            ),
            Step(
                "Investigate the payment issue",
                "Identify the reported payment issue and what is known about it.",
            ),
            Step(
                "Recommend a human follow-up",
                "Recommend the follow-up a member of the team should make with the account.",
            ),
            Step("Record the outcome", "Record the findings and the agreed next steps."),
        ),
    ),
    Playbook(
        playbook_id="renewal_readiness",
        version=1,
        title="Renewal readiness",
        description="Prepare a human-led renewal discussion from the renewal date, the "
        "account's context and its product usage.",
        triggers=("renewal_due",),
        steps=(
            Step(
                "Review the renewal",
                "Review the renewal date and the account's relevant context.",
            ),
            Step(
                "Review usage and history",
                "Review the available product usage and account history.",
            ),
            Step(
                "Prepare the renewal discussion",
                "Prepare a human-led renewal discussion with the account.",
            ),
            Step(
                "Record the outcome",
                "Record risks, open questions and the agreed next steps.",
            ),
        ),
    ),
    Playbook(
        playbook_id="customer_reengagement",
        version=1,
        title="Customer re-engagement",
        description="Look into a period without product activity and recommend a "
        "human-led check-in.",
        triggers=("inactivity",),
        steps=(
            Step("Review activity history", "Review the account's available activity history."),
            Step(
                "Investigate the reduced activity",
                "Look for possible reasons the account's activity fell.",
            ),
            Step(
                "Recommend a check-in",
                "Recommend an appropriate human-led check-in with the account.",
            ),
            Step("Record the outcome", "Record the findings and the follow-up actions."),
        ),
    ),
    Playbook(
        playbook_id="product_usage_investigation",
        version=1,
        title="Product usage investigation",
        description="Look into a fall in sessions or key product actions against its "
        "history, and recommend relevant guidance or enablement.",
        triggers=("sessions_decline", "key_actions_decline"),
        steps=(
            Step(
                "Review the usage change",
                "Review the observed change in usage and its historical context.",
            ),
            Step(
                "Investigate barriers",
                "Look for possible workflow or adoption barriers.",
            ),
            Step(
                "Recommend guidance",
                "Recommend relevant product guidance or customer enablement.",
            ),
            Step("Record the outcome", "Record the findings and the follow-up actions."),
        ),
    ),
    Playbook(
        playbook_id="adoption_coverage_improvement",
        version=1,
        title="Adoption coverage improvement",
        description="Look into low active-user coverage of purchased seats and recommend "
        "onboarding or enablement.",
        triggers=("low_active_user_coverage",),
        steps=(
            Step(
                "Review the coverage signal",
                "Review the available active-user coverage of purchased seats.",
            ),
            Step("Identify adoption gaps", "Identify potential gaps in adoption."),
            Step(
                "Recommend enablement",
                "Recommend appropriate onboarding or enablement activities.",
            ),
            Step("Record the outcome", "Record the findings and the follow-up actions."),
        ),
    ),
)


def _index() -> tuple[Mapping[str, Playbook], Mapping[str, Playbook]]:
    """Read-only indexes by ID and by trigger; refuses a catalog that does not cover M2 exactly."""
    by_id: dict[str, Playbook] = {}
    by_trigger: dict[str, Playbook] = {}
    for playbook in CATALOG:
        if playbook.playbook_id in by_id:
            raise ValueError(f"playbook {playbook.playbook_id!r} is in the catalog twice")
        by_id[playbook.playbook_id] = playbook
        for code in playbook.triggers:
            if code in by_trigger:
                raise ValueError(f"indicator {code!r} triggers more than one playbook")
            by_trigger[code] = playbook
    if set(by_trigger) != set(risk.RULE_ORDER):
        raise ValueError("the playbook triggers are not exactly M2's indicator codes")
    return MappingProxyType(by_id), MappingProxyType(by_trigger)


BY_ID, BY_TRIGGER = _index()


@dataclass(frozen=True, slots=True)
class Match:
    """A playbook that applies to an assessment, and the indicators that make it apply."""

    playbook: Playbook
    assessment: risk.Assessment
    #: The assessment's own indicators this playbook covers, in the assessment's order.
    indicators: tuple[risk.Indicator, ...]


def applicable(assessment: risk.Assessment) -> tuple[Match, ...]:
    """The playbooks the assessment's fired indicators point to, lead signal's first.

    Reads the indicators M2 recorded and nothing else. A not-assessed account,
    an account with no signal and an unknown indicator code get no playbook.
    """
    if assessment.method != risk.METHOD or assessment.method_version != INDICATOR_RULES_VERSION:
        raise ValueError(
            "the assessment was not made by the indicator rules this catalog was written for"
        )
    if not assessment.assessed:
        return ()
    grouped: dict[str, list[risk.Indicator]] = {}
    for indicator in assessment.indicators:
        playbook = BY_TRIGGER.get(indicator.code)
        if playbook is not None:
            grouped.setdefault(playbook.playbook_id, []).append(indicator)
    return tuple(
        Match(BY_ID[playbook_id], assessment, tuple(found))
        for playbook_id, found in grouped.items()
    )


def require_applicable(assessment: risk.Assessment, playbook_id: str) -> Match:
    """The match for ``playbook_id``, or ``ActionRefused`` saying why the playbook does not apply."""
    matches = applicable(assessment)
    if playbook_id not in BY_ID:
        raise ActionRefused("unknown_playbook", "the playbook is not in the catalog")
    if not assessment.assessed:
        raise ActionRefused(
            "account_not_assessed",
            f"the account is {assessment.status} and not assessed by the retention rules, "
            "so no playbook applies",
        )
    for match in matches:
        if match.playbook.playbook_id == playbook_id:
            return match
    triggers = ", ".join(BY_ID[playbook_id].triggers)
    raise ActionRefused(
        "playbook_not_applicable",
        f"the account's assessment on {assessment.as_of_date.isoformat()} has none of this "
        f"playbook's indicators ({triggers})",
    )
