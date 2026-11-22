"""The canonical B2B SaaS account snapshot contract, version 0.

**One row is one customer account at one point in time** (``as_of_date``). The
key within an import is ``account_external_id`` + ``as_of_date``: an account may
appear on several snapshot dates, never twice on the same one.

The row is an *account-level aggregate*, never one row per subscription:

* ``mrr`` is the sum of the monthly recurring revenue of every active paid
  subscription on ``as_of_date`` - past-due ones included while they have not
  ended; trials, paused and cancelled ones excluded - each normalised to a
  month (an annual amount divided by 12, a quarterly one by 3) before adding.
* Every subscription summed into one row is in the one reporting ``currency``.
  Conversion happens upstream; amounts in different currencies are never added,
  here or in any total this package reports.
* ``status`` is the account's meaning, aggregated from its subscriptions as
  ``STATUS_MEANINGS`` states. Trial non-conversion is not logo churn, and
  cancelling one of several paid subscriptions while another stays active is
  contraction (``active`` with a lower ``mrr``), not ``canceled``.

There is no billing interval at account level: an account's subscriptions may
bill on different intervals, so ``mrr`` is already monthly.

The contract is a structural fact the validator depends on, so it lives in code,
as V1's telecom contract does (``churnsense.data.schema``) - which it does not
touch, share or extend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

CONTRACT_VERSION: Final = "account_snapshot.v0"

Kind = Literal["id", "date", "status", "decimal", "currency", "text", "choice", "count"]

#: The account-level statuses, and what each means for the account's subscriptions.
STATUS_MEANINGS: Final[dict[str, str]] = {
    "active": "at least one active paid subscription, and none of them is past due",
    "past_due": "at least one active paid subscription has an overdue payment",
    "paused": "no active paid subscription remains, but a paid relationship is paused",
    "trialing": "no active paid subscription remains and the account is in a trial",
    "canceled": "the account had a paid relationship and its last active paid "
    "subscription has ended",
}
STATUSES: Final[tuple[str, ...]] = ("trialing", "active", "past_due", "paused", "canceled")
#: Statuses with at least one active paid subscription: ``mrr`` is above zero.
PAID_STATUSES: Final = frozenset({"active", "past_due"})
#: Statuses with no active paid subscription: ``mrr`` is exactly zero.
UNPAID_STATUSES: Final = frozenset({"trialing", "paused", "canceled"})

#: Why the account's last paid subscription ended: a controlled vocabulary, never free text.
CANCELLATION_REASONS: Final[tuple[str, ...]] = (
    "too_expensive",
    "budget_cut",
    "missing_features",
    "product_issues",
    "support_experience",
    "switched_to_competitor",
    "low_adoption",
    "business_closed",
    "acquired_or_merged",
    "payment_failure",
    "other",
)
#: Employee head-count bands of the customer company.
COMPANY_SIZE_BANDS: Final[tuple[str, ...]] = (
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1000",
    "1001-5000",
    "5001+",
)

ID_MAX_LENGTH: Final = 128
NAME_MAX_LENGTH: Final = 200
LABEL_MAX_LENGTH: Final = 64
#: ``mrr``: at most this many digits before the decimal point and after it.
MRR_INTEGER_DIGITS: Final = 12
MRR_FRACTION_DIGITS: Final = 4
#: The largest usage count accepted (nine digits).
COUNT_MAX: Final = 999_999_999
#: Every date must fall in this range; anything outside it is a typo, not a customer.
EARLIEST_DATE: Final = date(1900, 1, 1)
LATEST_DATE: Final = date(2199, 12, 31)


@dataclass(frozen=True, slots=True)
class Field:
    """One canonical column: its type, whether a row must have it, and what it means."""

    name: str
    kind: Kind
    required: bool
    description: str
    max_length: int | None = None
    choices: tuple[str, ...] | None = None


FIELDS: Final[tuple[Field, ...]] = (
    Field(
        "account_external_id",
        "id",
        True,
        "The account's stable identifier in the source system. Surrounding spaces are "
        "trimmed; compared case-sensitively.",
        max_length=ID_MAX_LENGTH,
    ),
    Field("as_of_date", "date", True, "The date the snapshot describes (YYYY-MM-DD)."),
    Field("status", "status", True, "The account-level status.", choices=STATUSES),
    Field(
        "mrr",
        "decimal",
        True,
        "Monthly recurring revenue of every active paid subscription on as_of_date, each "
        "normalised to a month, summed, in currency. Not an annual or per-subscription amount.",
    ),
    Field("currency", "currency", True, "The reporting currency of mrr: three uppercase letters."),
    Field("account_name", "text", False, "The customer company's name.", NAME_MAX_LENGTH),
    Field(
        "customer_since",
        "date",
        False,
        "The earliest known date of a paid relationship with the account.",
    ),
    Field(
        "renewal_date",
        "date",
        False,
        "The earliest upcoming renewal among the account's active paid subscriptions.",
    ),
    Field(
        "canceled_at",
        "date",
        False,
        "The date the account's last active paid subscription ended. Required when status "
        "is canceled; not allowed otherwise.",
    ),
    Field(
        "cancellation_reason",
        "choice",
        False,
        "Why the last paid subscription ended, from a fixed list.",
        choices=CANCELLATION_REASONS,
    ),
    Field(
        "segment",
        "text",
        False,
        "The account's segment, as the business names it.",
        LABEL_MAX_LENGTH,
    ),
    Field(
        "company_size_band",
        "choice",
        False,
        "The customer company's employee head-count band.",
        choices=COMPANY_SIZE_BANDS,
    ),
    Field("industry", "text", False, "The customer company's industry.", LABEL_MAX_LENGTH),
    Field(
        "active_users",
        "count",
        False,
        "Distinct users of the account active in the usage period: a count, never a list.",
    ),
    Field("seats_purchased", "count", False, "Seats the account pays for on as_of_date."),
    Field("sessions_30d", "count", False, "Sessions in the 30 days ending on as_of_date."),
    Field(
        "key_actions_30d",
        "count",
        False,
        "Key product actions (as the business defines them) in the 30 days ending on as_of_date.",
    ),
    Field(
        "last_activity_at",
        "date",
        False,
        "The last date any user of the account was active (YYYY-MM-DD).",
    ),
    Field("usage_period_start", "date", False, "First day of the period active_users covers."),
    Field("usage_period_end", "date", False, "Last day of the period active_users covers."),
)

BY_NAME: Final[dict[str, Field]] = {f.name: f for f in FIELDS}
FIELD_ORDER: Final[dict[str, int]] = {f.name: i for i, f in enumerate(FIELDS)}
REQUIRED: Final[tuple[str, ...]] = tuple(f.name for f in FIELDS if f.required)
