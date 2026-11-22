"""The Retention Workbench's preview dataset: synthetic B2B SaaS accounts, generated.

**Every value here is made up.** No row is read from anywhere - not the IBM
telecom dataset, not a model artifact, not a report, not an upload - and none
describes a real company or person. Account IDs read ``DEMO-ACC-000001``, names
read ``Synthetic <word> <word> #1``, and there is no email address, phone number,
contact or address field at all. Nothing is a label: there is no churn outcome,
no probability, no campaign and no result. The data exists so the rule engine
(``risk``) has accounts to read; it is never evidence of how a model performs or
of what happens to a business.

**Pure and reproducible.** ``generate`` is a function of ``seed``, ``accounts``
and ``as_of`` alone: the same three give the same accounts, value for value, in
any process. Each account draws from its own ``random.Random``, seeded from the
seed, the generator version and the account's number, so one account can be
generated without the others (``find``) and is the same whatever ``accounts`` is.
Nothing is cached, stored or shared between calls.

**Shape.** Each account has ``SNAPSHOTS`` snapshots, ``INTERVAL_DAYS`` apart,
the last on ``as_of``: an account at one date each, in the canonical contract
(``contract``, ``snapshot.Snapshot``). Each snapshot's 30-day usage window ends
on its own date, so consecutive windows are adjacent and never overlap. Every
snapshot passes the Data Hub's own validation with no warning
(``tests/test_workspace_synthetic.py`` runs them through it).

**Variation** comes from a handful of recipes (``PATTERNS``): steady and
expanding usage, declining usage, a recent stop in activity, an overdue payment,
an upcoming renewal, low active-user coverage, and trialing, paused and
canceled accounts. Renewals are yearly, on the anniversary of ``customer_since``,
so every paid recipe also meets the renewal rule by chance. The recipe is the
generator's own; it is never served, since it is not something anyone observed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from typing import Final

from churnsense.workspace import contract
from churnsense.workspace.snapshot import Snapshot

GENERATOR_VERSION: Final = "synthetic_saas.v1"
#: The documented defaults the Workbench serves: change one and every number changes.
DEFAULT_SEED: Final = 20_261_122
DEFAULT_ACCOUNTS: Final = 500
DEFAULT_AS_OF: Final = date(2026, 10, 31)
#: The most accounts one call may generate; the ID has room for six digits.
MAX_ACCOUNTS: Final = 10_000
SNAPSHOTS: Final = 4
INTERVAL_DAYS: Final = 30
#: The 30-day usage window ``sessions_30d`` and ``key_actions_30d`` count over.
USAGE_WINDOW_DAYS: Final = 30

ID_PREFIX: Final = "DEMO-ACC-"
_ID = re.compile(rf"{re.escape(ID_PREFIX)}(\d{{6}})", re.ASCII)
#: Every date the generator writes stays inside the contract's range from these.
_EARLIEST_AS_OF: Final = date(1910, 1, 1)
_LATEST_AS_OF: Final = date(2190, 12, 31)

#: Recipe -> weight out of 100.
PATTERNS: Final[dict[str, int]] = {
    "stable": 24,
    "expanding": 6,
    "renewal_soon": 10,
    "declining": 12,
    "inactive": 8,
    "past_due": 8,
    "low_coverage": 8,
    "trialing": 7,
    "paused": 7,
    "canceled": 10,
}
CURRENCIES: Final[tuple[tuple[str, int], ...]] = (("USD", 70), ("EUR", 20), ("GBP", 10))
SEGMENTS: Final[tuple[str, ...]] = ("SMB", "Mid-Market", "Enterprise")
_SEGMENT_WEIGHTS: Final = (50, 35, 15)
_SIZE_BANDS: Final[dict[str, tuple[str, ...]]] = {
    "SMB": ("1-10", "11-50"),
    "Mid-Market": ("51-200", "201-500"),
    "Enterprise": ("501-1000", "1001-5000", "5001+"),
}
#: Seats purchased, and mrr in cents, by segment: (low, high), inclusive.
_SEATS: Final[dict[str, tuple[int, int]]] = {
    "SMB": (3, 40),
    "Mid-Market": (40, 300),
    "Enterprise": (300, 2_500),
}
_MRR_CENTS: Final[dict[str, tuple[int, int]]] = {
    "SMB": (4_900, 150_000),
    "Mid-Market": (150_000, 900_000),
    "Enterprise": (900_000, 6_000_000),
}
INDUSTRIES: Final[tuple[str, ...]] = (
    "Software",
    "Healthcare",
    "Financial services",
    "Retail",
    "Manufacturing",
    "Education",
    "Logistics",
    "Media",
    "Professional services",
)
_NAME_WORDS: Final = (
    ("Amber", "Basalt", "Cedar", "Cobalt", "Copper", "Ember", "Granite", "Harbor", "Indigo"),
    ("Juniper", "Kestrel", "Lumen", "Maple", "Nimbus", "Onyx", "Quartz", "Russet", "Willow"),
)
_NAME_NOUNS: Final = (
    "Analytics",
    "Logistics",
    "Labs",
    "Systems",
    "Works",
    "Health",
    "Learning",
    "Robotics",
    "Studios",
    "Supply",
)
#: How a paid account's usage period is shaped: users per seat (per mille), and each
#: active user's sessions and key actions in 30 days. Inclusive ranges.
_COVERAGE: Final = (450, 950)
_LOW_COVERAGE: Final = (30, 230)
_SESSIONS_PER_USER: Final = (12, 40)
_ACTIONS_PER_USER: Final = (30, 120)
#: Month-to-month jitter in percent: small enough that a steady account never
#: crosses the decline rule's lowest threshold (15%) between two snapshots.
_JITTER: Final = (97, 103)


@dataclass(frozen=True, slots=True)
class Account:
    """One synthetic account: its snapshots oldest first, the last on ``as_of``."""

    account_id: str
    pattern: str
    snapshots: tuple[Snapshot, ...]

    @property
    def latest(self) -> Snapshot:
        return self.snapshots[-1]


def account_id(index: int) -> str:
    return f"{ID_PREFIX}{index:06d}"


def account_index(value: str) -> int | None:
    """The account number in a synthetic ID, or None if it is not one."""
    match = _ID.fullmatch(value)
    return int(match.group(1)) if match else None


def snapshot_dates(as_of: date) -> tuple[date, ...]:
    """The snapshot dates, oldest first, ``INTERVAL_DAYS`` apart and ending on ``as_of``."""
    return tuple(as_of - timedelta(days=INTERVAL_DAYS * k) for k in range(SNAPSHOTS - 1, -1, -1))


def _check(accounts: int, seed: int, as_of: date) -> None:
    if not isinstance(accounts, int) or not 1 <= accounts <= MAX_ACCOUNTS:
        raise ValueError(f"accounts must be a whole number from 1 to {MAX_ACCOUNTS:,}")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a whole number of 0 or more")
    if not _EARLIEST_AS_OF <= as_of <= _LATEST_AS_OF:
        raise ValueError(f"as_of must be between {_EARLIEST_AS_OF} and {_LATEST_AS_OF}")


def _weighted(rng: Random, choices: tuple[tuple[str, int], ...]) -> str:
    """One choice by integer weight: no float enters the draw."""
    roll = rng.randrange(sum(weight for _, weight in choices))
    for value, weight in choices:
        if roll < weight:
            return value
        roll -= weight
    raise AssertionError("unreachable")


def _anniversary(since: date, year: int) -> date:
    try:
        return since.replace(year=year)
    except ValueError:  # 29 February in a year without one
        return date(year, 2, 28)


def _next_renewal(since: date, on_or_after: date) -> date:
    """The first yearly anniversary of ``since`` on or after ``on_or_after``."""
    renewal = _anniversary(since, on_or_after.year)
    return renewal if renewal >= on_or_after else _anniversary(since, on_or_after.year + 1)


def _between(rng: Random, low: date, high: date) -> date:
    return low + timedelta(days=rng.randint(0, (high - low).days))


@dataclass(frozen=True, slots=True)
class _Plan:
    """What one account is: drawn once, then written out at every snapshot date."""

    pattern: str
    currency: str
    segment: str
    size_band: str
    industry: str
    name: str
    mrr: Decimal
    seats: int | None
    users: int
    sessions_per_user: int
    actions_per_user: int
    customer_since: date | None
    renewal_known: bool
    statuses: tuple[str, ...]
    levels: tuple[int, ...]
    stopped: date | None
    canceled_at: date | None
    cancellation_reason: str | None


def _plan(rng: Random, index: int, dates: tuple[date, ...]) -> _Plan:
    as_of = dates[-1]
    pattern = _weighted(rng, tuple(PATTERNS.items()))
    currency = _weighted(rng, CURRENCIES)
    segment = _weighted(rng, tuple(zip(SEGMENTS, _SEGMENT_WEIGHTS, strict=True)))
    size_band = rng.choice(_SIZE_BANDS[segment])
    industry = rng.choice(INDUSTRIES)
    name = f"Synthetic {rng.choice(rng.choice(_NAME_WORDS))} {rng.choice(_NAME_NOUNS)} #{index}"
    cents = rng.randint(*_MRR_CENTS[segment])
    seats = rng.randint(*_SEATS[segment])
    coverage = rng.randint(*(_LOW_COVERAGE if pattern == "low_coverage" else _COVERAGE))
    sessions_per_user = rng.randint(*_SESSIONS_PER_USER)
    actions_per_user = rng.randint(*_ACTIONS_PER_USER)

    # Paid for at least 120 days before as_of, so before the first snapshot too.
    if pattern == "renewal_soon":
        renewal = as_of + timedelta(days=rng.randint(0, 60))
        since = _anniversary(renewal, renewal.year - rng.randint(1, 5))
    else:
        since = as_of - timedelta(days=rng.randint(120, 6 * 365))
    renewal_known = pattern == "renewal_soon" or rng.randrange(10) != 0

    statuses = ["active"] * SNAPSHOTS
    levels = [100] * SNAPSHOTS
    stopped: date | None = None
    canceled_at: date | None = None
    reason: str | None = None
    match pattern:
        case "expanding":
            step = rng.randint(3, 8)
            levels = [100 + step * k for k in range(SNAPSHOTS)]
        case "declining":
            first, second = rng.randint(0, 10), rng.randint(16, 60)
            levels[-2] = 100 - first
            levels[-1] = (100 - first) * (100 - second) // 100
        case "inactive":
            stopped = as_of - timedelta(days=rng.randint(14, 70))
        case "past_due":
            statuses[-1] = "past_due"
            if rng.randrange(10) < 3:
                statuses[-2] = "past_due"
        case "trialing":
            statuses = ["trialing"] * SNAPSHOTS
            since = None
            seats = None
            coverage = 1000
        case "paused":
            first = SNAPSHOTS - (2 if rng.randrange(10) < 4 else 1)
            statuses[first:] = ["paused"] * (SNAPSHOTS - first)
            stopped = dates[first] - timedelta(days=rng.randint(3, 45))
        case "canceled":
            first = SNAPSHOTS - rng.randint(1, 2)
            canceled_at = _between(rng, dates[first - 1] + timedelta(days=1), dates[first])
            statuses[first:] = ["canceled"] * (SNAPSHOTS - first)
            stopped = canceled_at - timedelta(days=rng.randint(0, 25))
            reason = rng.choice(contract.CANCELLATION_REASONS)
    users = rng.randint(1, 12) if seats is None else max(1, seats * coverage // 1000)
    return _Plan(
        pattern=pattern,
        currency=currency,
        segment=segment,
        size_band=size_band,
        industry=industry,
        name=name,
        mrr=Decimal(cents).scaleb(-2),
        seats=seats,
        users=users,
        sessions_per_user=sessions_per_user,
        actions_per_user=actions_per_user,
        customer_since=since,
        renewal_known=renewal_known,
        statuses=tuple(statuses),
        levels=tuple(levels),
        stopped=stopped,
        canceled_at=canceled_at,
        cancellation_reason=reason,
    )


def _snapshot(rng: Random, plan: _Plan, k: int, on: date, account: str) -> Snapshot:
    status = plan.statuses[k]
    paid = status in contract.PAID_STATUSES
    window_start = on - timedelta(days=USAGE_WINDOW_DAYS - 1)

    # The last active day on or before this date, and the share of the window it was active.
    last = on - timedelta(days=rng.randint(0, 3))
    active_days = USAGE_WINDOW_DAYS
    if plan.stopped is not None and plan.stopped <= last:
        last = plan.stopped
        active_days = max(0, (last - window_start).days + 1)
    if active_days:
        users = max(1, plan.users * plan.levels[k] * active_days // (100 * USAGE_WINDOW_DAYS))
        sessions = max(1, users * plan.sessions_per_user * rng.randint(*_JITTER) // 100)
        actions = users * plan.actions_per_user * rng.randint(*_JITTER) // 100
    else:
        users = sessions = actions = 0

    # A trial never bought seats; an account without a paid subscription pays for none.
    seats = None if plan.seats is None else (plan.seats if paid else 0)
    renewal = (
        _next_renewal(plan.customer_since, on)
        if paid and plan.renewal_known and plan.customer_since is not None
        else None
    )
    canceled = status == "canceled"
    return Snapshot(
        account_external_id=account,
        as_of_date=on,
        status=status,
        mrr=plan.mrr if paid else Decimal(0),
        currency=plan.currency,
        account_name=plan.name,
        customer_since=plan.customer_since,
        renewal_date=renewal,
        canceled_at=plan.canceled_at if canceled else None,
        cancellation_reason=plan.cancellation_reason if canceled else None,
        segment=plan.segment,
        company_size_band=plan.size_band,
        industry=plan.industry,
        active_users=users,
        seats_purchased=seats,
        sessions_30d=sessions,
        key_actions_30d=actions,
        last_activity_at=last,
        usage_period_start=window_start,
        usage_period_end=on,
    )


def generate_account(
    index: int, *, seed: int = DEFAULT_SEED, as_of: date = DEFAULT_AS_OF
) -> Account:
    """Account number ``index`` (from 1) of the dataset ``seed`` describes on ``as_of``."""
    _check(index, seed, as_of)
    rng = Random(f"churnsense.workspace.synthetic/{GENERATOR_VERSION}/{seed}/{index}")
    dates = snapshot_dates(as_of)
    plan = _plan(rng, index, dates)
    identifier = account_id(index)
    return Account(
        account_id=identifier,
        pattern=plan.pattern,
        snapshots=tuple(_snapshot(rng, plan, k, on, identifier) for k, on in enumerate(dates)),
    )


def iter_accounts(
    accounts: int = DEFAULT_ACCOUNTS, *, seed: int = DEFAULT_SEED, as_of: date = DEFAULT_AS_OF
) -> Iterator[Account]:
    _check(accounts, seed, as_of)
    for index in range(1, accounts + 1):
        yield generate_account(index, seed=seed, as_of=as_of)


def generate(
    accounts: int = DEFAULT_ACCOUNTS, *, seed: int = DEFAULT_SEED, as_of: date = DEFAULT_AS_OF
) -> tuple[Account, ...]:
    """Every account of the dataset, in ID order."""
    return tuple(iter_accounts(accounts, seed=seed, as_of=as_of))


def find(
    identifier: str,
    accounts: int = DEFAULT_ACCOUNTS,
    *,
    seed: int = DEFAULT_SEED,
    as_of: date = DEFAULT_AS_OF,
) -> Account | None:
    """The account with this ID, generated on its own, or None if the dataset has none."""
    _check(accounts, seed, as_of)
    index = account_index(identifier)
    if index is None or not 1 <= index <= accounts:
        return None
    return generate_account(index, seed=seed, as_of=as_of)
