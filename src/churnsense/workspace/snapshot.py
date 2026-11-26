"""One canonical account snapshot as an immutable, typed record.

The same fields as the contract (``contract.FIELDS``), in the same order, with
the same meaning: one account at one ``as_of_date``. The Data Hub preview
validates rows into this shape; the synthetic dataset (``synthetic``) is
generated in it, and the rule engine (``risk``) reads nothing else.

Money is ``Decimal`` and dates are ``date``: nothing here is a float.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Snapshot:
    account_external_id: str
    as_of_date: date
    status: str
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

    def as_dict(self) -> dict[str, Any]:
        """The snapshot's fields by name, in contract order."""
        return asdict(self)
