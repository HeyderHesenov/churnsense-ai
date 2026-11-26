"""The Data Hub preview's response, as the OpenAPI schema states it.

Money is ``Decimal`` end to end and leaves as a JSON string ("1250.50"), so no
amount passes through a binary float. Every list of rows, problems or totals is
bounded, and says how much it left out.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AccountStatus = Literal["trialing", "active", "past_due", "paused", "canceled"]


class _Model(BaseModel):
    # A validation error never quotes the value it refused: it may be the upload's.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ColumnRef(_Model):
    """A column of the upload: its 1-based position and its name as it may be shown."""

    position: int = Field(ge=1)
    name: str = Field(
        description="The column's name; a name that is itself an email address or a phone "
        "number is replaced by '[column N]'."
    )


class FieldMatch(_Model):
    column: ColumnRef
    field: str
    match: Literal["explicit", "exact", "normalised"]


class UnresolvedField(_Model):
    field: str
    required: bool
    reason: Literal["missing", "ambiguous"]
    candidates: list[ColumnRef] = Field(description="The columns that match it, if ambiguous.")


class MappingReport(_Model):
    source: Literal["explicit", "suggested"] = Field(
        description="explicit: the request's mapping, used as sent. suggested: no mapping was "
        "sent, and every unambiguous match was applied."
    )
    applied: list[FieldMatch]
    suggestions: list[FieldMatch] = Field(
        description="Every unambiguous exact or normalised match, whichever mapping was applied."
    )
    unresolved: list[UnresolvedField]
    missing_required: list[str]


class ColumnsReport(_Model):
    ignored: list[ColumnRef] = Field(description="Columns no field was read from.")
    dropped_personal: list[ColumnRef] = Field(
        description="Columns whose name says they hold direct personal identifiers: never read."
    )


class RowIssue(_Model):
    row: int = Field(ge=1, description="The row in the file: the header is row 1.")
    field: str | None = Field(description="The canonical field, or null for the whole row.")
    code: str
    message: str


class MrrTotal(_Model):
    """Accepted rows' mrr on one snapshot date in one currency; never summed across either."""

    as_of_date: date
    currency: str
    accounts: int
    mrr: Decimal


class PreviewSummary(_Model):
    rows_processed: int = Field(description="Non-blank data rows read.")
    accepted: int
    rejected: int
    warnings: int
    blank_rows: int
    mrr_totals: list[MrrTotal]
    mrr_totals_omitted: int


class AccountSnapshot(_Model):
    """One accepted row, normalised to the canonical contract."""

    row: int = Field(ge=2)
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


class ImportPreviewResponse(_Model):
    """What the upload would import. Nothing is stored and nothing is committed."""

    status: Literal["valid", "partially_valid", "invalid", "mapping_required"] = Field(
        description="valid: every row accepted. partially_valid: some rows rejected. invalid: "
        "none accepted. mapping_required: no mapping was sent and a required field could not "
        "be matched, so no row was validated."
    )
    committed: Literal[False] = False
    contract_version: Literal["account_snapshot.v0"]
    mapping: MappingReport
    columns: ColumnsReport
    summary: PreviewSummary
    preview: list[AccountSnapshot] = Field(description="The first accepted rows, in file order.")
    problems: list[RowIssue]
    problems_truncated: bool
    problems_omitted: int
    warning_details: list[RowIssue]
    warnings_truncated: bool
    warnings_omitted: int
    notes: list[str]
