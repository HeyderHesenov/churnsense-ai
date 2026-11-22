"""The V2 rule engine and the per-currency revenue it reports.

Every rule is pinned at its boundaries from both sides, so moving a threshold or
turning ``<`` into ``<=`` fails here. Amounts are checked as exact decimal
strings against sums worked out by hand. Every snapshot is hand-built and
synthetic (``tests/workspace_snapshots.py``).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from churnsense.workspace import portfolio, risk, synthetic, workbench
from churnsense.workspace import workbench_schemas as out
from tests.workspace_snapshots import AS_OF, earlier, snap


def _codes(assessment: risk.Assessment) -> dict[str, str]:
    return {i.code: i.severity for i in assessment.indicators}


def _skipped(assessment: risk.Assessment) -> dict[str, str]:
    return {s.rule: s.reason for s in assessment.skipped}


def test_the_baseline_snapshot_has_no_signal():
    """The control: every rule below changes one field of this snapshot."""
    current = snap()
    result = risk.assess(current, [earlier(current, 30)])
    assert result.assessed and result.level == "no_signals"
    assert result.indicators == () and result.skipped == ()
    assert (result.method, result.method_version) == ("rules", risk.METHOD_VERSION)


# --- rule 1: payment --------------------------------------------------------------------------


def test_past_due_is_attention():
    result = risk.assess(snap(status="past_due"))
    assert _codes(result)["payment_past_due"] == "attention"
    assert result.level == "attention"
    indicator = result.indicators[0]
    assert indicator.evidence == (risk.Evidence("status", AS_OF, "past_due"),)


def test_active_is_not_a_payment_signal():
    assert "payment_past_due" not in _codes(risk.assess(snap(status="active")))


# --- rule 2: renewal --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [
        (0, "attention"),
        (1, "attention"),
        (30, "attention"),
        (31, "watch"),
        (60, "watch"),
        (61, None),
    ],
)
def test_renewal_windows(days, severity):
    result = risk.assess(snap(renewal_date=AS_OF + timedelta(days=days)))
    assert _codes(result).get("renewal_due") == severity
    if severity:
        indicator = result.indicators[0]
        assert (indicator.observed, indicator.unit) == (Decimal(days), "days")
        assert indicator.observed_on == AS_OF


def test_no_renewal_date_is_stated_not_guessed():
    result = risk.assess(snap(renewal_date=None))
    assert "renewal_due" not in _codes(result)
    assert "No renewal date" in _skipped(result)["renewal_due"]


def test_a_renewal_before_the_snapshot_is_not_urgency():
    result = risk.assess(snap(renewal_date=AS_OF - timedelta(days=1)))
    assert "renewal_due" not in _codes(result)
    assert "before the snapshot" in _skipped(result)["renewal_due"]


@pytest.mark.parametrize("status", ["trialing", "paused", "canceled"])
def test_renewal_urgency_is_never_read_from_an_account_not_assessed(status):
    s = snap(status=status, mrr=Decimal(0), renewal_date=AS_OF + timedelta(days=3))
    assert risk.assess(s).indicators == ()
    assert risk.days_to_renewal(s) is None


# --- rule 3: inactivity -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [(0, None), (13, None), (14, "watch"), (29, "watch"), (30, "attention"), (90, "attention")],
)
def test_inactivity_windows(days, severity):
    result = risk.assess(snap(last_activity_at=AS_OF - timedelta(days=days)))
    assert _codes(result).get("inactivity") == severity
    if severity:
        assert result.indicators[0].observed == Decimal(days)


def test_no_last_activity_is_stated():
    result = risk.assess(snap(last_activity_at=None))
    assert "inactivity" not in _codes(result)
    assert "No last activity" in _skipped(result)["inactivity"]


def test_activity_after_the_snapshot_is_refused_as_evidence():
    result = risk.assess(snap(last_activity_at=AS_OF + timedelta(days=1)))
    assert "inactivity" not in _codes(result)
    assert "after the snapshot" in _skipped(result)["inactivity"]


# --- rule 4: declining usage ------------------------------------------------------------------


@pytest.mark.parametrize("metric", ["sessions_30d", "key_actions_30d"])
@pytest.mark.parametrize(
    ("before", "now", "severity"),
    [
        (100, 86, None),  # 14%
        (100, 85, "watch"),  # exactly 15%
        (100, 71, "watch"),  # 29%
        (100, 70, "attention"),  # exactly 30%
        (100, 0, "attention"),
        (100, 100, None),
        (100, 140, None),  # growth
        (3, 2, "attention"),  # 33.3%: no rounding decides it
        (20, 17, "watch"),  # exactly 15% of a small count
        (7, 6, None),  # 14.28...%
    ],
)
def test_decline_thresholds(metric, before, now, severity):
    current = snap(**{metric: now})
    result = risk.assess(current, [earlier(current, 30, **{metric: before})])
    code = risk._TREND_CODES[metric]
    assert _codes(result).get(code) == severity
    other = "key_actions_decline" if code == "sessions_decline" else "sessions_decline"
    assert other not in _codes(result)
    if severity:
        indicator = next(i for i in result.indicators if i.code == code)
        assert indicator.compared_with == AS_OF - timedelta(days=30)
        assert [e.value for e in indicator.evidence] == [before, now]


def test_a_shown_percentage_never_crosses_its_threshold():
    """29.99...% is cut to 29.9, never rounded to 30.0, and reads as watch."""
    current = snap(sessions_30d=7001)
    result = risk.assess(current, [earlier(current, 30, sessions_30d=10_000)])
    indicator = next(i for i in result.indicators if i.code == "sessions_decline")
    assert (indicator.severity, indicator.observed) == ("watch", Decimal("29.9"))


def test_a_previous_count_of_zero_is_not_a_baseline():
    current = snap(sessions_30d=0)
    result = risk.assess(current, [earlier(current, 30, sessions_30d=0)])
    assert "sessions_decline" not in _codes(result)
    assert "had no sessions" in _skipped(result)["sessions_decline"]
    current = snap(sessions_30d=50)
    result = risk.assess(current, [earlier(current, 30, sessions_30d=0)])
    assert "sessions_decline" not in _codes(result)
    assert "had no sessions" in _skipped(result)["sessions_decline"]


@pytest.mark.parametrize(("now", "before"), [(None, 100), (100, None)])
def test_a_missing_count_is_no_trend(now, before):
    current = snap(key_actions_30d=now)
    result = risk.assess(current, [earlier(current, 30, key_actions_30d=before)])
    assert "key_actions_decline" not in _codes(result)
    assert "not given for both" in _skipped(result)["key_actions_decline"]


def test_no_earlier_snapshot_is_insufficient_history():
    result = risk.assess(snap(sessions_30d=0))
    assert result.trend_baseline is None
    skipped = _skipped(result)
    assert "No earlier snapshot" in skipped["sessions_decline"]
    assert "No earlier snapshot" in skipped["key_actions_decline"]


@pytest.mark.parametrize(("gap", "compared"), [(29, False), (30, True), (45, True), (46, False)])
def test_only_the_preceding_30_day_period_is_compared(gap, compared):
    """29 days apart the windows overlap; 46 days apart they are not adjacent periods."""
    current = snap(sessions_30d=10)
    result = risk.assess(current, [earlier(current, gap, sessions_30d=100)])
    assert ("sessions_decline" in _codes(result)) is compared
    assert result.trend_baseline == (current.as_of_date - timedelta(days=gap) if compared else None)


def test_the_latest_comparable_snapshot_is_the_baseline():
    current = snap(sessions_30d=60)
    history = [
        earlier(current, 60, sessions_30d=1000),
        earlier(current, 35, sessions_30d=100),
        earlier(current, 10, sessions_30d=5),
    ]
    result = risk.assess(current, history)
    assert result.trend_baseline == AS_OF - timedelta(days=35)
    assert _codes(result)["sessions_decline"] == "attention"  # 40% against the 35-day one


def test_of_two_comparable_snapshots_the_later_is_the_baseline():
    current = snap(sessions_30d=80)
    history = [earlier(current, 44, sessions_30d=80), earlier(current, 31, sessions_30d=200)]
    result = risk.assess(current, history)
    assert result.trend_baseline == AS_OF - timedelta(days=31)
    assert _codes(result)["sessions_decline"] == "attention"
    result = risk.assess(current, history[::-1])  # the order given does not matter
    assert result.trend_baseline == AS_OF - timedelta(days=31)


# --- rule 5: active-user coverage -------------------------------------------------------------


@pytest.mark.parametrize(
    ("users", "seats", "severity"),
    [
        (9, 100, "attention"),
        (10, 100, "watch"),  # exactly 10%
        (24, 100, "watch"),
        (25, 100, None),  # exactly 25%
        (0, 7, "attention"),
        (1, 10, "watch"),
        (1, 4, None),
        (12, 10, None),  # more active users than seats is not low coverage
    ],
)
def test_coverage_thresholds(users, seats, severity):
    result = risk.assess(snap(active_users=users, seats_purchased=seats))
    assert _codes(result).get("low_active_user_coverage") == severity


@pytest.mark.parametrize(("users", "seats"), [(5, 0), (5, None), (None, 10)])
def test_coverage_needs_both_values_and_seats_above_zero(users, seats):
    result = risk.assess(snap(active_users=users, seats_purchased=seats))
    assert "low_active_user_coverage" not in _codes(result)
    assert "low_active_user_coverage" in _skipped(result)


def test_coverage_is_named_as_coverage_not_utilisation():
    indicator = risk.assess(snap(active_users=1, seats_purchased=50)).indicators[0]
    assert indicator.title == "Low active-user coverage"
    assert "utilisation" not in indicator.explanation.lower()


# --- eligibility and aggregation --------------------------------------------------------------


@pytest.mark.parametrize("status", ["trialing", "paused", "canceled"])
def test_an_account_without_a_paid_subscription_is_not_assessed(status):
    s = snap(
        status=status,
        mrr=Decimal(0),
        last_activity_at=AS_OF - timedelta(days=200),
        active_users=0,
        canceled_at=AS_OF if status == "canceled" else None,
    )
    result = risk.assess(s, [earlier(s, 30, status="active", sessions_30d=1000)])
    assert not result.assessed
    assert result.level is None
    assert result.indicators == () and result.skipped == ()
    assert result.not_assessed_reason == risk.NOT_ASSESSED_REASONS[status]
    assert workbench.next_step_context(result).startswith("No review indicated")
    assert workbench.assessment_out(result).eligibility == "not_assessed"


def test_any_attention_makes_attention_and_watch_alone_makes_watch():
    watch_only = snap(renewal_date=AS_OF + timedelta(days=45), active_users=2, seats_purchased=10)
    result = risk.assess(watch_only)
    assert result.level == "watch"
    assert set(_codes(result).values()) == {"watch"}
    mixed = replace(watch_only, status="past_due")
    result = risk.assess(mixed)
    assert result.level == "attention"
    assert [i.code for i in result.indicators] == [
        "payment_past_due",
        "renewal_due",
        "low_active_user_coverage",
    ]


def test_indicators_are_listed_attention_first_then_in_rule_order():
    current = snap(
        renewal_date=AS_OF + timedelta(days=50),  # watch
        last_activity_at=AS_OF - timedelta(days=40),  # attention
        active_users=1,
        seats_purchased=5,  # watch
    )
    result = risk.assess(current)
    assert [(i.code, i.severity) for i in result.indicators] == [
        ("inactivity", "attention"),
        ("renewal_due", "watch"),
        ("low_active_user_coverage", "watch"),
    ]
    assert risk.assess(current) == result  # deterministic


def test_every_assessment_names_its_method_and_has_no_probability():
    current = snap(status="past_due")
    for result in (risk.assess(current), risk.assess(snap(status="paused", mrr=Decimal(0)))):
        assert result.method == "rules" and result.method_version == risk.METHOD_VERSION
        payload = workbench.assessment_out(result).model_dump_json()
        assert "probab" not in payload and "score" not in payload
    names = {f.name for f in dataclasses.fields(risk.Assessment)} | set(out.Assessment.model_fields)
    assert not {n for n in names if "probab" in n or "score" in n}


def test_every_indicator_explains_itself():
    current = snap(
        status="past_due",
        renewal_date=AS_OF + timedelta(days=5),
        last_activity_at=AS_OF - timedelta(days=31),
        active_users=1,
        seats_purchased=20,
        sessions_30d=10,
        key_actions_30d=10,
    )
    result = risk.assess(current, [earlier(current, 30, sessions_30d=100, key_actions_30d=400)])
    assert set(_codes(result)) == set(risk.RULE_ORDER)
    for indicator in result.indicators:
        assert indicator.explanation and indicator.title
        assert indicator.rule == risk.RULES.text(indicator.code)
        assert indicator.observed_on == AS_OF
        assert indicator.evidence
    assert "No user has been active for 31 days" in _explanation(result, "inactivity")
    assert "from 100 to 10 (90.0% lower)" in _explanation(result, "sessions_decline")
    assert "1 active user for 20 purchased seats: 5.0%" in _explanation(
        result, "low_active_user_coverage"
    )


def _explanation(result, code):
    return next(i.explanation for i in result.indicators if i.code == code)


def test_the_rule_text_is_written_from_the_thresholds():
    moved = risk.Rules(renewal_attention_days=14, renewal_watch_days=90)
    assert moved.text("renewal_due") == (
        "Attention when the renewal is 0 to 14 days away; watch at 15 to 90 days."
    )
    result = risk.assess(snap(renewal_date=AS_OF + timedelta(days=60)), rules=moved)
    assert _codes(result) == {"renewal_due": "watch"}
    assert set(risk.RULES.describe()) == set(risk.RULE_ORDER)


# --- never the future -------------------------------------------------------------------------


def test_a_later_or_foreign_snapshot_is_refused():
    current = snap()
    with pytest.raises(ValueError, match="not dated before"):
        risk.assess(current, [earlier(current, -30)])
    with pytest.raises(ValueError, match="not dated before"):
        risk.assess(current, [current])
    with pytest.raises(ValueError, match="another account"):
        risk.assess(current, [earlier(current, 30, account_external_id="DEMO-ACC-900002")])


def test_a_history_is_assessed_without_its_future():
    account = synthetic.generate_account(7)
    before = risk.assess_history(account.snapshots)
    # Rewrite the latest snapshot completely: no earlier assessment may move.
    changed = replace(
        account.latest, status="past_due", sessions_30d=0, key_actions_30d=0, active_users=0
    )
    after = risk.assess_history((*account.snapshots[:-1], changed))
    assert before[:-1] == after[:-1]
    assert [a.as_of_date for a in before] == [s.as_of_date for s in account.snapshots]
    assert before[0].trend_baseline is None
    for a in before[1:]:
        assert a.trend_baseline is None or a.trend_baseline < a.as_of_date


def test_history_assessments_match_assessing_each_prefix():
    for index in range(1, 60):
        account = synthetic.generate_account(index)
        history = risk.assess_history(account.snapshots)
        for k, s in enumerate(account.snapshots):
            assert history[k] == risk.assess(s, account.snapshots[:k])


# --- money: exact, and per currency -----------------------------------------------------------


def _entry(account: str, currency: str, mrr: str, status: str = "active", **changes):
    s = snap(
        account_external_id=account, currency=currency, mrr=Decimal(mrr), status=status, **changes
    )
    return s, risk.assess(s)


def _fixture():
    """Every account's level is fixed by one field; each sum below is worked out by hand."""
    soon = AS_OF + timedelta(days=10)
    later = AS_OF + timedelta(days=45)
    return [
        _entry("DEMO-ACC-900001", "USD", "0.10", status="past_due"),  # attention
        _entry("DEMO-ACC-900002", "USD", "0.20", renewal_date=soon),  # attention
        _entry("DEMO-ACC-900003", "USD", "1000.05", renewal_date=later),  # watch
        _entry("DEMO-ACC-900004", "USD", "99.99"),  # no signals
        _entry("DEMO-ACC-900005", "USD", "0", status="canceled", canceled_at=AS_OF),
        _entry("DEMO-ACC-900006", "EUR", "250.5", renewal_date=later),  # watch
        _entry("DEMO-ACC-900007", "EUR", "0.0001"),  # no signals
        _entry("DEMO-ACC-900008", "EUR", "0", status="trialing"),
        _entry("DEMO-ACC-900009", "GBP", "0", status="paused"),
    ]


def test_currency_totals_are_exact_and_never_mixed():
    totals = {t.currency: t for t in portfolio.by_currency(_fixture())}
    assert list(totals) == ["EUR", "GBP", "USD"]
    usd, eur, gbp = totals["USD"], totals["EUR"], totals["GBP"]
    assert str(usd.attention_mrr) == "0.30"  # 0.1 + 0.2, exactly
    assert str(usd.watch_mrr) == "1000.05"
    assert str(usd.exposed_mrr) == "1000.35"
    assert str(usd.no_signals_mrr) == "99.99"
    assert str(usd.active_mrr) == "1100.34"
    assert (usd.accounts, usd.eligible, usd.excluded) == (5, 4, 1)
    assert dict(usd.levels) == {"attention": 2, "watch": 1, "no_signals": 1}
    assert str(eur.attention_mrr) == "0"
    assert str(eur.exposed_mrr) == "250.5"
    assert str(eur.active_mrr) == "250.5001"
    assert (eur.accounts, eur.eligible, eur.excluded) == (3, 2, 1)
    assert (gbp.accounts, gbp.eligible, str(gbp.active_mrr), str(gbp.exposed_mrr)) == (
        1,
        0,
        "0",
        "0",
    )


def test_exposed_is_attention_plus_watch_and_active_is_the_three_levels():
    for t in portfolio.by_currency(
        (a.latest, risk.assess(a.latest, a.snapshots[:-1])) for a in synthetic.generate()
    ):
        assert t.exposed_mrr == t.attention_mrr + t.watch_mrr
        assert t.active_mrr == t.attention_mrr + t.watch_mrr + t.no_signals_mrr
        assert all(isinstance(v, Decimal) for v in (t.active_mrr, t.exposed_mrr))


def test_a_total_refuses_another_currency_or_another_snapshot():
    s, a = _fixture()[0]
    total = portfolio.CurrencyTotals("EUR")
    with pytest.raises(ValueError, match="another currency"):
        total.add(s, a)
    other = replace(s, as_of_date=AS_OF - timedelta(days=1))
    with pytest.raises(ValueError, match="not this snapshot"):
        portfolio.CurrencyTotals("USD").add(other, a)


def test_a_sum_that_would_round_raises_instead():
    total = portfolio.CurrencyTotals("USD")
    huge = Decimal("9" * 60)
    s = snap(mrr=huge)
    total.add(s, risk.assess(s))
    with pytest.raises(ArithmeticError):
        total.add(s, risk.assess(s))


def test_the_summary_matches_the_hand_worked_fixture():
    book = workbench.Book(
        dataset=workbench.dataset_info(),
        entries=tuple(workbench.Entry(s, a) for s, a in _fixture()),
    )
    summary = workbench.portfolio_summary(book).model_dump(mode="json")
    assert (summary["total_accounts"], summary["eligible_accounts"]) == (9, 6)
    assert summary["excluded_accounts"] == 3
    assert summary["not_assessed"] == {"trialing": 1, "paused": 1, "canceled": 1}
    assert summary["levels"] == {"attention": 2, "watch": 2, "no_signals": 2}
    assert [c["currency"] for c in summary["currencies"]] == ["EUR", "GBP", "USD"]
    usd = summary["currencies"][2]
    assert {k: usd[k] for k in ("active_mrr", "attention_mrr", "watch_mrr", "exposed_mrr")} == {
        "active_mrr": "1100.34",
        "attention_mrr": "0.30",
        "watch_mrr": "1000.05",
        "exposed_mrr": "1000.35",
    }
    assert summary["renewals"] == [
        {"from_days": 0, "to_days": 30, "severity": "attention", "accounts": 1},
        {"from_days": 31, "to_days": 60, "severity": "watch", "accounts": 2},
    ]
    assert "not predicted revenue loss" in summary["definitions"]["exposed_mrr"]
    # No amount anywhere in the summary adds two currencies.
    assert not any("mrr" in key for key in summary if key != "currencies")


_MONEY_MODULES = (portfolio, risk, synthetic, workbench)


@pytest.mark.parametrize("module", _MONEY_MODULES, ids=lambda m: m.__name__)
def test_no_float_enters_the_money_code(module):
    tree = ast.parse(inspect.getsource(module))
    floats = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Constant) and isinstance(node.value, float))
        or (isinstance(node, ast.Name) and node.id == "float")
    ]
    assert floats == []


def test_every_amount_in_a_response_model_is_a_decimal():
    for model in (out.CurrencySummary, out.AccountRow, out.Subscription, out.SnapshotRecord):
        for name, info in model.model_fields.items():
            if "mrr" in name:
                assert info.annotation is Decimal, (model.__name__, name)
