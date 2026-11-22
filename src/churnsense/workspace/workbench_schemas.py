"""Customer Intelligence and the Retention Workbench: their responses, as OpenAPI states them.

Money is ``Decimal`` and leaves as a JSON string ("1250.50"), as in the Data Hub
preview (``schemas``): no amount passes through a binary float, and no total
adds two currencies. An assessment is rule-based and says so; no model here has
a field for a probability, and every list is bounded.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from churnsense.workspace.risk import IndicatorCode
from churnsense.workspace.schemas import AccountStatus

Level = Literal["attention", "watch", "no_signals"]
LevelFilter = Literal["attention", "watch", "no_signals", "not_assessed"]
SortKey = Literal["priority", "renewal_asc", "mrr_desc"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DatasetInfo(_Model):
    """Where the accounts come from: a deterministic generator, never a customer."""

    source: Literal["synthetic"] = "synthetic"
    generator_version: str
    seed: int
    accounts: int
    as_of_date: date
    snapshots_per_account: int
    snapshot_interval_days: int


class SnapshotRecord(_Model):
    """One account at one date, in the canonical contract ``account_snapshot.v0``."""

    account_external_id: str
    as_of_date: date
    status: AccountStatus
    mrr: Decimal
    currency: str
    account_name: str | None = None
    customer_since: date | None = None
    renewal_date: date | None = None
    canceled_at: date | None = None
    cancellation_reason: str | None = None
    segment: str | None = None
    company_size_band: str | None = None
    industry: str | None = None
    active_users: int | None = None
    seats_purchased: int | None = None
    sessions_30d: int | None = None
    key_actions_30d: int | None = None
    last_activity_at: date | None = None
    usage_period_start: date | None = None
    usage_period_end: date | None = None


class EvidenceValue(_Model):
    """A value a rule read: the canonical field, the snapshot it is from, the value."""

    field: str
    as_of_date: date
    value: int | date | str


class Indicator(_Model):
    code: IndicatorCode = Field(description="Stable code of the rule that fired.")
    severity: Literal["attention", "watch"]
    title: str
    explanation: str = Field(description="What was observed, in business terms.")
    observed: Decimal | None = Field(
        description="The value the rule compared, in `unit`. A percentage is cut to one "
        "decimal place for reading; the level was decided on the exact whole-number values."
    )
    unit: Literal["days", "percent"] | None
    rule: str = Field(description="The rule and its thresholds, in words.")
    observed_on: date = Field(description="The snapshot date the evidence is from.")
    compared_with: date | None = Field(
        description="For a usage trend: the earlier snapshot whose 30-day period was compared."
    )
    evidence: list[EvidenceValue]


class RuleNotApplied(_Model):
    rule: IndicatorCode
    reason: str


class Assessment(_Model):
    """A rule-based assessment of one snapshot. Not a prediction: there is no probability."""

    method: Literal["rules"]
    method_version: str
    as_of_date: date
    eligibility: Literal["assessed", "not_assessed"] = Field(
        description="assessed: an active or past_due account. not_assessed: a trialing, "
        "paused or canceled account, which these rules do not apply to - not one without risk."
    )
    level: Level | None = Field(description="null when the account is not assessed.")
    not_assessed_reason: str | None
    indicators: list[Indicator] = Field(description="Attention first, then watch.")
    rules_not_applied: list[RuleNotApplied] = Field(
        description="Rules the snapshot's data could not support, and why."
    )
    trend_compared_with: date | None


class AccountRow(_Model):
    account_id: str
    account_name: str | None
    status: AccountStatus
    segment: str | None
    company_size_band: str | None
    industry: str | None
    currency: str
    mrr: Decimal
    renewal_date: date | None
    days_to_renewal: int | None = Field(
        description="Days from the snapshot to a valid upcoming renewal of an assessed "
        "account; null otherwise."
    )
    assessment: Assessment
    next_step_context: str = Field(
        description="What the evidence points the team to look at first. Not a proven intervention."
    )


class AppliedFilters(_Model):
    level: list[LevelFilter]
    status: list[AccountStatus]
    currency: str | None
    segment: str | None
    renewal_within_days: int | None
    q: str | None


class AccountPage(_Model):
    """The prioritised account list: one page of the accounts that match."""

    dataset: DatasetInfo
    total: int = Field(description="Accounts matching the filters.")
    page: int
    page_size: int
    pages: int
    sort: SortKey
    filters: AppliedFilters
    rows: list[AccountRow]
    notes: list[str]


class Subscription(_Model):
    status: AccountStatus
    mrr: Decimal
    currency: str
    customer_since: date | None
    tenure_days: int | None = Field(description="Days from customer_since to the snapshot.")
    renewal_date: date | None
    days_to_renewal: int | None
    canceled_at: date | None
    cancellation_reason: str | None


class HistoryEntry(_Model):
    snapshot: SnapshotRecord
    assessment: Assessment = Field(
        description="The rules applied to this snapshot with only this and earlier snapshots."
    )


class AccountDetail(_Model):
    """One account: its latest snapshot and assessment, and its bounded history."""

    dataset: DatasetInfo
    account_id: str
    account_name: str | None
    latest: SnapshotRecord
    subscription: Subscription
    assessment: Assessment
    next_step_context: str
    history: list[HistoryEntry] = Field(description="Every snapshot, oldest first.")
    notes: list[str]


class LevelCounts(_Model):
    attention: int
    watch: int
    no_signals: int


class NotAssessedCounts(_Model):
    trialing: int
    paused: int
    canceled: int


class CurrencySummary(_Model):
    """One currency's accounts and recurring revenue. Never added to another currency's."""

    currency: str
    accounts: int
    eligible_accounts: int
    excluded_accounts: int
    levels: LevelCounts
    active_mrr: Decimal = Field(
        description="MRR of the assessed (active and past_due) accounts in this currency."
    )
    attention_mrr: Decimal
    watch_mrr: Decimal
    no_signals_mrr: Decimal
    exposed_mrr: Decimal = Field(
        description="attention_mrr + watch_mrr: the gross recurring revenue of accounts "
        "showing attention or watch signals. Not predicted loss, expected churn, revenue "
        "saved, retention impact or recoverable revenue."
    )


class RenewalWindow(_Model):
    from_days: int
    to_days: int
    severity: Literal["attention", "watch"]
    accounts: int


class RuleDescription(_Model):
    code: IndicatorCode
    rule: str


class Methodology(_Model):
    method: Literal["rules"]
    method_version: str
    rules: list[RuleDescription]
    statement: str


class PortfolioSummary(_Model):
    """Every portfolio number, computed by the service: a front end only shows them."""

    dataset: DatasetInfo
    total_accounts: int
    eligible_accounts: int
    excluded_accounts: int
    not_assessed: NotAssessedCounts
    levels: LevelCounts
    currencies: list[CurrencySummary] = Field(description="One entry per currency, by code.")
    renewals: list[RenewalWindow] = Field(
        description="Assessed accounts renewing within each rule window. Counts, not amounts."
    )
    methodology: Methodology
    definitions: dict[str, str]
    notes: list[str]
