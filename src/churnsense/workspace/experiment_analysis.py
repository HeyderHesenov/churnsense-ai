"""A descriptive summary of a two-arm retention experiment: pure, deterministic, exact.

**As assigned, everyone.** Every assignment is counted in the arm it was assigned
to (intention to treat): no account is moved, dropped or re-labelled by a task's
status, a reported exposure or a missing outcome. Each account's outcome is the
recorded outcome of its own window - from its assignment date, for the declared
number of days - and of no other.

**Where each account stands, as of ``as_of``** (passed in; no clock is read):

* ``immature`` - its outcome window ends after ``as_of``: not yet in the matured
  population;
* ``missing`` - the window has ended, but no outcome was recorded and observed by
  ``as_of``;
* ``unknown`` - the outcome was recorded as unknown;
* ``retained`` or ``churned`` - an observed outcome.

**Eligibility comes first.** Every assignment carries its server-derived baseline
evidence (``ExperimentAssignment.eligibility``): MRR above zero, a currency, a
snapshot date no later than the assignment. An outcome belongs to the assignment
only if it states the same starting MRR and currency; one that does not is
refused, never corrected, and an assignment without paying-baseline evidence is
refused as a protocol violation. Eligibility is settled before assignment, so
nothing after it removes an account from its arm.

**The matured ITT population** of an arm is every assigned account whose window
has ended: ``retained + churned + unknown + missing``. Its **ITT retention rate**
is retained / population, and is given only when the data is complete - every
account in the population retained or churned. A missing or unknown outcome is
never imputed: the rate is then null with ``incomplete_outcome_data``, and the
primary treatment-control difference is null too.

**The observed-outcome diagnostic** is retained / (retained + churned), with its
coverage (observed / matured): a complete-case figure that leaves out missing and
unknown outcomes. It is reported separately, with its own difference, and is never
the primary ITT result.

**Descriptive only.** There is no p-value, confidence interval, significance test,
winner or revenue figure, and the result says so in fixed fields
(``experiment_schemas.ExperimentAnalysis``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Final

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import experiment_schemas as ex
from churnsense.workspace.action_lifecycle import ActionRefused
from churnsense.workspace.outcome_schemas import AccountOutcome, check_unique_outcomes
from churnsense.workspace.retention_metrics import count_ratio, percent

STANDINGS: Final[tuple[str, ...]] = ("immature", "missing", "unknown", "retained", "churned")
NO_MATURED: Final = "No assignment in this arm has a matured outcome window as of as_of."
LIMITATION_DESCRIPTIVE: Final = (
    "The comparison describes observed outcomes by assigned arm. It does not establish "
    "causality or statistical significance."
)


def outcome_window(definition: ex.ExperimentDefinition, assignment: ex.ExperimentAssignment):
    """The assignment's outcome window: from its UTC assignment date, for the declared days."""
    start = assignment.assigned_on
    return start, start + timedelta(days=definition.outcome_window_days)


def check_assignments(
    definition: ex.ExperimentDefinition, assignments: Sequence[ex.ExperimentAssignment]
) -> None:
    """Refuse assignments of another experiment or version, outside enrollment, or repeated.

    Also refuses, as a protocol violation, an assignment whose eligibility evidence does
    not admit it - one that skipped the contract's own checks. Every problem is listed
    by position, so all of them can be fixed at once.
    """
    problems: list[str] = []
    accounts: dict[str, int] = {}
    ids: dict[object, int] = {}
    for position, a in enumerate(assignments, start=1):
        if a.experiment_id != definition.experiment_id:
            problems.append(f"assignment {position} is for another experiment")
        elif a.experiment_version != definition.version:
            problems.append(
                f"assignment {position} is for version {a.experiment_version}, not "
                f"{definition.version}"
            )
        if not definition.enrollment_start <= a.assigned_on <= definition.enrollment_end:
            problems.append(f"assignment {position} is dated outside the enrollment period")
        if (seen := accounts.setdefault(a.account_id, position)) != position:
            problems.append(f"assignment {position} repeats the account of assignment {seen}")
        if (seen := ids.setdefault(a.assignment_id, position)) != position:
            problems.append(f"assignment {position} repeats the ID of assignment {seen}")
        evidence = a.eligibility
        if (
            not isinstance(evidence, ex.EligibilityEvidence)
            or not evidence.baseline_mrr > 0
            or evidence.account_id != a.account_id
            or evidence.snapshot_date > a.assigned_on
        ):
            problems.append(
                f"assignment {position} is not eligible: its baseline evidence does not show "
                "the account paying, as the same account, by the assignment date"
            )
    if problems:
        raise SchemaValidationError("invalid experiment assignments", problems)


def baseline_mismatch(
    assignment: ex.ExperimentAssignment, outcome: AccountOutcome
) -> tuple[str, str] | None:
    """Why the outcome is not of the assignment's baseline cohort, as (code, message); or None.

    The assignment's evidence is authoritative: a differing currency or starting MRR is
    refused, never replaced.
    """
    baseline = assignment.eligibility
    if outcome.currency != baseline.currency:
        return (
            "currency_mismatch",
            f"the outcome is in {outcome.currency}, not the assignment's baseline currency "
            f"{baseline.currency}",
        )
    if outcome.starting_mrr != baseline.baseline_mrr:
        return (
            "baseline_mismatch",
            f"the outcome's starting MRR {outcome.starting_mrr} is not the assignment's baseline "
            f"MRR {baseline.baseline_mrr}",
        )
    return None


def link_outcome(
    definition: ex.ExperimentDefinition,
    assignment: ex.ExperimentAssignment,
    outcome: AccountOutcome,
) -> ex.ExperimentOutcome:
    """The link between an assignment and its account's outcome, checked; or ``ActionRefused``."""
    check_assignments(definition, [assignment])
    if outcome.account_id != assignment.account_id:
        raise ActionRefused("account_mismatch", "the outcome is not the assigned account's")
    start, end = outcome_window(definition, assignment)
    if (outcome.window_start, outcome.window_end) != (start, end):
        raise ActionRefused(
            "window_mismatch",
            f"the assignment's outcome window is {start.isoformat()} to {end.isoformat()} "
            f"({definition.outcome_window_days} days from the assignment date)",
        )
    if mismatch := baseline_mismatch(assignment, outcome):
        raise ActionRefused(*mismatch)
    return ex.ExperimentOutcome(
        experiment_id=definition.experiment_id,
        experiment_version=definition.version,
        assignment_id=assignment.assignment_id,
        outcome_id=outcome.outcome_id,
        account_id=assignment.account_id,
        arm=assignment.arm,
    )


def _itt_rate(arm: str, n: dict[str, int], matured: int) -> ex.IttRetentionRate:
    counts = {k: n[k] for k in ("retained", "churned", "unknown", "missing")}
    if matured == 0:
        unavailable: ex.IttUnavailable | None = "no_matured_assignments"
        reason: str | None = NO_MATURED
    elif n["unknown"] or n["missing"]:
        unavailable = "incomplete_outcome_data"
        reason = (
            f"Incomplete outcome data: {n['missing']} missing and {n['unknown']} unknown of "
            f"{matured} matured {arm} account(s). No outcome is imputed, so there is no ITT rate."
        )
    else:
        unavailable = reason = None
    return ex.IttRetentionRate(
        population=matured,
        **counts,
        percent=None if unavailable else percent(n["retained"], matured),
        unavailable=unavailable,
        unavailable_reason=reason,
    )


def _summary(definition: ex.ExperimentDefinition, arm: str, standings: list[str]) -> ex.ArmSummary:
    n = {s: standings.count(s) for s in STANDINGS}
    observed = n["retained"] + n["churned"]
    matured = len(standings) - n["immature"]
    return ex.ArmSummary(
        arm=arm,
        allocation_percent=definition.allocation(arm),
        assigned=len(standings),
        immature=n["immature"],
        matured=matured,
        retained=n["retained"],
        churned=n["churned"],
        unknown=n["unknown"],
        missing=n["missing"],
        itt_retention_rate=_itt_rate(arm, n, matured),
        observed_outcome_diagnostic=ex.ObservedOutcomeDiagnostic(
            retained=n["retained"],
            observed=observed,
            unknown=n["unknown"],
            missing=n["missing"],
            observed_retention_rate=count_ratio(
                n["retained"],
                observed,
                NO_MATURED
                if matured == 0
                else "No matured assignment in this arm has an observed retained or churned "
                "outcome.",
            ),
            outcome_coverage=count_ratio(observed, matured, NO_MATURED),
        ),
    )


def _cut_difference(treatment: Fraction, control: Fraction) -> Decimal:
    tenths = int((treatment - control) * 1000)  # int() cuts toward zero, as every percentage
    return Decimal(tenths).scaleb(-1)


def _itt_difference(control: ex.ArmSummary, treatment: ex.ArmSummary) -> ex.RateDifference:
    lacking = [s for s in (control, treatment) if s.itt_retention_rate.percent is None]
    if lacking:
        why = "; ".join(f"{s.arm}: {s.itt_retention_rate.unavailable}" for s in lacking)
        return ex.RateDifference(
            metric="logo_retention_rate",
            basis="intention_to_treat_complete_data",
            treatment_minus_control_pp=None,
            unavailable_reason="The primary comparison needs a complete-data ITT retention rate "
            f"in both arms ({why}).",
        )
    return ex.RateDifference(
        metric="logo_retention_rate",
        basis="intention_to_treat_complete_data",
        treatment_minus_control_pp=_cut_difference(
            Fraction(treatment.retained, treatment.matured),
            Fraction(control.retained, control.matured),
        ),
        unavailable_reason=None,
    )


def _observed_difference(control: ex.ArmSummary, treatment: ex.ArmSummary) -> ex.RateDifference:
    rates = [s.observed_outcome_diagnostic for s in (control, treatment)]
    lacking = [s.arm for s, d in zip((control, treatment), rates, strict=True) if d.observed == 0]
    if lacking:
        return ex.RateDifference(
            metric="logo_retention_rate",
            basis="observed_outcomes_only",
            treatment_minus_control_pp=None,
            unavailable_reason="The observed-outcome diagnostic needs an observed outcome in "
            f"both arms; {' and '.join(lacking)} has none.",
        )
    c, t = rates
    return ex.RateDifference(
        metric="logo_retention_rate",
        basis="observed_outcomes_only",
        treatment_minus_control_pp=_cut_difference(
            Fraction(t.retained, t.observed), Fraction(c.retained, c.observed)
        ),
        unavailable_reason=None,
    )


def _limitations(
    arms: tuple[ex.ArmSummary, ex.ArmSummary], itt: ex.RateDifference, unused: int
) -> tuple[ex.Limitation, ...]:
    found = [ex.Limitation(code="descriptive_only", message=LIMITATION_DESCRIPTIVE)]
    for code, field, words in (
        (
            "immature_assignments",
            "immature",
            "have an outcome window that ends after as_of, "
            "so they are not yet in the matured population",
        ),
        ("missing_outcomes", "missing", "have no outcome recorded and observed by as_of"),
        ("unknown_outcomes", "unknown", "have an outcome recorded as unknown"),
    ):
        if counts := [f"{getattr(s, field)} {s.arm}" for s in arms if getattr(s, field)]:
            found.append(
                ex.Limitation(
                    code=code, message=f"Assigned accounts ({', '.join(counts)}) {words}."
                )
            )
    if incomplete := [
        s.arm for s in arms if s.itt_retention_rate.unavailable == "incomplete_outcome_data"
    ]:
        found.append(
            ex.Limitation(
                code="incomplete_outcome_data",
                message=f"The ITT retention rate of {' and '.join(incomplete)} is unavailable: "
                "missing and unknown outcomes are not imputed. The observed-outcome diagnostic "
                "leaves those accounts out, so it is not the ITT estimate.",
            )
        )
    if itt.unavailable_reason is not None:
        found.append(
            ex.Limitation(code="itt_comparison_unavailable", message=itt.unavailable_reason)
        )
    if unused:
        found.append(
            ex.Limitation(
                code="outcomes_not_used",
                message=f"{unused} outcome record(s) match no assignment's account and outcome "
                "window, and are not in this analysis.",
            )
        )
    return tuple(found)


def analyze(
    definition: ex.ExperimentDefinition,
    assignments: Sequence[ex.ExperimentAssignment],
    outcomes: Sequence[AccountOutcome],
    as_of: date,
) -> ex.ExperimentAnalysis:
    """Each arm as assigned: its ITT rate when complete, and the observed-outcome diagnostic.

    Refuses invalid, ineligible or repeated assignments, assignments dated after
    ``as_of``, duplicate outcome records, and any outcome of an assignment's window that
    differs from its baseline currency or MRR. Changes nothing it is given.
    """
    check_assignments(definition, assignments)
    if late := [p for p, a in enumerate(assignments, start=1) if a.assigned_on > as_of]:
        raise SchemaValidationError(
            "assignments dated after as_of",
            [f"assignment {p} is dated after as_of" for p in late],
        )
    check_unique_outcomes(outcomes)
    by_key = {o.key: o for o in outcomes}
    used: set[tuple[str, date, date]] = set()
    standings: dict[str, list[str]] = {arm: [] for arm in ex.ARMS}
    mismatches: list[str] = []
    for position, a in enumerate(assignments, start=1):
        start, end = outcome_window(definition, a)
        key = (a.account_id, start, end)
        outcome = by_key.get(key)
        if outcome is not None:
            used.add(key)
            if mismatch := baseline_mismatch(a, outcome):
                mismatches.append(f"assignment {position}: {mismatch[1]}")
        if end > as_of:
            standing = "immature"
        elif outcome is None or outcome.observed_on > as_of:
            standing = "missing"
        else:
            standing = outcome.status
        standings[a.arm].append(standing)
    if mismatches:
        raise SchemaValidationError("outcomes outside the assignments' baseline cohort", mismatches)
    control = _summary(definition, "control", standings["control"])
    treatment = _summary(definition, "treatment", standings["treatment"])
    itt = _itt_difference(control, treatment)
    unused = len(outcomes) - len(used)
    return ex.ExperimentAnalysis(
        analysis_type="descriptive",
        causal_interpretation="not_established",
        statistical_inference="not_performed",
        statement=ex.DESCRIPTIVE_STATEMENT,
        experiment_id=definition.experiment_id,
        experiment_version=definition.version,
        primary_metric=definition.primary_metric,
        analysis_principle=definition.analysis_principle,
        as_of=as_of,
        control=control,
        treatment=treatment,
        itt_retention_rate_difference=itt,
        observed_outcome_difference=_observed_difference(control, treatment),
        outcomes_not_used=unused,
        limitations=_limitations((control, treatment), itt, unused),
    )
