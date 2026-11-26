"""Measured retention (M4): the outcome record, logo retention, GRR, NRR and the task link.

**Test data only.** Every outcome below is hand-written for these tests: the
accounts, amounts and dates are invented, and no figure here is a measured
product or business result. Each metric is checked against a sum worked out by
hand in the test's own docstring or comment.
"""

from __future__ import annotations

import ast
import json
import random
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import outcome_schemas as oc
from churnsense.workspace import retention_metrics as rm
from churnsense.workspace.action_lifecycle import ActionRefused
from churnsense.workspace.action_schemas import RetentionTask
from tests.test_wording import _offending
from tests.test_workspace_action_center import _task

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "src" / "churnsense" / "workspace"
M4_MODULES = (
    "outcome_schemas.py",
    "retention_metrics.py",
    "experiment_schemas.py",
    "experiment_analysis.py",
)
MEMBER = UUID("6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f")
START, END = date(2026, 1, 1), date(2026, 3, 31)
WINDOW = oc.MeasurementWindow(start=START, end=END)
OBSERVED = date(2026, 4, 1)
AS_OF = date(2026, 4, 15)
_ids = iter(range(1, 10_000))


def facts(**changes) -> dict:
    """A retained USD account in WINDOW, observed the day after it ended (test data)."""
    base = {
        "account_id": "TEST-ACC-000001",
        "window_start": START,
        "window_end": END,
        "observed_on": OBSERVED,
        "status": "retained",
        "currency": "USD",
        "starting_mrr": "100.00",
        "ending_mrr": "100.00",
    }
    return {**base, **changes}


def outcome(recorded_at: datetime | None = None, **changes) -> oc.AccountOutcome:
    return oc.AccountOutcome.model_validate(
        {
            "outcome_id": UUID(int=next(_ids)),
            "provenance": {
                "source_system": "billing_system",
                "recorded": {
                    "by": MEMBER,
                    "at": recorded_at or datetime(2027, 1, 4, 8, tzinfo=UTC),
                },
            },
            **facts(**changes),
        }
    )


# --- the outcome record ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "ending"), [("retained", "120.50"), ("churned", "0"), ("unknown", None)]
)
def test_each_outcome_round_trips_with_amounts_as_strings(status, ending):
    record = outcome(status=status, ending_mrr=ending, starting_mrr="1250.5000")
    body = json.loads(record.model_dump_json())
    assert body["starting_mrr"] == "1250.5000"
    assert body["ending_mrr"] == (None if ending is None else ending)
    assert oc.AccountOutcome.model_validate_json(record.model_dump_json()) == record
    assert (
        set(oc.OUTCOME_MEANINGS) == set(oc.OUTCOME_STATUSES) == {"retained", "churned", "unknown"}
    )


@pytest.mark.parametrize(
    ("status", "ending"),
    [
        ("retained", "0"),
        ("retained", "0.0000"),
        ("retained", None),
        ("churned", "10.00"),
        ("churned", None),
        ("unknown", "0"),
        ("unknown", "55.00"),
    ],
)
def test_the_status_needs_its_evidence(status, ending):
    with pytest.raises(ValidationError):
        oc.OutcomeSubmission.model_validate(facts(status=status, ending_mrr=ending))


def test_an_unknown_outcome_is_never_zero():
    record = outcome(status="unknown", ending_mrr=None)
    assert record.ending_mrr is None
    assert json.loads(record.model_dump_json())["ending_mrr"] is None
    without = facts(status="unknown")
    del without["ending_mrr"]
    with pytest.raises(ValidationError):  # ending_mrr is required, even when null
        oc.OutcomeSubmission.model_validate(without)


@pytest.mark.parametrize(
    "amount",
    [
        "-1.00",
        "-0",
        100.5,
        100,
        True,
        "1e3",
        "1,000.00",
        "$100",
        " 100",
        "100.12345",
        "1234567890123",
        "NaN",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-5"),
    ],
)
def test_an_amount_is_a_plain_non_negative_decimal(amount):
    with pytest.raises(ValidationError):
        oc.OutcomeSubmission.model_validate(facts(starting_mrr=amount))
    with pytest.raises(ValidationError):
        oc.OutcomeSubmission.model_validate(facts(status="retained", ending_mrr=amount))


def test_amounts_are_exact_decimals():
    record = oc.OutcomeSubmission.model_validate(facts(starting_mrr=Decimal("999999999999.9999")))
    assert record.starting_mrr == Decimal("999999999999.9999")
    assert isinstance(record.starting_mrr, Decimal)


@pytest.mark.parametrize(
    "changes",
    [
        {"window_end": START},
        {"window_end": START - timedelta(days=1)},
        {"observed_on": END - timedelta(days=1)},
        {"window_start": "1899-12-31"},
        {"observed_on": "2200-01-01"},
        {"window_start": "2026-01-01T00:00:00"},
        {"observed_on": 1_780_000_000},
        {"currency": "usd"},
        {"currency": "US"},
        {"currency": "USDX"},
        {"currency": "U$D"},
        {"account_id": "someone@example.test"},
        {"account_id": "+1 555 123 4567"},
        {"account_id": " TEST-ACC-000001"},
        {"source_reference": "billing contact someone@example.test"},
        {"source_reference": "inv\x00oice"},
        {"status": "lost"},
    ],
)
def test_an_inconsistent_or_unsafe_outcome_is_refused(changes):
    with pytest.raises(ValidationError):
        oc.OutcomeSubmission.model_validate(facts(**changes))


def test_an_outcome_observed_on_the_window_end_is_valid():
    assert oc.OutcomeSubmission.model_validate(facts(observed_on=END)).observed_on == END
    assert (
        oc.OutcomeSubmission.model_validate(
            facts(source_reference="INV-2026-0042")
        ).source_reference
        == "INV-2026-0042"
    )


def test_a_refusal_never_quotes_the_value():
    with pytest.raises(ValidationError) as refused:
        oc.OutcomeSubmission.model_validate(facts(account_id="private.person@example.test"))
    assert "private.person" not in str(refused.value)


def test_provenance_is_the_servers_and_time_zoned():
    oc.AccountOutcome.model_validate(outcome().model_dump())  # the control
    with pytest.raises(ValidationError):
        oc.AccountOutcome.model_validate(
            {
                **outcome().model_dump(),
                "provenance": {
                    "source_system": "billing_system",
                    "recorded": {"by": MEMBER, "at": datetime(2026, 4, 2, 8)},
                },
            }
        )
    for source in ("spreadsheet", "model_prediction"):
        with pytest.raises(ValidationError):
            oc.OutcomeProvenance.model_validate(
                {"source_system": source, "recorded": {"by": MEMBER, "at": datetime.now(UTC)}}
            )


def test_an_outcome_cannot_be_recorded_before_it_was_observed():
    body = outcome().model_dump()
    body["provenance"]["recorded"]["at"] = datetime(2026, 3, 31, 23, tzinfo=UTC)
    with pytest.raises(ValidationError, match="recorded before"):
        oc.AccountOutcome.model_validate(body)


SUBMISSIONS = {
    oc.OutcomeSubmission: facts(),
    oc.OutcomeImportRequest: {"outcomes": [facts()]},
    oc.LinkTaskOutcomeRequest: {"task_id": str(UUID(int=1)), "outcome_id": str(UUID(int=2))},
    oc.RetentionMetricsQuery: {"window_start": START, "window_end": END, "as_of": AS_OF},
}


@pytest.mark.parametrize("model", list(SUBMISSIONS), ids=lambda m: m.__name__)
def test_no_m4_request_has_a_server_derived_field(model):
    """A request may name an existing task or outcome by ID; it never sets an ID or identity."""
    named = set(model.model_fields) & oc.SERVER_DERIVED_FIELDS
    assert named <= oc.REFERENCE_FIELDS
    assert named == (oc.REFERENCE_FIELDS if model is oc.LinkTaskOutcomeRequest else set())
    assert {"organization_id", "actor_id", "created_by", "outcome_id", "provenance"} <= (
        oc.SERVER_DERIVED_FIELDS
    )


@pytest.mark.parametrize("model", list(SUBMISSIONS), ids=lambda m: m.__name__)
@pytest.mark.parametrize("field", sorted(oc.SERVER_DERIVED_FIELDS))
def test_a_client_cannot_inject_identity_provenance_or_ids(model, field):
    model.model_validate(SUBMISSIONS[model])  # the control
    if field in model.model_fields:
        pytest.skip("a reference to an existing record, which this request names")
    with pytest.raises(ValidationError) as refused:
        model.model_validate({**SUBMISSIONS[model], field: str(MEMBER)})
    assert [e["type"] for e in refused.value.errors()] == ["extra_forbidden"]


def test_duplicate_outcomes_are_refused_by_key_and_by_id():
    first = outcome(account_id="TEST-ACC-000001")
    again = outcome(account_id="TEST-ACC-000001", status="churned", ending_mrr="0")
    other_window = outcome(account_id="TEST-ACC-000001", window_end=END + timedelta(days=1))
    oc.check_unique_outcomes([first, other_window])
    with pytest.raises(SchemaValidationError) as refused:
        oc.check_unique_outcomes([first, other_window, again])
    assert refused.value.problems == [
        "outcome 3 repeats the account and measurement window of outcome 1"
    ]
    same_id = first.model_copy(update={"account_id": "TEST-ACC-000002"})
    with pytest.raises(SchemaValidationError) as refused:
        oc.check_unique_outcomes([first, same_id])
    assert refused.value.problems == ["outcome 2 repeats the outcome ID of outcome 1"]
    with pytest.raises(ValidationError, match="more than once"):
        oc.OutcomeImportRequest(outcomes=[facts(), facts(status="churned", ending_mrr="0")])


def test_records_are_immutable():
    record = outcome()
    with pytest.raises(ValidationError):
        record.status = "churned"  # type: ignore[misc]


# --- the metrics -------------------------------------------------------------------------------


def book() -> list[oc.AccountOutcome]:
    """Test data. USD: A 100->120, B 200->150, C 50->0 churned, D 80 unknown, G not yet
    observed; E a new customer (starting 0); F another window. EUR: H 1000.50 kept, I 999.50
    churned."""
    return [
        outcome(account_id="A", starting_mrr="100.00", ending_mrr="120.00"),
        outcome(account_id="B", starting_mrr="200.00", ending_mrr="150.00"),
        outcome(account_id="C", starting_mrr="50.00", status="churned", ending_mrr="0"),
        outcome(account_id="D", starting_mrr="80.00", status="unknown", ending_mrr=None),
        outcome(account_id="E", starting_mrr="0", ending_mrr="40.00"),
        outcome(account_id="F", window_start=date(2025, 10, 1), window_end=date(2025, 12, 31)),
        outcome(account_id="G", starting_mrr="30.00", observed_on=date(2026, 4, 20)),
        outcome(account_id="H", currency="EUR", starting_mrr="1000.50", ending_mrr="1000.50"),
        outcome(
            account_id="I", currency="EUR", starting_mrr="999.50", status="churned", ending_mrr="0"
        ),
    ]


def test_every_record_is_accounted_for():
    coverage = rm.retention_metrics(book(), WINDOW, AS_OF).coverage
    assert (coverage.records, coverage.other_window, coverage.not_in_starting_cohort) == (9, 1, 1)
    c = coverage.counts
    assert (c.eligible, c.immature, c.not_yet_observed, c.matured) == (7, 0, 1, 6)
    assert (c.retained, c.churned, c.unknown, c.observed) == (3, 2, 1, 5)
    assert (c.coverage.numerator, c.coverage.denominator, c.coverage.percent) == (
        5,
        6,
        Decimal("83.3"),
    )


def test_logo_retention_counts_retained_over_observed():
    """Retained A, B, H; churned C, I: 3 / 5. D (unknown) and G (not yet observed) are in neither."""
    logo = rm.retention_metrics(book(), WINDOW, AS_OF).logo_retention
    assert (logo.numerator, logo.denominator, logo.percent) == (3, 5, Decimal("60.0"))
    assert logo.unavailable_reason is None


def test_grr_and_nrr_per_currency_by_hand():
    """USD observed A, B, C: starting 350.00; GRR 100 + 150 + 0 = 250.00 (71.42% -> 71.4);
    NRR 120 + 150 + 0 = 270.00 (77.14% -> 77.1); revenue coverage 350 / 430 (81.39% -> 81.3,
    cut, not rounded up). EUR: 1000.50 / 2000.00 = 50.025% -> 50.0 for both."""
    by = {r.currency: r for r in rm.retention_metrics(book(), WINDOW, AS_OF).revenue}
    assert list(by) == ["EUR", "USD"]
    usd, eur = by["USD"], by["EUR"]
    assert (usd.grr.numerator, usd.grr.denominator, usd.grr.percent) == (
        Decimal("250.00"),
        Decimal("350.00"),
        Decimal("71.4"),
    )
    assert (usd.nrr.numerator, usd.nrr.percent) == (Decimal("270.00"), Decimal("77.1"))
    assert (usd.matured_starting_mrr, usd.unknown_starting_mrr) == (
        Decimal("430.00"),
        Decimal("80.00"),
    )
    assert usd.revenue_coverage.percent == Decimal("81.3")
    assert (usd.counts.eligible, usd.counts.not_yet_observed, usd.counts.unknown) == (5, 1, 1)
    assert (eur.grr.denominator, eur.grr.numerator, eur.grr.percent) == (
        Decimal("2000.00"),
        Decimal("1000.50"),
        Decimal("50.0"),
    )
    assert eur.nrr.percent == Decimal("50.0")
    body = json.loads(eur.model_dump_json())
    assert body["grr"]["denominator"] == "2000.00" and body["grr"]["percent"] == "50.0"


def test_no_amount_crosses_a_currency():
    """The same accounts in one currency or split across two: each currency only its own."""
    split = rm.retention_metrics(book(), WINDOW, AS_OF).revenue
    starting = {r.currency: r.grr.denominator for r in split}
    assert starting == {"EUR": Decimal("2000.00"), "USD": Decimal("350.00")}
    assert not hasattr(rm.retention_metrics(book(), WINDOW, AS_OF), "total_mrr")


def test_nrr_may_exceed_100_and_grr_may_not():
    metrics = rm.retention_metrics(
        [outcome(account_id="X", starting_mrr="100.00", ending_mrr="250.00")], WINDOW, AS_OF
    )
    (usd,) = metrics.revenue
    assert usd.nrr.percent == Decimal("250.0") and usd.nrr.can_exceed_100
    assert usd.grr.percent == Decimal("100.0") and not usd.grr.can_exceed_100
    with pytest.raises(ValidationError):
        oc.AmountRatio(
            numerator=Decimal("2"),
            denominator=Decimal("1"),
            percent=Decimal("200.0"),
            unavailable_reason=None,
            can_exceed_100=False,
        )


def test_contraction_and_churn_lower_grr_and_expansion_does_not_raise_it():
    """Starting 100 + 100 + 100: one expands to 300, one contracts to 40, one churns.
    GRR (100 + 40 + 0) / 300 = 46.6; NRR (300 + 40 + 0) / 300 = 113.3."""
    metrics = rm.retention_metrics(
        [
            outcome(account_id="X", ending_mrr="300.00"),
            outcome(account_id="Y", ending_mrr="40.00"),
            outcome(account_id="Z", status="churned", ending_mrr="0"),
        ],
        WINDOW,
        AS_OF,
    )
    (usd,) = metrics.revenue
    assert (usd.grr.numerator, usd.grr.percent) == (Decimal("140.00"), Decimal("46.6"))
    assert (usd.nrr.numerator, usd.nrr.percent) == (Decimal("340.00"), Decimal("113.3"))
    assert usd.grr.denominator == usd.nrr.denominator == Decimal("300.00")


def test_a_new_customer_never_enters_the_starting_cohort():
    with_new = rm.retention_metrics(
        [outcome(account_id="A"), outcome(account_id="N", starting_mrr="0", ending_mrr="500.00")],
        WINDOW,
        AS_OF,
    )
    alone = rm.retention_metrics([outcome(account_id="A")], WINDOW, AS_OF)
    assert with_new.revenue == alone.revenue
    assert with_new.logo_retention == alone.logo_retention
    assert with_new.coverage.not_in_starting_cohort == 1


def test_unknown_is_neither_churn_nor_retention():
    known = [outcome(account_id="A"), outcome(account_id="C", status="churned", ending_mrr="0")]
    with_unknown = [*known, outcome(account_id="U", status="unknown", ending_mrr=None)]
    a = rm.retention_metrics(known, WINDOW, AS_OF)
    b = rm.retention_metrics(with_unknown, WINDOW, AS_OF)
    assert a.logo_retention == b.logo_retention  # 1 / 2 either way
    assert (a.revenue[0].grr, a.revenue[0].nrr) == (b.revenue[0].grr, b.revenue[0].nrr)
    assert b.coverage.counts.unknown == 1 and b.coverage.counts.coverage.percent == Decimal("66.6")
    assert b.revenue[0].unknown_starting_mrr == Decimal("100.00")


def test_an_immature_window_gives_no_percentage_and_says_why():
    metrics = rm.retention_metrics(book(), WINDOW, END - timedelta(days=1))
    c = metrics.coverage.counts
    assert (c.eligible, c.immature, c.matured) == (7, 7, 0)
    for ratio in (metrics.logo_retention, c.coverage, *(r.grr for r in metrics.revenue)):
        assert ratio.percent is None and ratio.denominator == 0
        assert "not mature" in ratio.unavailable_reason
    assert json.loads(metrics.model_dump_json())["logo_retention"]["percent"] is None


def test_no_records_give_no_percentage():
    metrics = rm.retention_metrics([], WINDOW, AS_OF)
    assert metrics.logo_retention.percent is None
    assert "No matured" in metrics.logo_retention.unavailable_reason
    assert metrics.revenue == () and metrics.coverage.records == 0


def test_all_unknown_gives_no_percentage_but_full_visibility():
    metrics = rm.retention_metrics(
        [outcome(account_id=f"U{i}", status="unknown", ending_mrr=None) for i in range(3)],
        WINDOW,
        AS_OF,
    )
    assert metrics.logo_retention.percent is None
    assert metrics.coverage.counts.unknown == 3
    assert metrics.coverage.counts.coverage.percent == Decimal("0.0")  # 0 of 3 is a real 0%


def test_duplicate_records_are_refused_before_measuring():
    with pytest.raises(SchemaValidationError):
        rm.retention_metrics([outcome(account_id="A"), outcome(account_id="A")], WINDOW, AS_OF)


def test_the_metrics_are_deterministic_and_exact():
    records = book()
    first = rm.retention_metrics(records, WINDOW, AS_OF)
    shuffled = records[:]
    random.Random(7).shuffle(shuffled)
    assert rm.retention_metrics(shuffled, WINDOW, AS_OF).model_dump_json() == (
        first.model_dump_json()
    )
    tenths = [
        outcome(account_id=f"T{i}", starting_mrr="0.1000", ending_mrr="0.1") for i in range(3)
    ]
    (usd,) = rm.retention_metrics(tenths, WINDOW, AS_OF).revenue
    assert usd.grr.denominator == Decimal("0.3000") and usd.grr.percent == Decimal("100.0")


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (1, 3, "33.3"),
        (2, 3, "66.6"),
        (1, 1, "100.0"),
        (0, 7, "0.0"),
        (999, 1000, "99.9"),
        (9999, 10000, "99.9"),
        (Decimal("1000.50"), Decimal("2000.00"), "50.0"),
        (Decimal("250.00"), Decimal("100.00"), "250.0"),
    ],
)
def test_percentages_are_cut_toward_zero_to_one_decimal(numerator, denominator, expected):
    assert rm.percent(numerator, denominator) == Decimal(expected)
    assert str(rm.percent(numerator, denominator)) == expected


def test_the_metrics_explain_themselves():
    metrics = rm.retention_metrics(book(), WINDOW, AS_OF)
    assert metrics.metrics_version == "retention_metrics.v1"
    assert set(metrics.definitions) >= {"logo_retention", "grr", "nrr", "coverage", "rounding"}
    for text in (*metrics.definitions.values(), *metrics.notes):
        assert not _offending(text), text


def test_the_metrics_read_no_status_signal_or_task():
    """Only recorded outcomes: nothing from snapshots, rules, the synthetic book or tasks.

    One exception: the experiment contracts read M2's ``Snapshot`` type, the trusted
    baseline that makes an account eligible before it is assigned."""
    for name in M4_MODULES:
        imported = _imports(WORKSPACE / name)
        for module in (
            "risk",
            "synthetic",
            "snapshot",
            "workbench",
            "portfolio",
            "action_playbooks",
        ):
            if (name, module) == ("experiment_schemas.py", "snapshot"):
                continue
            assert f"churnsense.workspace.{module}" not in imported, (name, module)
    assert "churnsense.workspace.snapshot" in _imports(WORKSPACE / "experiment_schemas.py")
    assert list(rm.retention_metrics.__code__.co_varnames[:3]) == ["outcomes", "window", "as_of"]


# --- a task and an outcome ---------------------------------------------------------------------


def completed_task(at: datetime | None = None, **changes) -> RetentionTask:
    """M3's completed test task (account DEMO-ACC-900001), completed at ``at``."""
    body = _task("completed", **changes)
    if at is not None:
        body["created"] = {"by": MEMBER, "at": at - timedelta(hours=3)}
        body["approved"] = {"by": MEMBER, "at": at - timedelta(hours=2)}
        body["started"] = {"by": MEMBER, "at": at - timedelta(hours=1)}
        body["completed"] = {"by": MEMBER, "at": at}
        body["updated_at"] = at
    return RetentionTask.model_validate(body)


def task_outcome(window_start: date, window_end: date, **changes) -> oc.AccountOutcome:
    return outcome(
        recorded_at=datetime(2028, 1, 1, tzinfo=UTC),
        account_id="DEMO-ACC-900001",
        window_start=window_start,
        window_end=window_end,
        observed_on=window_end,
        **changes,
    )


def test_a_completed_task_is_linked_to_a_later_outcome_in_time():
    """M3's test task was completed on 2026-11-02 (UTC)."""
    task = completed_task()
    during = task_outcome(date(2026, 11, 1), date(2027, 1, 31))
    before = task_outcome(date(2026, 11, 3), date(2027, 2, 2))
    assert oc.check_association(task, during) == "task_completed_during_window"
    assert oc.check_association(task, before) == "task_completed_before_window"
    link = oc.associate(
        task, during, UUID(int=77), {"by": MEMBER, "at": datetime(2027, 2, 5, tzinfo=UTC)}
    )
    assert link.interpretation == "observed_temporal_association"
    assert (link.task_id, link.outcome_id, link.account_id) == (
        task.task_id,
        during.outcome_id,
        during.account_id,
    )
    assert link.playbook == task.playbook and link.task_completed_on == date(2026, 11, 2)
    assert oc.TaskOutcomeAssociation.model_validate_json(link.model_dump_json()) == link


def test_the_relationship_uses_the_utc_date_of_completion():
    """23:30 at UTC-5 on 2 November is 04:30 on 3 November in UTC: during a window that
    starts on 3 November, not before it."""
    late = completed_task(at=datetime(2026, 11, 2, 23, 30, tzinfo=timezone(timedelta(hours=-5))))
    assert oc.check_association(late, task_outcome(date(2026, 11, 3), date(2027, 1, 31))) == (
        "task_completed_during_window"
    )


@pytest.mark.parametrize(
    ("task_changes", "outcome_changes", "code"),
    [
        ({"account_id": "DEMO-ACC-000001"}, {}, "account_mismatch"),
        ({}, {"status": "unknown", "ending_mrr": None}, "outcome_not_observed"),
        (
            {},
            {"window_start": date(2026, 8, 1), "window_end": date(2026, 10, 31)},
            "task_after_window",
        ),
    ],
)
def test_a_link_that_does_not_hold_is_refused(task_changes, outcome_changes, code):
    task = completed_task(**task_changes)
    window = {"window_start": date(2026, 11, 1), "window_end": date(2027, 1, 31)}
    record = task_outcome(**{**window, **outcome_changes})
    with pytest.raises(ActionRefused) as refused:
        oc.check_association(task, record)
    assert refused.value.code == code


@pytest.mark.parametrize(
    "status", ["proposed", "approved", "in_progress", "dismissed", "cancelled"]
)
def test_only_a_completed_task_is_linked(status):
    task = RetentionTask.model_validate(_task(status))
    with pytest.raises(ActionRefused) as refused:
        oc.check_association(task, task_outcome(date(2026, 11, 1), date(2027, 1, 31)))
    assert refused.value.code == "task_not_completed"


def test_an_association_record_cannot_contradict_its_dates():
    task = completed_task()
    link = oc.associate(
        task,
        task_outcome(date(2026, 11, 1), date(2027, 1, 31)),
        UUID(int=77),
        {"by": MEMBER, "at": datetime(2027, 2, 5, tzinfo=UTC)},
    )
    body = link.model_dump()
    for update in (
        {"relationship": "task_completed_before_window"},
        {"interpretation": "caused_retention"},
        {"outcome_status": "unknown"},
        {"revenue_saved": "100.00"},
        {"account_id": "someone@example.test"},
    ):
        with pytest.raises(ValidationError):
            oc.TaskOutcomeAssociation.model_validate({**body, **update})


def test_an_association_claims_no_impact():
    names = _schema_names(oc.TaskOutcomeAssociation.model_json_schema())
    for word in ("impact", "saved", "prevent", "attribut", "caus", "effect", "lift", "recover"):
        assert not [n for n in names if word in n.lower()], word
    assert not _offending(oc.ASSOCIATION_NOTE)
    assert "not revenue saved" in oc.ASSOCIATION_NOTE


# --- scope -------------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.module == "churnsense.workspace":
                found |= {f"churnsense.workspace.{a.name}" for a in node.names}
    return found


def _schema_names(schema: dict) -> set[str]:
    """Every property name anywhere in a JSON schema."""
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            names.update(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return names


@pytest.mark.parametrize("name", M4_MODULES)
def test_the_m4_modules_store_send_and_time_nothing(name):
    path = WORKSPACE / name
    roots = {m.split(".")[0] for m in _imports(path)}
    assert roots <= {
        "__future__",
        "collections",
        "datetime",
        "decimal",
        "fractions",
        "pydantic",
        "re",
        "typing",
        "uuid",
        "churnsense",
    }, roots
    source = path.read_text(encoding="utf-8")
    for forbidden in (".now(", ".today(", "uuid4", "import random", "random.", "open("):
        assert forbidden not in source, forbidden


def test_nothing_serves_or_imports_m4():
    m4 = {f"churnsense.workspace.{name[:-3]}" for name in M4_MODULES}
    for path in sorted((ROOT / "src" / "churnsense").rglob("*.py")):
        if path.name not in M4_MODULES:
            assert not _imports(path) & m4, path


def test_the_preview_installs_no_m4_route(monkeypatch):
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
        words = ("outcome", "metric", "experiment", "assignment", "retention-")
        assert not [p for p in schema["paths"] if any(w in p for w in words)]
        assert not [
            n
            for n in schema["components"]["schemas"]
            if any(w in n.lower() for w in ("outcome", "experiment", "retentionmetric"))
        ]
    finally:
        application.router.routes[:] = routes
        application.openapi_schema = None


@pytest.mark.parametrize(
    "model",
    [
        oc.OutcomeSubmission,
        oc.OutcomeImportRequest,
        oc.AccountOutcome,
        oc.RetentionMetrics,
        oc.RetentionMetricsQuery,
        oc.MeasurementCoverage,
        oc.LinkTaskOutcomeRequest,
        oc.TaskOutcomeAssociation,
    ],
    ids=lambda m: m.__name__,
)
def test_each_m4_contract_has_a_closed_json_schema_without_a_causal_claim(model):
    schema = model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert not _offending(json.dumps(schema))
