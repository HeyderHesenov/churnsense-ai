"""Hand-built canonical snapshots for the V2 rule and Workbench tests.

Every value is synthetic. ``snap`` is an active account on ``AS_OF`` with no rule
signal at all: steady usage, recent activity, full coverage and a renewal far
off; each test changes only the fields its rule reads.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from churnsense.workspace.snapshot import Snapshot

AS_OF = date(2026, 10, 31)


def snap(**changes) -> Snapshot:
    base = Snapshot(
        account_external_id="DEMO-ACC-900001",
        as_of_date=AS_OF,
        status="active",
        mrr=Decimal("100.00"),
        currency="USD",
        account_name="Synthetic Test Account",
        customer_since=date(2024, 3, 15),
        renewal_date=AS_OF + timedelta(days=200),
        segment="SMB",
        company_size_band="11-50",
        industry="Software",
        active_users=10,
        seats_purchased=10,
        sessions_30d=100,
        key_actions_30d=400,
        last_activity_at=AS_OF,
        usage_period_start=AS_OF - timedelta(days=29),
        usage_period_end=AS_OF,
    )
    return replace(base, **changes)


def earlier(current: Snapshot, days: int, **changes) -> Snapshot:
    """The same account ``days`` before ``current``, its usage window ending then."""
    on = current.as_of_date - timedelta(days=days)
    fields = {
        "as_of_date": on,
        "last_activity_at": on,
        "usage_period_start": on - timedelta(days=29),
        "usage_period_end": on,
    }
    return replace(current, **{**fields, **changes})
