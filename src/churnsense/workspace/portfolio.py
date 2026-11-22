"""Recurring revenue attached to the rule signals, per currency, in exact decimals.

**Never across currencies.** Every amount belongs to one currency's record; no
total adds USD to EUR. Counts of accounts may be added across currencies; amounts
may not.

**Exact.** Every sum runs in ``validate.MONEY``, the Data Hub's decimal context,
which raises rather than round. No amount is ever a float.

**What exposed MRR is.** ``exposed_mrr = attention_mrr + watch_mrr``: the gross
monthly recurring revenue of the accounts that show at least one attention or
watch signal on their latest snapshot. It is *not* predicted revenue loss,
expected churn loss, revenue saved, retention impact or recoverable revenue;
it says how much recurring revenue sits with the accounts the rules point at,
and nothing about what will happen to it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from churnsense.workspace.risk import ASSESSED_STATUSES, Assessment
from churnsense.workspace.snapshot import Snapshot
from churnsense.workspace.validate import MONEY

EXPOSED_MRR_DEFINITION = (
    "Exposed MRR is attention MRR plus watch MRR: the gross monthly recurring revenue of the "
    "accounts showing at least one attention or watch signal. It is not predicted revenue "
    "loss, expected churn loss, revenue saved, retention impact or recoverable revenue."
)
ACTIVE_MRR_DEFINITION = (
    "Active MRR is the monthly recurring revenue of the assessed accounts (status active or "
    "past_due): trialing, paused and canceled accounts have no active paid subscription."
)
CURRENCY_DEFINITION = (
    "Every amount is reported in its own currency; amounts in different currencies are never "
    "added together."
)


@dataclass(slots=True)
class CurrencyTotals:
    currency: str
    accounts: int = 0
    eligible: int = 0
    levels: Counter[str] = field(default_factory=Counter)
    active_mrr: Decimal = Decimal(0)
    attention_mrr: Decimal = Decimal(0)
    watch_mrr: Decimal = Decimal(0)
    no_signals_mrr: Decimal = Decimal(0)

    @property
    def excluded(self) -> int:
        return self.accounts - self.eligible

    @property
    def exposed_mrr(self) -> Decimal:
        return MONEY.add(self.attention_mrr, self.watch_mrr)

    def add(self, snapshot: Snapshot, assessment: Assessment) -> None:
        if snapshot.currency != self.currency:
            raise ValueError("an amount in another currency cannot join this total")
        if assessment.account_id != snapshot.account_external_id or (
            assessment.as_of_date != snapshot.as_of_date
        ):
            raise ValueError("the assessment is not this snapshot's")
        self.accounts += 1
        if not assessment.assessed:
            return
        assert assessment.level is not None and snapshot.status in ASSESSED_STATUSES
        self.eligible += 1
        self.levels[assessment.level] += 1
        self.active_mrr = MONEY.add(self.active_mrr, snapshot.mrr)
        match assessment.level:
            case "attention":
                self.attention_mrr = MONEY.add(self.attention_mrr, snapshot.mrr)
            case "watch":
                self.watch_mrr = MONEY.add(self.watch_mrr, snapshot.mrr)
            case "no_signals":
                self.no_signals_mrr = MONEY.add(self.no_signals_mrr, snapshot.mrr)


def by_currency(latest: Iterable[tuple[Snapshot, Assessment]]) -> list[CurrencyTotals]:
    """Each currency's totals over the accounts' latest snapshots, ordered by currency code."""
    totals: dict[str, CurrencyTotals] = {}
    for snapshot, assessment in latest:
        totals.setdefault(snapshot.currency, CurrencyTotals(snapshot.currency)).add(
            snapshot, assessment
        )
    return [totals[code] for code in sorted(totals)]
