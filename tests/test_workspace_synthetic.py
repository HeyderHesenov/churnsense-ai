"""The Workbench's synthetic dataset: reproducible, internally consistent, and plainly fake.

Every snapshot is run through the Data Hub's own validation, so the generator
cannot drift from the canonical contract the preview enforces.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import os
import random
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from churnsense.config import PROJECT_ROOT
from churnsense.workspace import contract, privacy, risk, synthetic
from churnsense.workspace.preview import Limits, build_preview
from churnsense.workspace.snapshot import Snapshot

ACCOUNTS = synthetic.generate()


def _digest(accounts) -> str:
    return hashlib.sha256(repr(accounts).encode()).hexdigest()


def _csv(accounts) -> bytes:
    names = [f.name for f in contract.FIELDS]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(names)
    for account in accounts:
        for s in account.snapshots:
            writer.writerow(["" if v is None else str(v) for v in s.as_dict().values()])
    return buffer.getvalue().encode()


# --- reproducible ------------------------------------------------------------------------------


def test_the_same_seed_count_and_date_give_the_same_accounts():
    assert synthetic.generate() == ACCOUNTS
    assert len(ACCOUNTS) == synthetic.DEFAULT_ACCOUNTS == 500


def test_another_process_with_another_hash_seed_gives_the_same_accounts():
    code = (
        "import hashlib; from churnsense.workspace import synthetic; "
        "print(hashlib.sha256(repr(synthetic.generate()).encode()).hexdigest())"
    )
    for hash_seed in ("0", "12345"):
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            cwd=PROJECT_ROOT,
            timeout=120,
            check=True,
        )
        assert done.stdout.strip() == _digest(ACCOUNTS)


def test_another_seed_gives_other_accounts():
    other = synthetic.generate(seed=synthetic.DEFAULT_SEED + 1)
    assert [a.account_id for a in other] == [a.account_id for a in ACCOUNTS]
    different = sum(a.snapshots != b.snapshots for a, b in zip(ACCOUNTS, other, strict=True))
    assert different > 450


def test_another_date_moves_every_snapshot_date():
    later = synthetic.DEFAULT_AS_OF + timedelta(days=7)
    moved = synthetic.generate(20, as_of=later)
    assert {a.latest.as_of_date for a in moved} == {later}
    assert [a.pattern for a in moved] == [a.pattern for a in ACCOUNTS[:20]]


def test_an_account_is_the_same_whatever_the_count_and_alone():
    assert synthetic.generate(10) == ACCOUNTS[:10]
    assert synthetic.find("DEMO-ACC-000123") == ACCOUNTS[122]
    assert synthetic.generate_account(500) == ACCOUNTS[-1]


@pytest.mark.parametrize(
    "identifier",
    [
        "DEMO-ACC-000000",
        "DEMO-ACC-000501",
        "DEMO-ACC-12",
        "demo-acc-000001",
        "DEMO-ACC-000001 ",
        "",
    ],
)
def test_an_id_outside_the_dataset_is_not_found(identifier):
    assert synthetic.find(identifier) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"accounts": 0},
        {"accounts": synthetic.MAX_ACCOUNTS + 1},
        {"seed": -1},
        {"as_of": synthetic.DEFAULT_AS_OF.replace(year=1900)},
    ],
)
def test_out_of_range_arguments_are_refused(kwargs):
    with pytest.raises(ValueError):
        synthetic.generate(**kwargs)


def test_the_generator_leaves_the_global_random_state_alone():
    state = random.getstate()
    synthetic.generate(50)
    assert random.getstate() == state


# --- shape -------------------------------------------------------------------------------------


def test_every_account_has_four_snapshots_thirty_days_apart_ending_on_the_date():
    dates = synthetic.snapshot_dates(synthetic.DEFAULT_AS_OF)
    assert len(dates) == synthetic.SNAPSHOTS == 4
    assert dates[-1] == synthetic.DEFAULT_AS_OF
    assert all((b - a).days == synthetic.INTERVAL_DAYS for a, b in pairwise(dates))
    for account in ACCOUNTS:
        assert tuple(s.as_of_date for s in account.snapshots) == dates
        assert {s.account_external_id for s in account.snapshots} == {account.account_id}
        for s in account.snapshots:
            assert s.usage_period_end == s.as_of_date
            assert (s.usage_period_end - s.usage_period_start).days == 29


def test_ids_and_names_are_unique_and_unmistakably_synthetic():
    ids = [a.account_id for a in ACCOUNTS]
    assert ids == [f"DEMO-ACC-{i:06d}" for i in range(1, 501)]
    names = [a.latest.account_name for a in ACCOUNTS]
    assert len(set(names)) == len(names)
    for i, name in enumerate(names, start=1):
        assert name.startswith("Synthetic ") and name.endswith(f" #{i}")


def test_no_value_is_personal_or_contact_data():
    assert not [f.name for f in dataclasses.fields(Snapshot) if privacy.sensitive_header(f.name)]
    for account in ACCOUNTS:
        for s in account.snapshots:
            for value in s.as_dict().values():
                if isinstance(value, str):
                    assert "@" not in value and "http" not in value.lower()
                    assert not privacy.looks_like_personal_identifier(value)


def test_the_record_is_the_contract_and_nothing_else():
    """No label, outcome, probability or score: the snapshot holds the contract's fields only."""
    assert [f.name for f in dataclasses.fields(Snapshot)] == [f.name for f in contract.FIELDS]
    assert not {"pattern"} & {f.name for f in dataclasses.fields(Snapshot)}


def test_every_snapshot_passes_the_data_hub_validation_without_a_warning():
    response = build_preview(_csv(ACCOUNTS), Limits(max_columns=50, max_rows=10_000))
    assert response.status == "valid"
    assert response.summary.accepted == 500 * 4
    assert response.summary.rejected == 0
    assert response.summary.warnings == 0
    # The preview's own per-date totals agree with the snapshots to the cent.
    latest = {
        (t.currency): t.mrr
        for t in response.summary.mrr_totals
        if t.as_of_date == synthetic.DEFAULT_AS_OF
    }
    expected: dict[str, Decimal] = {}
    for a in ACCOUNTS:
        expected[a.latest.currency] = expected.get(a.latest.currency, Decimal(0)) + a.latest.mrr
    assert latest == expected


def test_money_is_decimal_with_cents_and_mrr_follows_the_status():
    for account in ACCOUNTS:
        paid = {s.mrr for s in account.snapshots if s.status in contract.PAID_STATUSES}
        assert len(paid) <= 1  # one plan price per account
        for s in account.snapshots:
            assert isinstance(s.mrr, Decimal)
            if s.status in contract.PAID_STATUSES:
                assert s.mrr > 0 and s.mrr.as_tuple().exponent == -2
            else:
                assert s.mrr == 0 and s.renewal_date is None
    assert {a.latest.currency for a in ACCOUNTS} == {"USD", "EUR", "GBP"}


def test_each_history_is_consistent_over_time():
    for account in ACCOUNTS:
        first = account.snapshots[0]
        for s in account.snapshots:
            for name in ("account_name", "currency", "segment", "industry", "customer_since"):
                assert getattr(s, name) == getattr(first, name)
            if s.status == "canceled":
                assert s.canceled_at is not None and s.cancellation_reason is not None
                assert s.customer_since <= s.canceled_at <= s.as_of_date
            else:
                assert s.canceled_at is None and s.cancellation_reason is None
            # No activity in the window exactly when the last activity is before it.
            idle = s.last_activity_at < s.usage_period_start
            assert idle == (s.sessions_30d == 0) == (s.active_users == 0)
            if idle:
                assert s.key_actions_30d == 0
            if s.renewal_date is not None:
                assert s.as_of_date <= s.renewal_date <= s.as_of_date + timedelta(days=366)
            if s.customer_since is not None:
                assert s.customer_since <= account.snapshots[0].as_of_date
        statuses = [s.status for s in account.snapshots]
        if "canceled" in statuses:  # once canceled, it stays canceled
            assert statuses[statuses.index("canceled") :] == ["canceled"] * (
                len(statuses) - statuses.index("canceled")
            )
        if "trialing" in statuses:
            assert set(statuses) == {"trialing"}
            assert first.customer_since is None and first.seats_purchased is None


# --- variety the rules can use -----------------------------------------------------------------


def test_the_default_dataset_has_every_pattern_status_level_and_signal():
    assert set(Counter(a.pattern for a in ACCOUNTS)) == set(synthetic.PATTERNS)
    assert {a.latest.status for a in ACCOUNTS} == set(contract.STATUSES)
    assessed = [risk.assess(a.latest, a.snapshots[:-1]) for a in ACCOUNTS]
    assert {a.level for a in assessed} == {"attention", "watch", "no_signals", None}
    signals = {(i.code, i.severity) for a in assessed for i in a.indicators}
    assert signals == {
        (code, severity) for code in risk.RULE_ORDER for severity in ("attention", "watch")
    } - {("payment_past_due", "watch")}


def test_steady_accounts_show_no_usage_signal():
    """The jitter is too small to cross the decline rule's lowest threshold."""
    usage = {"sessions_decline", "key_actions_decline", "inactivity", "low_active_user_coverage"}
    for account in ACCOUNTS:
        if account.pattern in {"stable", "expanding", "renewal_soon"}:
            result = risk.assess(account.latest, account.snapshots[:-1])
            assert not usage & {i.code for i in result.indicators}, account.account_id
