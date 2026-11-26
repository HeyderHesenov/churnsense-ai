"""The Retention Action Center's foundation (M3): playbooks, task contracts and the lifecycle.

Nothing here is served or stored: these tests hold the catalog to M2's exact
indicator codes, the request contracts to the trust boundary (no organization,
actor or evidence from a client), the response contracts to the lifecycle, and
the lifecycle to its table - every one of the 36 status pairs. Every snapshot is
hand-built and synthetic (``tests/workspace_snapshots.py``).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import typing
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import action_lifecycle as lifecycle
from churnsense.workspace import action_playbooks as playbooks
from churnsense.workspace import action_schemas as contracts
from churnsense.workspace import risk, workbench
from churnsense.workspace.action_lifecycle import ActionRefused
from tests.test_wording import _offending
from tests.workspace_snapshots import AS_OF, earlier, snap

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "src" / "churnsense" / "workspace"
M3_MODULES = ("action_lifecycle.py", "action_playbooks.py", "action_schemas.py")
#: M4's contract-only modules, which build on M3's task contract and are served by nothing
#: either (``tests/test_workspace_retention_impact.py`` holds them to that).
CONTRACT_ONLY_IMPORTERS = (
    "outcome_schemas.py",
    "retention_metrics.py",
    "experiment_schemas.py",
    "experiment_analysis.py",
)

#: The mapping, written out independently of the catalog: a moved trigger fails here.
EXPECTED_MAPPING = {
    "payment_past_due": "payment_resolution",
    "renewal_due": "renewal_readiness",
    "inactivity": "customer_reengagement",
    "sessions_decline": "product_usage_investigation",
    "key_actions_decline": "product_usage_investigation",
    "low_active_user_coverage": "adoption_coverage_improvement",
}
#: The lifecycle, written out independently of ``TRANSITIONS``.
EXPECTED_TRANSITIONS = {
    "proposed": {"approved", "dismissed", "cancelled"},
    "approved": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "dismissed": set(),
    "cancelled": set(),
}
ACCOUNT = "DEMO-ACC-900001"
MEMBER = UUID("6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f")
OTHER_MEMBER = UUID("0a1b2c3d-4e5f-4a6b-9c7d-8e9f0a1b2c3d")
TASK = UUID("11111111-2222-4333-8444-555555555555")
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _assess(**changes) -> risk.Assessment:
    current = snap(**changes)
    return risk.assess(current, [earlier(current, 30)])


def _declining() -> risk.Assessment:
    """Both usage counts fell by half against the preceding period: two related indicators."""
    current = snap(sessions_30d=50, key_actions_30d=200)
    return risk.assess(current, [earlier(current, 30, sessions_30d=100, key_actions_30d=400)])


def _ids(matches) -> list[str]:
    return [m.playbook.playbook_id for m in matches]


# --- the catalog covers M2's indicators exactly ------------------------------------------------


def test_every_m2_indicator_has_exactly_one_playbook():
    assert set(typing.get_args(risk.IndicatorCode)) == set(risk.RULE_ORDER)
    assert {code: p.playbook_id for code, p in playbooks.BY_TRIGGER.items()} == EXPECTED_MAPPING
    triggers = [code for p in playbooks.CATALOG for code in p.triggers]
    assert sorted(triggers) == sorted(risk.RULE_ORDER)  # each once, no other


def test_the_catalog_is_the_five_playbooks_in_a_fixed_order():
    ids = tuple(p.playbook_id for p in playbooks.CATALOG)
    assert ids == typing.get_args(playbooks.PlaybookId)
    assert ids == (
        "payment_resolution",
        "renewal_readiness",
        "customer_reengagement",
        "product_usage_investigation",
        "adoption_coverage_improvement",
    )
    assert set(playbooks.BY_ID) == set(ids)
    assert playbooks.INDICATOR_RULES_VERSION == risk.METHOD_VERSION == "workbench_rules.v1"
    assert playbooks.CATALOG_VERSION == "action_playbooks.v1"


def test_every_playbook_is_manual_needs_approval_and_has_ordered_steps():
    for p in playbooks.CATALOG:
        assert p.version == 1
        assert p.execution == "manual_only"
        assert p.requires_human_approval is True
        assert isinstance(p.steps, tuple) and len(p.steps) >= 3
        assert p.steps[-1].title == "Record the outcome"
    out = contracts.catalog_out()
    assert [p.execution for p in out.playbooks] == ["manual_only"] * 5
    assert all(p.requires_human_approval is True for p in out.playbooks)
    assert all(
        [s.position for s in p.steps] == list(range(1, len(p.steps) + 1)) for p in out.playbooks
    )


@pytest.mark.parametrize(
    "change",
    [
        {"execution": "automatic"},
        {"requires_human_approval": False},
        {"version": 0},
        {"triggers": ()},
        {"triggers": ("inactivity", "inactivity")},
        {"steps": ()},
        {"steps": [playbooks.Step("a", "b")]},
    ],
)
def test_a_playbook_that_is_not_manual_approved_and_complete_cannot_be_built(change):
    with pytest.raises(ValueError):
        dataclasses.replace(playbooks.BY_ID["customer_reengagement"], **change)


@pytest.mark.parametrize(
    "catalog",
    [
        playbooks.CATALOG[:-1],  # low_active_user_coverage uncovered
        (*playbooks.CATALOG, playbooks.CATALOG[0]),  # an ID twice
        (
            *playbooks.CATALOG,
            dataclasses.replace(
                playbooks.CATALOG[0], playbook_id="renewal_copy", triggers=("renewal_due",)
            ),
        ),
        (
            *playbooks.CATALOG[:-1],
            dataclasses.replace(playbooks.CATALOG[-1], triggers=("low_active_user_coverage", "x")),
        ),
    ],
    ids=["uncovered", "duplicate-id", "duplicate-trigger", "unknown-trigger"],
)
def test_a_catalog_that_does_not_cover_m2_exactly_is_refused(monkeypatch, catalog):
    monkeypatch.setattr(playbooks, "CATALOG", catalog)
    with pytest.raises(ValueError):
        playbooks._index()


def test_the_catalog_is_immutable():
    p = playbooks.BY_ID["payment_resolution"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.title = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.steps[0].detail = "Changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        playbooks.BY_ID["payment_resolution"] = p  # type: ignore[index]
    with pytest.raises(TypeError):
        playbooks.BY_TRIGGER["inactivity"] = p  # type: ignore[index]
    out = contracts.catalog_out()
    with pytest.raises(ValidationError):
        out.catalog_version = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        out.playbooks[0].title = "Changed"  # type: ignore[misc]


def test_the_catalog_response_is_deterministic():
    first, second = contracts.catalog_out(), contracts.catalog_out()
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    body = json.loads(first.model_dump_json())
    assert body["catalog_version"] == "action_playbooks.v1"
    assert body["indicator_rules_version"] == "workbench_rules.v1"
    assert [p["triggers"] for p in body["playbooks"]] == [
        ["payment_past_due"],
        ["renewal_due"],
        ["inactivity"],
        ["sessions_decline", "key_actions_decline"],
        ["low_active_user_coverage"],
    ]
    assert contracts.PlaybookCatalog.model_validate_json(first.model_dump_json()) == first


def test_the_playbook_text_offers_nothing_and_claims_no_effect():
    out = contracts.catalog_out()
    texts = [
        *out.notes,
        *(t for p in out.playbooks for t in (p.title, p.description)),
        *(t for p in out.playbooks for s in p.steps for t in (s.title, s.detail)),
    ]
    for text in texts:
        assert not _offending(text), text
        for word in ("discount", "offer", "coupon", "refund", "credit", "email", "automatic"):
            assert word not in text.lower() or "run automatically" in text.lower(), (word, text)


# --- applicability reads M2's evidence and nothing else ----------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"status": "past_due"}, ["payment_resolution"]),
        ({"renewal_date": AS_OF + timedelta(days=10)}, ["renewal_readiness"]),
        ({"renewal_date": AS_OF + timedelta(days=45)}, ["renewal_readiness"]),  # watch
        ({"last_activity_at": AS_OF - timedelta(days=30)}, ["customer_reengagement"]),
        ({"active_users": 1, "seats_purchased": 100}, ["adoption_coverage_improvement"]),
    ],
)
def test_one_indicator_gets_its_playbook(changes, expected):
    matches = playbooks.applicable(_assess(**changes))
    assert _ids(matches) == expected
    (match,) = matches
    assert [i.code for i in match.indicators] == list(match.playbook.triggers)


def test_two_related_indicators_share_one_playbook():
    assessment = _declining()
    assert [i.code for i in assessment.indicators] == ["sessions_decline", "key_actions_decline"]
    (match,) = playbooks.applicable(assessment)
    assert match.playbook.playbook_id == "product_usage_investigation"
    assert match.indicators == assessment.indicators


def test_several_playbooks_follow_the_assessments_order():
    """Attention before watch, as M2 orders the indicators: the lead signal's playbook first."""
    assessment = _assess(
        status="past_due",
        renewal_date=AS_OF + timedelta(days=45),  # watch
        last_activity_at=AS_OF - timedelta(days=40),  # attention
    )
    assert [i.code for i in assessment.indicators] == [
        "payment_past_due",
        "inactivity",
        "renewal_due",
    ]
    assert _ids(playbooks.applicable(assessment)) == [
        "payment_resolution",
        "customer_reengagement",
        "renewal_readiness",
    ]


@pytest.mark.parametrize("status", ["trialing", "paused", "canceled"])
def test_an_account_not_assessed_gets_no_playbook(status):
    # Signals that would fire on an assessed account: still nothing.
    assessment = _assess(status=status, last_activity_at=AS_OF - timedelta(days=90))
    assert not assessment.assessed
    assert playbooks.applicable(assessment) == ()
    with pytest.raises(ActionRefused) as refused:
        playbooks.require_applicable(assessment, "customer_reengagement")
    assert refused.value.code == "account_not_assessed"


def test_an_account_without_signals_gets_no_playbook():
    assessment = _assess()
    assert assessment.level == "no_signals" and assessment.indicators == ()
    assert playbooks.applicable(assessment) == ()
    for playbook_id in playbooks.BY_ID:
        with pytest.raises(ActionRefused) as refused:
            playbooks.require_applicable(assessment, playbook_id)
        assert refused.value.code == "playbook_not_applicable"


def test_an_unknown_indicator_gets_no_playbook():
    fired = _assess(status="past_due")
    unknown = dataclasses.replace(fired.indicators[0], code="made_up_signal")
    assessment = dataclasses.replace(fired, indicators=(unknown,))
    assert playbooks.applicable(assessment) == ()
    mixed = dataclasses.replace(fired, indicators=(unknown, *fired.indicators))
    assert _ids(playbooks.applicable(mixed)) == ["payment_resolution"]


def test_applicability_reads_the_recorded_indicators_not_the_thresholds():
    """An assessment whose indicators no snapshot backs still decides alone: no rule is re-run."""
    recorded = _assess(active_users=1, seats_purchased=100)
    moved = dataclasses.replace(
        recorded, indicators=(dataclasses.replace(recorded.indicators[0], severity="watch"),)
    )
    assert _ids(playbooks.applicable(moved)) == ["adoption_coverage_improvement"]
    assert playbooks.applicable(moved)[0].indicators == moved.indicators


def test_an_assessment_by_other_rules_is_refused():
    assessment = dataclasses.replace(_assess(status="past_due"), method_version="other_rules.v9")
    with pytest.raises(ValueError, match="indicator rules"):
        playbooks.applicable(assessment)
    with pytest.raises(ValueError, match="indicator rules"):
        playbooks.require_applicable(assessment, "payment_resolution")


def test_require_applicable_names_why_a_playbook_does_not_apply():
    assessment = _assess(status="past_due")
    assert playbooks.require_applicable(assessment, "payment_resolution").playbook.version == 1
    with pytest.raises(ActionRefused) as refused:
        playbooks.require_applicable(assessment, "renewal_readiness")
    assert refused.value.code == "playbook_not_applicable"
    assert "renewal_due" in str(refused.value) and "2026-10-31" in str(refused.value)
    with pytest.raises(ActionRefused) as refused:
        playbooks.require_applicable(assessment, "no_such_playbook")
    assert refused.value.code == "unknown_playbook"


def test_every_signal_of_the_synthetic_book_meets_exactly_one_playbook():
    book = workbench.load_book()
    for entry in book.entries:
        a = entry.assessment
        matches = playbooks.applicable(a)
        covered = [i for m in matches for i in m.indicators]
        assert sorted(covered, key=a.indicators.index) == list(a.indicators)
        assert len(covered) == len(a.indicators)
        for m in matches:
            assert {i.code for i in m.indicators} <= set(m.playbook.triggers)
        if not a.assessed or a.level == "no_signals":
            assert matches == ()


# --- requests: only what a person chooses ------------------------------------------------------

REQUESTS = {
    contracts.CreateTaskRequest: {"account_id": ACCOUNT, "playbook_id": "payment_resolution"},
    contracts.AssignTaskRequest: {"assignee_id": str(MEMBER)},
    contracts.TransitionTaskRequest: {"to_status": "approved"},
    contracts.TaskListQuery: {},
}


@pytest.mark.parametrize("model", list(REQUESTS), ids=lambda m: m.__name__)
def test_no_request_has_a_server_derived_field(model):
    assert not set(model.model_fields) & contracts.SERVER_DERIVED_FIELDS
    assert "organization_id" in contracts.SERVER_DERIVED_FIELDS
    assert "actor_id" in contracts.SERVER_DERIVED_FIELDS
    assert model.model_config["extra"] == "forbid"


@pytest.mark.parametrize("model", list(REQUESTS), ids=lambda m: m.__name__)
@pytest.mark.parametrize("field", sorted(contracts.SERVER_DERIVED_FIELDS))
def test_a_client_cannot_inject_identity_or_evidence(model, field):
    model.model_validate(REQUESTS[model])  # the control
    value = {"level": "attention"} if field == "evidence" else str(OTHER_MEMBER)
    with pytest.raises(ValidationError) as refused:
        model.model_validate({**REQUESTS[model], field: value})
    assert [e["type"] for e in refused.value.errors()] == ["extra_forbidden"]


def test_a_create_request_round_trips():
    request = contracts.CreateTaskRequest(
        account_id=ACCOUNT,
        playbook_id="renewal_readiness",
        due_date="2026-11-30",
        assignee_id=str(MEMBER),
    )
    assert request.due_date == date(2026, 11, 30) and request.assignee_id == MEMBER
    body = json.loads(request.model_dump_json())
    assert body == {
        "account_id": ACCOUNT,
        "playbook_id": "renewal_readiness",
        "due_date": "2026-11-30",
        "assignee_id": str(MEMBER),
    }
    assert contracts.CreateTaskRequest.model_validate_json(request.model_dump_json()) == request
    minimal = contracts.CreateTaskRequest(account_id=ACCOUNT, playbook_id="renewal_readiness")
    assert minimal.due_date is None and minimal.assignee_id is None
    # A task starts proposed: the request cannot choose its first status.
    for status in ("approved", "in_progress", "proposed"):
        with pytest.raises(ValidationError):
            contracts.CreateTaskRequest.model_validate({**body, "status": status})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", ""),
        ("account_id", " DEMO-ACC-900001"),
        ("account_id", "DEMO-ACC-900001 "),
        ("account_id", "DEMO\x00ACC"),
        ("account_id", "A" * 129),
        ("account_id", 900001),
        ("playbook_id", "send_discount"),
        ("playbook_id", "Payment_Resolution"),
        ("due_date", "2026-11-30T00:00:00"),
        ("due_date", "2026-02-30"),
        ("due_date", "30/11/2026"),
        ("due_date", 1_790_000_000),
        ("due_date", datetime(2026, 11, 30, tzinfo=UTC)),
        ("assignee_id", "not-a-member-id"),
        ("assignee_id", 42),
    ],
)
def test_a_create_request_refuses_invalid_values(field, value):
    with pytest.raises(ValidationError):
        contracts.CreateTaskRequest.model_validate(
            {"account_id": ACCOUNT, "playbook_id": "payment_resolution", field: value}
        )


def test_a_refusal_never_quotes_the_value():
    with pytest.raises(ValidationError) as refused:
        contracts.CreateTaskRequest(account_id="private-name\x00", playbook_id="payment_resolution")
    assert "private-name" not in str(refused.value)


def test_assignment_is_explicit_and_null_unassigns():
    assert contracts.AssignTaskRequest(assignee_id=None).assignee_id is None
    assert contracts.AssignTaskRequest.model_validate({"assignee_id": str(MEMBER)}).assignee_id == (
        MEMBER
    )
    with pytest.raises(ValidationError):
        contracts.AssignTaskRequest.model_validate({})  # an empty body is not an unassignment
    with pytest.raises(ValidationError):
        contracts.AssignTaskRequest.model_validate({"assignee_id": "someone@example.test"})


@pytest.mark.parametrize("to_status", ["dismissed", "cancelled"])
def test_dismissing_or_cancelling_needs_a_reason(to_status):
    for missing in ({}, {"reason": None}, {"reason": ""}, {"reason": "   "}):
        with pytest.raises(ValidationError):
            contracts.TransitionTaskRequest.model_validate({"to_status": to_status, **missing})
    request = contracts.TransitionTaskRequest(to_status=to_status, reason="  Duplicate task.  ")
    assert request.reason == "Duplicate task."


def test_a_transition_request_is_bounded_and_never_targets_proposed():
    assert contracts.TransitionTaskRequest(to_status="approved").reason is None
    assert contracts.TransitionTaskRequest(to_status="completed", reason="a\nb").reason == "a\nb"
    for body in (
        {"to_status": "proposed"},
        {"to_status": "reopened"},
        {"to_status": "APPROVED"},
        {"to_status": "cancelled", "reason": "x" * (contracts.REASON_MAX_LENGTH + 1)},
        {"to_status": "cancelled", "reason": "tab\there"},
        {"to_status": "cancelled", "reason": "nul\x00"},
    ):
        with pytest.raises(ValidationError):
            contracts.TransitionTaskRequest.model_validate(body)
    assert set(typing.get_args(lifecycle.TransitionTarget)) == set(lifecycle.STATUSES) - {
        "proposed"
    }


def test_the_list_query_is_bounded_and_reads_each_status_once():
    query = contracts.TaskListQuery(status=("completed", "proposed", "completed"))
    assert query.status == ("proposed", "completed")
    assert (query.sort, query.page, query.page_size) == ("due_date_asc", 1, 25)
    for body in ({"page_size": 101}, {"page_size": 0}, {"page": 0}, {"status": ["open"]}):
        with pytest.raises(ValidationError):
            contracts.TaskListQuery.model_validate(body)


# --- responses: consistent with the lifecycle --------------------------------------------------


def _evidence(**changes) -> contracts.TaskEvidence:
    match = playbooks.require_applicable(_assess(status="past_due"), "payment_resolution")
    return contracts.task_evidence(match).model_copy(update=changes)


def _stamp(minutes: int, by: UUID = MEMBER) -> dict:
    return {"by": by, "at": T0 + timedelta(minutes=minutes)}


def _task(status: str, **changes) -> dict:
    stamps = {
        "proposed": {},
        "approved": {"approved": _stamp(10)},
        "in_progress": {"approved": _stamp(10), "started": _stamp(20)},
        "completed": {
            "approved": _stamp(10),
            "started": _stamp(20),
            "completed": {**_stamp(30), "note": "Billing confirmed the fix."},
        },
        "dismissed": {"closed": {**_stamp(10), "reason": "Already handled."}},
        "cancelled": {"closed": {**_stamp(10), "reason": "Account merged."}},
    }[status]
    return {
        "task_id": TASK,
        "account_id": ACCOUNT,
        "playbook": {
            "playbook_id": "payment_resolution",
            "version": 1,
            "title": "Payment resolution",
        },
        "evidence": _evidence(),
        "status": status,
        "assignee_id": None,
        "due_date": date(2026, 11, 30),
        "created": _stamp(0),
        "updated_at": T0 + timedelta(minutes=60),
        **stamps,
        **changes,
    }


@pytest.mark.parametrize("status", lifecycle.STATUSES)
def test_a_task_in_each_status_round_trips(status):
    task = contracts.RetentionTask.model_validate(_task(status))
    assert contracts.RetentionTask.model_validate_json(task.model_dump_json()) == task
    body = json.loads(task.model_dump_json())
    assert body["evidence"]["trigger_codes"] == ["payment_past_due"]
    assert body["evidence"]["indicators"][0]["evidence"] == [
        {"field": "status", "as_of_date": "2026-10-31", "value": "past_due"}
    ]
    assert body["created"]["at"] == "2026-11-02T09:00:00Z"


def test_a_cancelled_task_keeps_the_stamps_of_the_path_it_took():
    after_start = _task(
        "cancelled",
        approved=_stamp(5),
        started=_stamp(8),
        closed={**_stamp(10), "reason": "Customer asked to pause."},
    )
    assert contracts.RetentionTask.model_validate(after_start).started is not None
    contracts.RetentionTask.model_validate(_task("cancelled", approved=_stamp(5)))


@pytest.mark.parametrize(
    ("status", "changes"),
    [
        ("approved", {"approved": None}),
        ("in_progress", {"started": None}),
        ("completed", {"completed": None}),
        ("dismissed", {"closed": None}),
        ("cancelled", {"closed": None}),
        ("proposed", {"approved": _stamp(10)}),
        ("proposed", {"closed": {**_stamp(10), "reason": "x"}}),
        ("approved", {"started": _stamp(20)}),
        ("in_progress", {"completed": _stamp(30)}),
        ("completed", {"closed": {**_stamp(40), "reason": "x"}}),
        ("dismissed", {"approved": _stamp(5)}),  # only a proposed task is dismissed
        ("cancelled", {"started": _stamp(8)}),  # started without an approval
    ],
)
def test_a_task_inconsistent_with_its_status_is_refused(status, changes):
    with pytest.raises(ValidationError):
        contracts.RetentionTask.model_validate(_task(status, **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"approved": _stamp(-5)},  # approved before it was created
        {"started": _stamp(5)},  # started before it was approved
        {"updated_at": T0 + timedelta(minutes=15)},  # updated before it started
    ],
)
def test_a_task_out_of_time_order_is_refused(changes):
    with pytest.raises(ValidationError, match="time order"):
        contracts.RetentionTask.model_validate(_task("in_progress", **changes))


@pytest.mark.parametrize(
    "at",
    [datetime(2026, 11, 2, 9, 0), "2026-11-02T09:00:00", "not a time"],
    ids=["naive", "naive-text", "invalid"],
)
def test_a_timestamp_without_a_time_zone_is_refused(at):
    with pytest.raises(ValidationError):
        contracts.Stamp(by=MEMBER, at=at)
    with pytest.raises(ValidationError):
        contracts.RetentionTask.model_validate(_task("proposed", updated_at=at))
    offset = datetime(2026, 11, 2, 13, 0, tzinfo=timezone(timedelta(hours=4)))
    assert contracts.Stamp(by=MEMBER, at=offset).at == T0


def test_a_task_cannot_carry_evidence_its_playbook_does_not_cover():
    renewal = playbooks.require_applicable(
        _assess(renewal_date=AS_OF + timedelta(days=10)), "renewal_readiness"
    )
    with pytest.raises(ValidationError, match="does not cover"):
        contracts.RetentionTask.model_validate(
            _task("proposed", evidence=contracts.task_evidence(renewal))
        )


def test_evidence_tells_one_story():
    good = _evidence()
    indicator = good.indicators[0]
    bad = [
        {"trigger_codes": ("renewal_due",)},
        {"trigger_codes": ("payment_past_due", "payment_past_due")},
        {"indicators": (indicator, indicator), "trigger_codes": ("payment_past_due",) * 2},
        {"indicators": (indicator.model_copy(update={"observed_on": date(2026, 10, 1)}),)},
        {"account_level": "watch"},
        {"trigger_codes": ()},
    ]
    for update in bad:
        with pytest.raises(ValidationError):
            contracts.TaskEvidence.model_validate({**good.model_dump(), **update})


def test_task_detail_shows_the_lifecycle_moves_and_the_playbook_version():
    task = contracts.RetentionTask.model_validate(_task("approved"))
    playbook = contracts.playbook_out(playbooks.BY_ID["payment_resolution"])
    detail = contracts.TaskDetail(
        task=task, playbook=playbook, next_statuses=("in_progress", "cancelled"), notes=()
    )
    assert detail.next_statuses == ("in_progress", "cancelled")
    with pytest.raises(ValidationError):
        contracts.TaskDetail(task=task, playbook=playbook, next_statuses=("completed",), notes=())
    other = contracts.playbook_out(playbooks.BY_ID["renewal_readiness"])
    with pytest.raises(ValidationError):
        contracts.TaskDetail(
            task=task, playbook=other, next_statuses=("in_progress", "cancelled"), notes=()
        )


def test_a_task_page_is_bounded():
    task = contracts.RetentionTask.model_validate(_task("proposed"))
    page = {
        "total": 2,
        "page": 1,
        "page_size": 1,
        "pages": 2,
        "sort": "due_date_asc",
        "filters": {},
        "rows": [task],
        "notes": [],
    }
    contracts.TaskPage.model_validate(page)
    for update in ({"rows": [task, task]}, {"page": 3}, {"page_size": 101}):
        with pytest.raises(ValidationError):
            contracts.TaskPage.model_validate({**page, **update})


# --- the audit trail ---------------------------------------------------------------------------


def _event(sequence: int, action: str, **fields) -> dict:
    return {
        "event_id": UUID(int=sequence),
        "task_id": TASK,
        "sequence": sequence,
        "action": action,
        "actor_id": MEMBER,
        "occurred_at": T0 + timedelta(minutes=sequence),
        **fields,
    }


def test_a_valid_audit_trail_round_trips():
    events = [
        _event(1, "task_proposed", to_status="proposed", assignee_id=OTHER_MEMBER),
        _event(2, "task_unassigned"),
        _event(3, "task_assigned", assignee_id=MEMBER),
        _event(4, "status_changed", from_status="proposed", to_status="approved"),
        _event(5, "status_changed", from_status="approved", to_status="in_progress"),
        _event(6, "status_changed", from_status="in_progress", to_status="cancelled", reason="x"),
    ]
    page = contracts.TaskEventPage(
        task_id=TASK, total=6, page=1, page_size=25, pages=1, events=events
    )
    assert contracts.TaskEventPage.model_validate_json(page.model_dump_json()) == page
    assert all(e.actor_id == MEMBER for e in page.events)


@pytest.mark.parametrize(
    "event",
    [
        _event(2, "task_proposed", to_status="proposed"),
        _event(1, "task_proposed", to_status="approved"),
        _event(1, "task_proposed", to_status="proposed", reason="x"),
        _event(2, "task_assigned"),
        _event(2, "task_unassigned", assignee_id=MEMBER),
        _event(2, "task_assigned", assignee_id=MEMBER, to_status="approved"),
        _event(2, "status_changed", from_status="proposed", to_status="in_progress"),
        _event(2, "status_changed", from_status="proposed", to_status="dismissed"),
        _event(2, "status_changed", from_status="completed", to_status="in_progress"),
        _event(2, "status_changed", from_status="approved", to_status="approved"),
        _event(2, "status_changed", to_status="approved"),
        _event(
            2, "status_changed", from_status="proposed", to_status="approved", assignee_id=MEMBER
        ),
        {**_event(2, "task_assigned", assignee_id=MEMBER), "occurred_at": datetime(2026, 11, 2)},
        {**_event(2, "task_assigned", assignee_id=MEMBER), "organization_id": str(MEMBER)},
    ],
)
def test_an_audit_event_that_the_lifecycle_would_not_write_is_refused(event):
    with pytest.raises(ValidationError):
        contracts.TaskAuditEvent.model_validate(event)


def test_an_event_page_is_one_task_in_order():
    first = _event(1, "task_proposed", to_status="proposed")
    second = _event(2, "status_changed", from_status="proposed", to_status="approved")
    base = {"task_id": TASK, "total": 2, "page": 1, "page_size": 25, "pages": 1}
    contracts.TaskEventPage.model_validate({**base, "events": [first, second]})
    for events in ([second, first], [first, {**second, "task_id": UUID(int=99)}]):
        with pytest.raises(ValidationError):
            contracts.TaskEventPage.model_validate({**base, "events": events})


# --- a proposal checked against the server's assessment ----------------------------------------


def test_a_proposal_records_the_servers_evidence():
    assessment = _declining()
    request = contracts.CreateTaskRequest(
        account_id=ACCOUNT, playbook_id="product_usage_investigation", due_date="2026-11-20"
    )
    proposal = contracts.check_proposal(request, assessment, today=date(2026, 11, 2))
    assert proposal.playbook.model_dump() == {
        "playbook_id": "product_usage_investigation",
        "version": 1,
        "title": "Product usage investigation",
    }
    shown = workbench.assessment_out(assessment)
    assert proposal.evidence.indicators == tuple(shown.indicators)
    assert proposal.evidence.trigger_codes == ("sessions_decline", "key_actions_decline")
    assert proposal.evidence.assessment_as_of_date == AS_OF
    assert proposal.evidence.method_version == risk.METHOD_VERSION
    assert proposal.evidence.account_level == "attention"
    assert proposal.due_date == date(2026, 11, 20) and proposal.assignee_id is None


def test_a_proposal_is_refused_when_the_playbook_does_not_apply_or_the_date_is_out_of_range():
    assessment = _assess(status="past_due")
    today = date(2026, 11, 2)
    with pytest.raises(ActionRefused) as refused:
        contracts.check_proposal(
            contracts.CreateTaskRequest(account_id=ACCOUNT, playbook_id="renewal_readiness"),
            assessment,
            today,
        )
    assert refused.value.code == "playbook_not_applicable"
    for due, code in (("2026-11-01", "due_date_in_past"), ("2027-11-04", "due_date_too_far")):
        request = contracts.CreateTaskRequest(
            account_id=ACCOUNT, playbook_id="payment_resolution", due_date=due
        )
        with pytest.raises(ActionRefused) as refused:
            contracts.check_proposal(request, assessment, today)
        assert refused.value.code == code
    other = contracts.CreateTaskRequest(
        account_id="DEMO-ACC-000001", playbook_id="payment_resolution"
    )
    with pytest.raises(ValueError, match="requested account"):
        contracts.check_proposal(other, assessment, today)


# --- the lifecycle -----------------------------------------------------------------------------


def test_the_lifecycle_is_the_documented_table():
    assert {s: set(n) for s, n in lifecycle.TRANSITIONS.items()} == EXPECTED_TRANSITIONS
    assert typing.get_args(lifecycle.TaskStatus) == lifecycle.STATUSES
    assert {"completed", "dismissed", "cancelled"} == lifecycle.TERMINAL
    assert {"dismissed", "cancelled"} == lifecycle.REASON_REQUIRED
    with pytest.raises(TypeError):
        lifecycle.TRANSITIONS["completed"] = frozenset({"proposed"})  # type: ignore[index]


@pytest.mark.parametrize("target", lifecycle.STATUSES)
@pytest.mark.parametrize("current", lifecycle.STATUSES)
def test_every_status_pair(current, target):
    """All 36 pairs: allowed exactly where the table says, refused with a named rule otherwise."""
    if target in EXPECTED_TRANSITIONS[current]:
        assert lifecycle.validate_transition(current, target, "A reason.") is None
        return
    with pytest.raises(ActionRefused) as refused:
        lifecycle.validate_transition(current, target, "A reason.")
    if current == target:
        expected = "no_op_transition"
    elif current in lifecycle.TERMINAL:
        expected = "terminal_status"
    elif current == "proposed" and target in {"in_progress", "completed"}:
        expected = "approval_required"
    else:
        expected = "transition_not_allowed"
    assert refused.value.code == expected


def test_work_cannot_start_without_approval():
    with pytest.raises(ActionRefused, match="approved before") as refused:
        lifecycle.validate_transition("proposed", "in_progress")
    assert refused.value.code == "approval_required"
    lifecycle.validate_transition("proposed", "approved")
    lifecycle.validate_transition("approved", "in_progress")


@pytest.mark.parametrize(
    ("current", "target"), [("proposed", "dismissed"), ("approved", "cancelled")]
)
def test_closing_without_completing_needs_a_reason(current, target):
    for reason in (None, "", "  \n "):
        with pytest.raises(ActionRefused) as refused:
            lifecycle.validate_transition(current, target, reason)
        assert refused.value.code == "reason_required"
    lifecycle.validate_transition(current, target, "Duplicate of another task.")


def test_an_unknown_status_is_refused_by_name():
    for current, target in (("open", "approved"), ("proposed", "done"), ("Proposed", "approved")):
        with pytest.raises(ActionRefused) as refused:
            lifecycle.validate_transition(current, target)
        assert refused.value.code == "unknown_status"


def test_a_refusal_is_the_services_validation_error():
    with pytest.raises(SchemaValidationError) as refused:
        lifecycle.validate_transition("completed", "in_progress")
    assert refused.value.problems == [str(refused.value)]
    assert "cannot be reopened" in str(refused.value)


def test_the_lifecycle_is_deterministic():
    outcomes = set()
    for _ in range(3):
        for current in lifecycle.STATUSES:
            outcomes.add((current, lifecycle.next_statuses(current)))
    assert sorted(outcomes) == sorted(
        (s, tuple(t for t in lifecycle.STATUSES if t in EXPECTED_TRANSITIONS[s]))
        for s in lifecycle.STATUSES
    )


def test_assignment_changes_only_an_open_task_and_only_when_it_changes():
    lifecycle.validate_assignment("proposed", None, str(MEMBER))
    lifecycle.validate_assignment("in_progress", str(MEMBER), None)
    lifecycle.validate_assignment("approved", str(MEMBER), str(OTHER_MEMBER))
    for status in sorted(lifecycle.TERMINAL):
        with pytest.raises(ActionRefused) as refused:
            lifecycle.validate_assignment(status, None, str(MEMBER))
        assert refused.value.code == "terminal_status"
    for same in (None, str(MEMBER)):
        with pytest.raises(ActionRefused) as refused:
            lifecycle.validate_assignment("approved", same, same)
        assert refused.value.code == "no_op_assignment"


def test_a_due_date_is_from_today_to_366_days_ahead():
    today = date(2026, 11, 2)
    lifecycle.validate_due_date(today, today)
    lifecycle.validate_due_date(today + timedelta(days=366), today)
    with pytest.raises(ActionRefused, match="before today"):
        lifecycle.validate_due_date(today - timedelta(days=1), today)
    with pytest.raises(ActionRefused, match="366"):
        lifecycle.validate_due_date(today + timedelta(days=367), today)


# --- scope: contracts only, nothing served, stored or sent -------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("name", M3_MODULES)
def test_the_m3_modules_serve_store_send_and_time_nothing(name):
    path = WORKSPACE / name
    roots = {m.split(".")[0] for m in _imports(path)}
    assert roots <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "itertools",
        "pydantic",
        "re",
        "types",
        "typing",
        "uuid",
        "churnsense",
    }, roots
    churnsense = {m for m in _imports(path) if m.startswith("churnsense")}
    assert churnsense <= {
        "churnsense.exceptions",
        "churnsense.workspace",
        "churnsense.workspace.action_lifecycle",
        "churnsense.workspace.action_playbooks",
        "churnsense.workspace.risk",
    }, churnsense
    source = path.read_text(encoding="utf-8")
    for forbidden in ("now(", "today(", "uuid4", "time.time", "random", "open("):
        assert forbidden not in source, forbidden


def test_nothing_serves_or_imports_the_action_center():
    """No route, router or service module reaches M3: it is contracts only."""
    m3 = {f"churnsense.workspace.{name[:-3]}" for name in M3_MODULES}
    for path in sorted((ROOT / "src" / "churnsense").rglob("*.py")):
        if path.name in M3_MODULES or path.name in CONTRACT_ONLY_IMPORTERS:
            continue
        imported = _imports(path)
        assert not imported & m3, path
        if "churnsense.workspace" in imported:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                a.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "churnsense.workspace"
                for a in node.names
            }
            assert not names & {name[:-3] for name in M3_MODULES}, path


def test_the_preview_installs_no_action_center_route(monkeypatch):
    from churnsense.api import main as api_main
    from churnsense.api import middleware
    from churnsense.workspace import api as preview_api

    application = api_main.app
    routes = list(application.router.routes)
    monkeypatch.setattr(middleware, "LOCAL_UPLOAD_PATHS", middleware.LOCAL_UPLOAD_PATHS)
    monkeypatch.setattr(middleware, "LOCAL_QUERY_PATHS", middleware.LOCAL_QUERY_PATHS)
    try:
        application.openapi_schema = None
        assert preview_api.install(application, {preview_api.FLAG: "1"})
        schema = application.openapi()
        paths = set(schema["paths"])
        assert not [p for p in paths if "task" in p or "playbook" in p]
        assert not [n for n in schema["components"]["schemas"] if "Task" in n or "Playbook" in n]
    finally:
        application.router.routes[:] = routes
        application.openapi_schema = None


@pytest.mark.parametrize(
    "model",
    [
        contracts.PlaybookCatalog,
        contracts.CreateTaskRequest,
        contracts.AssignTaskRequest,
        contracts.TransitionTaskRequest,
        contracts.TaskListQuery,
        contracts.TaskDetail,
        contracts.TaskPage,
        contracts.TaskEventPage,
        contracts.TaskProposal,
    ],
    ids=lambda m: m.__name__,
)
def test_each_contract_has_a_json_schema_without_a_causal_claim(model):
    schema = model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert not _offending(json.dumps(schema))
