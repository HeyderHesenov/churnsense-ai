"""Retention experiments (M4): the two-arm definition, assignments, links and the analysis.

**Test data only.** The experiment, its assignments and every outcome below are
hand-written for these tests. No count or rate here is a measured result, and
none says anything about whether a retention action works. Every expected count
and rate is worked out by hand in the scenario's docstring.
"""

from __future__ import annotations

import inspect
import json
import random
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import experiment_analysis as ea
from churnsense.workspace import experiment_schemas as ex
from churnsense.workspace import outcome_schemas as oc
from churnsense.workspace.action_lifecycle import ActionRefused
from churnsense.workspace.snapshot import Snapshot
from tests.test_wording import _offending
from tests.test_workspace_retention_impact import _schema_names

MEMBER = UUID("6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f")
EXPERIMENT = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
ENROLL_START, ENROLL_END = date(2026, 1, 1), date(2026, 1, 31)
DAYS = 90
AS_OF = date(2026, 4, 20)
_ids = iter(range(1, 100_000))


def design(**changes) -> dict:
    base = {
        "name": "Renewal readiness check-in",
        "objective": "Learn whether a human-led renewal review is followed by different "
        "observed logo retention.",
        "hypothesis": "Accounts offered a renewal readiness review show a different observed "
        "logo retention rate at 90 days than accounts in the usual process.",
        "arms": [
            {"arm": "control", "allocation_percent": 50, "description": "The usual process."},
            {
                "arm": "treatment",
                "allocation_percent": 50,
                "description": "A renewal readiness review by the account team.",
            },
        ],
        "enrollment_start": ENROLL_START,
        "enrollment_end": ENROLL_END,
        "outcome_window_days": DAYS,
    }
    return {**base, **changes}


def definition(**changes) -> ex.ExperimentDefinition:
    return ex.ExperimentDefinition.model_validate(
        {
            "experiment_id": EXPERIMENT,
            "version": 1,
            "created": {"by": MEMBER, "at": datetime(2025, 12, 15, 9, tzinfo=UTC)},
            **design(**changes),
        }
    )


def evidence(account: str, on: date = date(2026, 1, 5), **changes) -> dict:
    """Baseline evidence as the server would take it from the account's M2 snapshot (test data):
    paying 100.00 USD, snapshot the day before the assignment."""
    return {
        "account_id": account,
        "snapshot_date": on - timedelta(days=1),
        "baseline_mrr": "100.00",
        "currency": "USD",
        **changes,
    }


def assignment(account: str, arm: str, on: date = date(2026, 1, 5), **changes):
    body = {
        "assignment_id": UUID(int=next(_ids)),
        "experiment_id": EXPERIMENT,
        "experiment_version": 1,
        "account_id": account,
        "arm": arm,
        "assigned_at": datetime(on.year, on.month, on.day, 10, tzinfo=UTC),
        "assignment_method": "server_randomized",
        "eligibility": evidence(account, on),
        **changes,
    }
    return ex.ExperimentAssignment.model_validate(body)


def result(account: str, start: date = date(2026, 1, 5), **changes) -> oc.AccountOutcome:
    """The recorded outcome of ``account``'s 90-day window from ``start`` (test data)."""
    end = start + timedelta(days=DAYS)
    body = {
        "outcome_id": UUID(int=next(_ids)),
        "account_id": account,
        "window_start": start,
        "window_end": end,
        "observed_on": end + timedelta(days=5),
        "status": "retained",
        "currency": "USD",
        "starting_mrr": "100.00",
        "ending_mrr": "100.00",
        "provenance": {
            "source_system": "billing_system",
            "recorded": {"by": MEMBER, "at": datetime(2026, 12, 1, tzinfo=UTC)},
        },
        **changes,
    }
    return oc.AccountOutcome.model_validate(body)


# --- the definition ----------------------------------------------------------------------------


def test_a_two_arm_account_level_itt_definition_round_trips():
    d = definition()
    assert (d.primary_metric, d.randomization_unit, d.analysis_principle) == (
        "logo_retention_rate",
        "account",
        "intention_to_treat",
    )
    assert [a.arm for a in d.arms] == ["control", "treatment"]
    assert ex.ExperimentDefinition.model_validate_json(d.model_dump_json()) == d
    assert (d.allocation("control"), d.allocation("treatment")) == (50, 50)
    with pytest.raises(ValidationError):
        d.version = 2  # type: ignore[misc]


@pytest.mark.parametrize(("control", "treatment"), [(50, 50), (90, 10), (1, 99), (99, 1)])
def test_allocations_are_positive_and_total_100(control, treatment):
    arms = design()["arms"]
    arms = [
        {**arms[0], "allocation_percent": control},
        {**arms[1], "allocation_percent": treatment},
    ]
    assert definition(arms=arms).allocation("treatment") == treatment


@pytest.mark.parametrize(
    "allocations", [(50, 49), (50, 51), (0, 100), (100, 0), (-10, 110), (50.5, 49.5)]
)
def test_allocations_that_do_not_total_100_with_positive_parts_are_refused(allocations):
    arms = design()["arms"]
    arms = [
        {**arm, "allocation_percent": share} for arm, share in zip(arms, allocations, strict=True)
    ]
    with pytest.raises(ValidationError):
        ex.CreateExperimentRequest.model_validate(design(arms=arms))


def _arm(name: str, share: int = 50) -> dict:
    return {"arm": name, "allocation_percent": share, "description": "Test arm."}


@pytest.mark.parametrize(
    "arms",
    [
        [_arm("control"), _arm("control")],
        [_arm("treatment"), _arm("treatment")],
        [_arm("treatment"), _arm("control")],
        [_arm("control"), _arm("placebo")],
        [_arm("control", 40), _arm("treatment", 30), _arm("treatment", 30)],
        [_arm("control", 100)],
        [],
    ],
)
def test_exactly_control_then_treatment(arms):
    with pytest.raises(ValidationError):
        ex.CreateExperimentRequest.model_validate(design(arms=arms))


@pytest.mark.parametrize(
    "changes",
    [
        {"primary_metric": "nrr"},
        {"primary_metric": "grr"},
        {"randomization_unit": "user"},
        {"randomization_unit": "cluster"},
        {"analysis_principle": "per_protocol"},
        {"enrollment_end": ENROLL_START - timedelta(days=1)},
        {"enrollment_end": ENROLL_START + timedelta(days=366)},  # 367 days of enrollment
        {"enrollment_start": "2026-01-01T00:00:00"},
        {"outcome_window_days": 6},
        {"outcome_window_days": 367},
        {"outcome_window_days": 0},
        {"name": "   "},
        {"name": "x" * 121},
        {"hypothesis": "Ask jane.doe@example.test"},
        {"objective": "nul\x00byte"},
        {"bandit": True},
    ],
)
def test_an_invalid_design_is_refused(changes):
    with pytest.raises(ValidationError):
        ex.CreateExperimentRequest.model_validate(design(**changes))


def test_the_bounds_themselves_are_valid():
    for days in (ex.MIN_OUTCOME_WINDOW_DAYS, ex.MAX_OUTCOME_WINDOW_DAYS):
        ex.CreateExperimentRequest.model_validate(design(outcome_window_days=days))
    longest = ENROLL_START + timedelta(days=ex.MAX_ENROLLMENT_DAYS - 1)
    ex.CreateExperimentRequest.model_validate(design(enrollment_end=longest))
    ex.CreateExperimentRequest.model_validate(design(enrollment_end=ENROLL_START))


def test_a_definition_is_declared_before_enrollment_and_time_zoned():
    late = {"by": MEMBER, "at": datetime(2026, 1, 2, tzinfo=UTC)}
    with pytest.raises(ValidationError, match="no later than its enrollment start"):
        definition().model_validate({**definition().model_dump(), "created": late})
    naive = {"by": MEMBER, "at": datetime(2025, 12, 15, 9)}
    with pytest.raises(ValidationError):
        ex.ExperimentDefinition.model_validate({**definition().model_dump(), "created": naive})
    for version in (0, -1):
        with pytest.raises(ValidationError):
            ex.ExperimentDefinition.model_validate(
                {**definition().model_dump(), "version": version}
            )


EXPERIMENT_REQUESTS = {
    ex.CreateExperimentRequest: design(),
    ex.EnrollAccountRequest: {"account_id": "TEST-ACC-000001"},
    ex.ExperimentAnalysisQuery: {"as_of": AS_OF},
}


@pytest.mark.parametrize("model", list(EXPERIMENT_REQUESTS), ids=lambda m: m.__name__)
@pytest.mark.parametrize("field", sorted(oc.SERVER_DERIVED_FIELDS))
def test_no_experiment_request_takes_a_server_field(model, field):
    model.model_validate(EXPERIMENT_REQUESTS[model])  # the control
    assert not set(model.model_fields) & oc.SERVER_DERIVED_FIELDS
    with pytest.raises(ValidationError) as refused:
        model.model_validate({**EXPERIMENT_REQUESTS[model], field: "1"})
    assert [e["type"] for e in refused.value.errors()] == ["extra_forbidden"]


def test_a_client_never_chooses_the_arm():
    assert set(ex.EnrollAccountRequest.model_fields) == {"account_id"}
    with pytest.raises(ValidationError):
        ex.EnrollAccountRequest.model_validate({"account_id": "TEST-ACC-1", "arm": "treatment"})


# --- assignments -------------------------------------------------------------------------------


def test_a_valid_assignment_round_trips():
    a = assignment("TEST-ACC-000001", "treatment")
    assert a.assigned_on == date(2026, 1, 5)
    assert ex.ExperimentAssignment.model_validate_json(a.model_dump_json()) == a
    ea.check_assignments(definition(), [a])


@pytest.mark.parametrize(
    "changes",
    [
        {"arm": "placebo"},
        {"arm": "Treatment"},
        {"assigned_at": datetime(2026, 1, 5, 10)},
        {"assigned_at": "2026-01-05T10:00:00"},
        {"assigned_at": "not a time"},
        {"assignment_method": "manual_choice"},
        {"experiment_version": 0},
        {"account_id": "someone@example.test"},
    ],
)
def test_an_invalid_assignment_is_refused(changes):
    with pytest.raises(ValidationError):
        changes = dict(changes)
        assignment("TEST-ACC-000001", changes.pop("arm", "control"), **changes)


def test_assignments_are_checked_against_the_exact_version_and_enrollment():
    d = definition()
    bad = [
        assignment("A", "control", experiment_version=2),
        assignment("B", "control", experiment_id=UUID(int=5)),
        assignment("C", "control", on=date(2025, 12, 31)),
        assignment("D", "control", on=date(2026, 2, 1)),
        # 23:30 at UTC-5 on 31 January is 1 February in UTC: outside enrollment.
        assignment(
            "E",
            "control",
            assigned_at=datetime(2026, 1, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
        ),
    ]
    with pytest.raises(SchemaValidationError) as refused:
        ea.check_assignments(d, bad)
    assert refused.value.problems == [
        "assignment 1 is for version 2, not 1",
        "assignment 2 is for another experiment",
        "assignment 3 is dated outside the enrollment period",
        "assignment 4 is dated outside the enrollment period",
        "assignment 5 is dated outside the enrollment period",
    ]
    ea.check_assignments(d, [assignment("F", "control", on=ENROLL_START)])
    ea.check_assignments(d, [assignment("G", "control", on=ENROLL_END)])


def test_one_assignment_per_account_and_per_id():
    first = assignment("A", "control")
    with pytest.raises(SchemaValidationError) as refused:
        ea.check_assignments(
            definition(),
            [
                first,
                assignment("A", "treatment"),
                first.model_copy(
                    update={
                        "account_id": "B",
                        "eligibility": first.eligibility.model_copy(update={"account_id": "B"}),
                    }
                ),
            ],
        )
    assert refused.value.problems == [
        "assignment 2 repeats the account of assignment 1",
        "assignment 3 repeats the ID of assignment 1",
    ]


# --- eligibility, settled before assignment ----------------------------------------------------


def snapshot(account: str = "TEST-ACC-000001", mrr: str = "100.00", **changes) -> Snapshot:
    """A trusted M2 account snapshot (test data)."""
    fields = {
        "account_external_id": account,
        "as_of_date": date(2026, 1, 4),
        "status": "active",
        "mrr": Decimal(mrr),
        "currency": "USD",
        **changes,
    }
    return Snapshot(**fields)


def test_a_paying_snapshot_gives_eligibility_evidence():
    found = ex.eligibility_evidence(snapshot(mrr="250.50", currency="EUR"))
    assert found == ex.EligibilityEvidence(
        account_id="TEST-ACC-000001",
        snapshot_date=date(2026, 1, 4),
        baseline_mrr=Decimal("250.50"),
        currency="EUR",
    )
    a = assignment("TEST-ACC-000001", "control", eligibility=found.model_dump())
    assert a.eligibility == found
    assert ex.ExperimentAssignment.model_validate_json(a.model_dump_json()) == a
    ea.check_assignments(definition(), [a])


@pytest.mark.parametrize("mrr", ["0", "0.00", "0.0000"])
def test_an_account_paying_nothing_at_the_baseline_is_not_eligible(mrr):
    with pytest.raises(ValidationError, match="baseline MRR must be above zero"):
        ex.eligibility_evidence(snapshot(mrr=mrr))
    with pytest.raises(ValidationError, match="baseline MRR must be above zero"):
        assignment("A", "control", eligibility=evidence("A", baseline_mrr=mrr))


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline_mrr": "-1.00"},
        {"baseline_mrr": -1},
        {"baseline_mrr": 100.0},  # a binary float is never money
        {"baseline_mrr": "1e3"},
        {"baseline_mrr": None},
        {"currency": "usd"},
        {"currency": "US"},
        {"currency": "EURO"},
        {"currency": None},
        {"snapshot_date": "2026/01/04"},
        {"snapshot_date": None},
        {"account_id": "someone@example.test"},
    ],
    ids=repr,
)
def test_invalid_eligibility_evidence_is_refused(changes):
    with pytest.raises(ValidationError):
        assignment("A", "control", eligibility=evidence("A", **changes))


@pytest.mark.parametrize("missing", ["account_id", "snapshot_date", "baseline_mrr", "currency"])
def test_incomplete_eligibility_evidence_is_refused(missing):
    body = evidence("A")
    del body[missing]
    with pytest.raises(ValidationError):
        assignment("A", "control", eligibility=body)


def test_an_assignment_without_eligibility_evidence_is_refused():
    body = assignment("A", "control").model_dump()
    del body["eligibility"]
    with pytest.raises(ValidationError) as refused:
        ex.ExperimentAssignment.model_validate(body)
    assert [e["loc"] for e in refused.value.errors()] == [("eligibility",)]
    with pytest.raises(ValidationError):
        ex.ExperimentAssignment.model_validate({**body, "eligibility": None})


def test_a_baseline_snapshot_after_the_assignment_is_refused():
    with pytest.raises(ValidationError, match="baseline snapshot is dated after the assignment"):
        assignment("A", "control", eligibility=evidence("A", snapshot_date=date(2026, 1, 6)))
    # 01:00 at UTC+5 on 5 January is 4 January in UTC: a snapshot of the 5th is after it.
    with pytest.raises(ValidationError, match="dated after the assignment"):
        assignment(
            "A",
            "control",
            assigned_at=datetime(2026, 1, 5, 1, tzinfo=timezone(timedelta(hours=5))),
            eligibility=evidence("A", snapshot_date=date(2026, 1, 5)),
        )
    same_day = assignment("A", "control", eligibility=evidence("A", snapshot_date=date(2026, 1, 5)))
    assert same_day.eligibility.snapshot_date == same_day.assigned_on


def test_evidence_of_another_account_is_refused():
    with pytest.raises(ValidationError, match="another account's"):
        assignment("A", "control", eligibility=evidence("B"))


@pytest.mark.parametrize("field", ["eligibility", "baseline_mrr", "snapshot_date"])
def test_a_client_cannot_state_eligibility_when_enrolling(field):
    assert field in oc.SERVER_DERIVED_FIELDS
    value = evidence("TEST-ACC-000001") if field == "eligibility" else "100.00"
    with pytest.raises(ValidationError) as refused:
        ex.EnrollAccountRequest.model_validate({"account_id": "TEST-ACC-000001", field: value})
    assert [e["type"] for e in refused.value.errors()] == ["extra_forbidden"]


def test_an_ineligible_legacy_assignment_is_a_protocol_violation():
    """An assignment built without its contract's checks still cannot enter an analysis."""
    good = assignment("A", "control")
    zero = good.eligibility.model_construct(**{**good.eligibility.__dict__, "baseline_mrr": 0})
    legacy = [
        good.model_copy(update={"eligibility": zero}),
        good.model_copy(
            update={"eligibility": None, "assignment_id": UUID(int=77), "account_id": "B"}
        ),
    ]
    for position, bad in enumerate(legacy, start=1):
        with pytest.raises(SchemaValidationError) as refused:
            ea.analyze(definition(), [bad], [result(bad.account_id)], AS_OF)
        assert refused.value.problems == [
            "assignment 1 is not eligible: its baseline evidence does not show the account "
            "paying, as the same account, by the assignment date"
        ], position


# --- links -------------------------------------------------------------------------------------


def test_an_assignment_links_to_its_own_accounts_window():
    d, a = definition(), assignment("A", "treatment")
    record = result("A")
    link = ea.link_outcome(d, a, record)
    assert (link.assignment_id, link.outcome_id, link.arm, link.experiment_version) == (
        a.assignment_id,
        record.outcome_id,
        "treatment",
        1,
    )
    assert ea.outcome_window(d, a) == (date(2026, 1, 5), date(2026, 4, 5))


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (lambda: result("B"), "account_mismatch"),
        (lambda: result("A", start=date(2026, 1, 6)), "window_mismatch"),
        (lambda: result("A", window_end=date(2026, 4, 6)), "window_mismatch"),
    ],
)
def test_a_link_to_another_account_or_window_is_refused(record, code):
    with pytest.raises(ActionRefused) as refused:
        ea.link_outcome(definition(), assignment("A", "control"), record())
    assert refused.value.code == code


@pytest.mark.parametrize(
    ("changes", "code", "words"),
    [
        ({"starting_mrr": "100.01", "ending_mrr": "100.01"}, "baseline_mismatch", "100.01"),
        (
            {"starting_mrr": "90.00", "ending_mrr": "0", "status": "churned"},
            "baseline_mismatch",
            "90",
        ),
        ({"currency": "EUR"}, "currency_mismatch", "EUR"),
        ({"currency": "EUR", "starting_mrr": "5.00"}, "currency_mismatch", "USD"),
        (
            {"status": "unknown", "ending_mrr": None, "starting_mrr": "1.00"},
            "baseline_mismatch",
            "1.00",
        ),
    ],
)
def test_an_outcome_outside_the_baseline_cohort_is_refused(changes, code, words):
    d, a = definition(), assignment("A", "control")
    with pytest.raises(ActionRefused) as refused:
        ea.link_outcome(d, a, result("A", **changes))
    assert refused.value.code == code and words in str(refused.value)
    with pytest.raises(SchemaValidationError) as again:
        ea.analyze(d, [assignment("B", "treatment"), a], [result("A", **changes)], AS_OF)
    assert again.value.problems == [f"assignment 2: {refused.value.problems[0]}"]


def test_the_same_amount_written_differently_is_the_same_baseline():
    d, a = definition(), assignment("A", "control")
    assert (
        ea.link_outcome(d, a, result("A", starting_mrr="100.0", ending_mrr="100")).arm == "control"
    )


def test_an_ineligible_account_cannot_inflate_logo_retention():
    """An account that paid nothing at the baseline and started paying during the window
    would read as retained. It cannot be assigned, and its outcome cannot be attached to
    another account's paying baseline."""
    with pytest.raises(ValidationError):
        ex.eligibility_evidence(snapshot("NEW", mrr="0"))
    with pytest.raises(ValidationError):
        assignment("NEW", "treatment", eligibility=evidence("NEW", baseline_mrr="0"))
    acquired = {"starting_mrr": "0", "ending_mrr": "80.00", "status": "retained"}
    with pytest.raises(ActionRefused) as refused:
        ea.link_outcome(definition(), assignment("NEW", "treatment"), result("NEW", **acquired))
    assert refused.value.code == "baseline_mismatch"
    assignments = [assignment("C", "control"), assignment("NEW", "treatment")]
    with pytest.raises(SchemaValidationError):
        ea.analyze(definition(), assignments, [result("C"), result("NEW", **acquired)], AS_OF)


def test_a_link_to_another_version_is_refused():
    with pytest.raises(SchemaValidationError):
        ea.link_outcome(definition(), assignment("A", "control", experiment_version=2), result("A"))


# --- the analysis ------------------------------------------------------------------------------


def scenario():
    """Test data, as of 2026-04-20; windows of 90 days, so assignments on 5 January end on
    5 April (matured) and the one on 25 January ends on 25 April (immature).

    Control (5): c1 retained, c2 churned, c3 unknown, c4 no record (missing), c5 churned.
      Matured population 5.
      ITT: incomplete (1 missing, 1 unknown) -> no rate.
      Observed diagnostic: 1 retained / 3 observed = 33.3; coverage 3 / 5 = 60.0.
    Treatment (6): t1, t2 retained, t3 churned, t4 immature, t5 observed after as_of (missing),
      t6 recorded for a different window (missing). Matured population 5.
      ITT: incomplete (2 missing) -> no rate.
      Observed diagnostic: 2 / 3 = 66.6; coverage 3 / 5 = 60.0.
    Observed difference: 2/3 - 1/3 = 33.3 points. Primary ITT difference: unavailable.
    Records not used: t6's other window, and an account never assigned.
    """
    assignments = [
        assignment("c1", "control"),
        assignment("c2", "control"),
        assignment("c3", "control"),
        assignment("c4", "control"),
        assignment("c5", "control"),
        assignment("t1", "treatment"),
        assignment("t2", "treatment"),
        assignment("t3", "treatment"),
        assignment("t4", "treatment", on=date(2026, 1, 25)),
        assignment("t5", "treatment"),
        assignment("t6", "treatment"),
    ]
    outcomes = [
        result("c1"),
        result("c2", status="churned", ending_mrr="0"),
        result("c3", status="unknown", ending_mrr=None),
        result("c5", status="churned", ending_mrr="0"),
        result("t1", ending_mrr="150.00"),
        result("t2"),
        result("t3", status="churned", ending_mrr="0"),
        result("t5", observed_on=date(2026, 4, 25)),
        result("t6", start=date(2026, 1, 6)),
        result("stranger"),
    ]
    return assignments, outcomes


def complete():
    """Test data with complete outcomes. Control: 3 retained, 1 churned -> ITT 3/4 = 75.0.
    Treatment: 2 retained, 1 churned -> ITT 2/3 = 66.6, plus one immature assignment, which
    is not in the matured population. Difference: 2/3 - 3/4 = -1/12 = -8.33 -> -8.3."""
    assignments = [
        *(assignment(f"c{i}", "control") for i in range(4)),
        *(assignment(f"t{i}", "treatment") for i in range(3)),
        assignment("t-late", "treatment", on=date(2026, 1, 25)),
    ]
    outcomes = [
        result("c0"),
        result("c1"),
        result("c2"),
        result("c3", status="churned", ending_mrr="0"),
        result("t0"),
        result("t1"),
        result("t2", status="churned", ending_mrr="0"),
    ]
    return assignments, outcomes


def test_complete_outcomes_give_itt_rates_and_the_primary_difference():
    analysis = ea.analyze(definition(), *complete(), AS_OF)
    c, t = analysis.control, analysis.treatment
    assert (c.assigned, c.immature, c.matured) == (4, 0, 4)
    assert (t.assigned, t.immature, t.matured) == (4, 1, 3)
    assert (c.itt_retention_rate.population, c.itt_retention_rate.percent) == (4, Decimal("75.0"))
    assert (t.itt_retention_rate.population, t.itt_retention_rate.percent) == (3, Decimal("66.6"))
    for arm in (c, t):
        assert arm.itt_retention_rate.unavailable is None
        assert arm.itt_retention_rate.unavailable_reason is None
    primary = analysis.itt_retention_rate_difference
    assert primary.basis == "intention_to_treat_complete_data"
    assert primary.treatment_minus_control_pp == Decimal("-8.3")
    # With complete data the diagnostic agrees with the ITT rate.
    assert c.observed_outcome_diagnostic.observed_retention_rate.percent == Decimal("75.0")
    assert analysis.observed_outcome_difference.treatment_minus_control_pp == Decimal("-8.3")
    assert analysis.observed_outcome_difference.basis == "observed_outcomes_only"
    codes = [lim.code for lim in analysis.limitations]
    assert codes == ["descriptive_only", "immature_assignments"]


@pytest.mark.parametrize(
    "gap",
    [
        {"drop": "t0"},  # no record: missing
        {"replace": ("t0", {"status": "unknown", "ending_mrr": None})},
        {"replace": ("t0", {"observed_on": date(2026, 4, 25)})},  # observed after as_of
    ],
    ids=["missing", "unknown", "not-yet-observed"],
)
def test_an_incomplete_arm_has_no_itt_rate_and_there_is_no_primary_difference(gap):
    assignments, outcomes = complete()
    if "drop" in gap:
        outcomes = [o for o in outcomes if o.account_id != gap["drop"]]
    else:
        account, changes = gap["replace"]
        outcomes = [o for o in outcomes if o.account_id != account] + [result(account, **changes)]
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    c, t = analysis.control, analysis.treatment
    assert c.itt_retention_rate.percent == Decimal("75.0")  # the complete arm keeps its rate
    itt = t.itt_retention_rate
    assert itt.percent is None and itt.unavailable == "incomplete_outcome_data"
    assert itt.population == 3 == t.matured  # every matured assigned account is still counted
    assert (itt.retained, itt.churned, itt.unknown + itt.missing) == (1, 1, 1)
    assert "No outcome is imputed" in itt.unavailable_reason
    primary = analysis.itt_retention_rate_difference
    assert primary.treatment_minus_control_pp is None
    assert "treatment: incomplete_outcome_data" in primary.unavailable_reason
    # The diagnostic is still computed, over the observed outcomes only: 1 / 2 = 50.0.
    diagnostic = t.observed_outcome_diagnostic
    assert (diagnostic.retained, diagnostic.observed) == (1, 2)
    assert diagnostic.observed_retention_rate.percent == Decimal("50.0")
    assert diagnostic.outcome_coverage.percent == Decimal("66.6")
    assert analysis.observed_outcome_difference.treatment_minus_control_pp == Decimal("-25.0")
    codes = {lim.code for lim in analysis.limitations}
    assert {"incomplete_outcome_data", "itt_comparison_unavailable"} <= codes


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_incomplete_data_in_either_arm_removes_the_primary_difference(arm):
    assignments, outcomes = complete()
    victim = "c0" if arm == "control" else "t0"
    outcomes = [o for o in outcomes if o.account_id != victim]
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    assert analysis.itt_retention_rate_difference.treatment_minus_control_pp is None
    assert f"{arm}: incomplete_outcome_data" in (
        analysis.itt_retention_rate_difference.unavailable_reason
    )
    assert analysis.observed_outcome_difference.treatment_minus_control_pp is not None


def test_each_arm_is_counted_as_assigned_by_hand():
    assignments, outcomes = scenario()
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    c, t = analysis.control, analysis.treatment
    assert (c.assigned, c.immature, c.matured) == (5, 0, 5)
    assert (c.retained, c.churned, c.unknown, c.missing) == (1, 2, 1, 1)
    assert (t.assigned, t.immature, t.matured) == (6, 1, 5)
    assert (t.retained, t.churned, t.unknown, t.missing) == (2, 1, 0, 2)
    for arm in (c, t):
        assert arm.itt_retention_rate.population == arm.matured == 5
        assert arm.itt_retention_rate.percent is None
    cd, td = c.observed_outcome_diagnostic, t.observed_outcome_diagnostic
    assert (cd.retained, cd.observed, cd.unknown, cd.missing) == (1, 3, 1, 1)
    assert (cd.observed_retention_rate.percent, cd.outcome_coverage.percent) == (
        Decimal("33.3"),
        Decimal("60.0"),
    )
    assert (td.retained, td.observed, td.unknown, td.missing) == (2, 3, 0, 2)
    assert (td.observed_retention_rate.percent, td.outcome_coverage.percent) == (
        Decimal("66.6"),
        Decimal("60.0"),
    )
    assert (c.allocation_percent, t.allocation_percent) == (50, 50)
    assert analysis.itt_retention_rate_difference.treatment_minus_control_pp is None
    assert analysis.observed_outcome_difference.treatment_minus_control_pp == Decimal("33.3")
    assert analysis.outcomes_not_used == 2


def test_the_analysis_lists_every_limitation():
    assignments, outcomes = scenario()
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    codes = {lim.code: lim.message for lim in analysis.limitations}
    assert list(codes) == [
        "descriptive_only",
        "immature_assignments",
        "missing_outcomes",
        "unknown_outcomes",
        "incomplete_outcome_data",
        "itt_comparison_unavailable",
        "outcomes_not_used",
    ]
    assert "1 control, 2 treatment" in codes["missing_outcomes"]
    assert "1 treatment" in codes["immature_assignments"]
    assert "not the ITT estimate" in codes["incomplete_outcome_data"]


def test_a_negative_difference_is_cut_toward_zero():
    assignments, outcomes = scenario()
    swapped = [
        a.model_copy(update={"arm": "treatment" if a.arm == "control" else "control"})
        for a in assignments
    ]
    analysis = ea.analyze(definition(), swapped, outcomes, AS_OF)
    assert analysis.observed_outcome_difference.treatment_minus_control_pp == Decimal("-33.3")


def test_missing_data_is_never_imputed():
    """Two retained and three with no record. Imputing churn would give 40%, imputing
    retention 100%: the ITT rate is neither, it is unavailable; the diagnostic says 2 of 2
    observed, with 40% coverage."""
    assignments = [assignment(f"m{i}", "control") for i in range(5)]
    c = ea.analyze(definition(), assignments, [result("m0"), result("m1")], AS_OF).control
    assert (c.retained, c.churned, c.missing) == (2, 0, 3)
    assert c.itt_retention_rate.percent is None
    assert c.itt_retention_rate.population == 5
    assert c.observed_outcome_diagnostic.observed_retention_rate.percent == Decimal("100.0")
    assert c.observed_outcome_diagnostic.outcome_coverage.percent == Decimal("40.0")


def test_an_arm_without_observed_outcomes_gives_no_rate_and_no_comparison():
    assignments = [assignment("a", "control"), assignment("b", "treatment")]
    analysis = ea.analyze(definition(), assignments, [result("a")], AS_OF)
    t = analysis.treatment
    assert t.itt_retention_rate.unavailable == "incomplete_outcome_data"
    rate = t.observed_outcome_diagnostic.observed_retention_rate
    assert rate.percent is None and "observed retained or churned" in rate.unavailable_reason
    assert analysis.itt_retention_rate_difference.treatment_minus_control_pp is None
    observed = analysis.observed_outcome_difference
    assert observed.treatment_minus_control_pp is None
    assert "treatment has none" in observed.unavailable_reason
    assert "itt_comparison_unavailable" in {lim.code for lim in analysis.limitations}


def test_an_experiment_with_nothing_matured_or_assigned_says_so():
    empty = ea.analyze(definition(), [], [], AS_OF)
    assert (empty.control.assigned, empty.treatment.assigned) == (0, 0)
    assert empty.control.itt_retention_rate.unavailable == "no_matured_assignments"
    diagnostic = empty.control.observed_outcome_diagnostic
    assert "matured outcome window" in diagnostic.outcome_coverage.unavailable_reason
    assert empty.itt_retention_rate_difference.treatment_minus_control_pp is None
    early = ea.analyze(
        definition(),
        [assignment("a", "control"), assignment("b", "treatment")],
        [result("a"), result("b")],
        date(2026, 2, 1),
    )
    assert early.control.immature == 1 and early.control.matured == 0
    assert early.control.itt_retention_rate.population == 0
    assert early.control.itt_retention_rate.unavailable == "no_matured_assignments"


def test_immature_assignments_are_outside_the_matured_population():
    """The same accounts, as of two dates: t-late joins treatment's population once its
    window has ended."""
    assignments, outcomes = complete()
    outcomes = [*outcomes, result("t-late", start=date(2026, 1, 25))]
    before = ea.analyze(definition(), assignments, outcomes, AS_OF).treatment
    after = ea.analyze(definition(), assignments, outcomes, date(2026, 5, 31)).treatment
    assert (before.immature, before.itt_retention_rate.population) == (1, 3)
    assert (after.immature, after.itt_retention_rate.population) == (0, 4)
    assert after.itt_retention_rate.percent == Decimal("75.0")  # 3 / 4


def test_every_matured_assignment_stays_in_its_assigned_arm():
    """Arms follow assignments only: outcomes, missing data and starting MRR move no account."""
    assignments, outcomes = scenario()
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    for summary in (analysis.control, analysis.treatment):
        mine = [a for a in assignments if a.arm == summary.arm]
        assert summary.assigned == len(mine)
        assert summary.itt_retention_rate.population == summary.matured
        assert summary.matured == summary.retained + summary.churned + summary.unknown + (
            summary.missing
        )
    params = list(inspect.signature(ea.analyze).parameters)
    assert params == ["definition", "assignments", "outcomes", "as_of"]  # no exposure, no tasks


def test_an_itt_rate_cannot_be_given_for_incomplete_data():
    body = {
        "population": 3,
        "retained": 2,
        "churned": 0,
        "unknown": 0,
        "missing": 1,
        "percent": Decimal("66.6"),
        "unavailable": None,
        "unavailable_reason": None,
    }
    with pytest.raises(ValidationError):
        ex.IttRetentionRate.model_validate(body)
    with pytest.raises(ValidationError):  # nor a population short of the matured accounts
        ex.IttRetentionRate.model_validate(
            {
                **body,
                "population": 2,
                "percent": None,
                "unavailable": "incomplete_outcome_data",
                "unavailable_reason": "x",
            }
        )
    ex.IttRetentionRate.model_validate(
        {
            **body,
            "percent": None,
            "unavailable": "incomplete_outcome_data",
            "unavailable_reason": "x",
        }
    )


def test_the_diagnostic_cannot_take_the_primary_place():
    analysis = ea.analyze(definition(), *complete(), AS_OF)
    body = analysis.model_dump()
    primary, diagnostic = body["itt_retention_rate_difference"], body["observed_outcome_difference"]
    with pytest.raises(ValidationError):
        ex.ExperimentAnalysis.model_validate(
            {
                **body,
                "itt_retention_rate_difference": diagnostic,
                "observed_outcome_difference": primary,
            }
        )


def test_assignments_after_as_of_or_duplicate_outcomes_are_refused():
    assignments, outcomes = scenario()
    with pytest.raises(SchemaValidationError, match="after as_of"):
        ea.analyze(definition(), assignments, outcomes, date(2026, 1, 20))
    with pytest.raises(SchemaValidationError):
        ea.analyze(definition(), assignments, [*outcomes, result("c1")], AS_OF)
    with pytest.raises(SchemaValidationError):
        ea.analyze(definition(), [*assignments, assignment("c1", "treatment")], outcomes, AS_OF)


def test_the_analysis_is_deterministic_and_leaves_its_input_alone():
    assignments, outcomes = scenario()
    before = [a.model_dump_json() for a in assignments]
    first = ea.analyze(definition(), assignments, outcomes, AS_OF)
    a2, o2 = assignments[:], outcomes[:]
    random.Random(11).shuffle(a2)
    random.Random(12).shuffle(o2)
    assert ea.analyze(definition(), a2, o2, AS_OF).model_dump_json() == first.model_dump_json()
    assert [a.model_dump_json() for a in assignments] == before


# --- it says what it is, and only that ---------------------------------------------------------

FORBIDDEN = (
    "p_value",
    "pvalue",
    "confidence",
    "interval",
    "significan",
    "winner",
    "best",
    "lift",
    "effect",
    "impact",
    "uplift",
    "revenue",
    "saved",
    "recover",
    "prevent",
    "attribut",
    "probability",
)


def test_the_analysis_is_always_descriptive():
    assignments, outcomes = scenario()
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    assert (
        analysis.analysis_type,
        analysis.causal_interpretation,
        analysis.statistical_inference,
    ) == ("descriptive", "not_established", "not_performed")
    assert analysis.statement == ex.DESCRIPTIVE_STATEMENT
    assert analysis.limitations[0].code == "descriptive_only"
    body = analysis.model_dump()
    for update in (
        {"analysis_type": "causal"},
        {"causal_interpretation": "established"},
        {"statistical_inference": "t_test"},
        {"statement": "Treatment wins."},
        {"winner": "treatment"},
        {"p_value": 0.01},
    ):
        with pytest.raises(ValidationError):
            ex.ExperimentAnalysis.model_validate({**body, **update})


def test_no_field_can_carry_a_significance_winner_or_revenue_claim():
    for model in (ex.ExperimentAnalysis, ex.ExperimentDefinition, ex.ExperimentOutcome):
        names = _schema_names(model.model_json_schema())
        for word in FORBIDDEN:
            assert not [n for n in names if word in n.lower()], (model.__name__, word)


def test_no_text_the_analysis_returns_claims_a_cause():
    assignments, outcomes = scenario()
    analysis = ea.analyze(definition(), assignments, outcomes, AS_OF)
    assert not _offending(analysis.model_dump_json())
    assert not _offending(json.dumps(ex.ExperimentAnalysis.model_json_schema()))
    text = analysis.statement.lower()
    assert "does not establish a causal effect" in text and "no statistical test" in text


@pytest.mark.parametrize(
    "model",
    [
        ex.CreateExperimentRequest,
        ex.ExperimentDefinition,
        ex.ExperimentList,
        ex.EnrollAccountRequest,
        ex.ExperimentAssignment,
        ex.ExperimentOutcome,
        ex.ExperimentAnalysisQuery,
        ex.ExperimentAnalysis,
    ],
    ids=lambda m: m.__name__,
)
def test_each_experiment_contract_is_closed(model):
    assert model.model_json_schema()["additionalProperties"] is False
