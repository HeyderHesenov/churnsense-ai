"""Every row of an upload checked against the account snapshot contract.

Collect, never stop: every problem of every row is found, so a user fixing the
source data sees all of them at once. A row with any problem is *rejected*; the
others are *accepted*. A warning never rejects a row, and warnings are reported
for accepted rows only - a rejected row's problems come first.

**No message repeats a value from the file.** A message says which rule a
field breaks, in fixed words; the row number and the field name locate it. No
value is guessed, filled in or silently changed: surrounding spaces are trimmed,
a status written in another case is read as the canonical one (and counted in
the notes), and a status the request's ``status_mapping`` names is translated.
Nothing else.

Rules between fields hold only between values that were read: a field that
failed its own check is not checked again against another.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Context, Decimal, Inexact, InvalidOperation
from typing import Any, Final

from churnsense.workspace import contract, privacy
from churnsense.workspace.mapping import Column
from churnsense.workspace.reader import Record

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_DECIMAL = re.compile(
    rf"\d{{1,{contract.MRR_INTEGER_DIGITS}}}(?:\.\d{{1,{contract.MRR_FRACTION_DIGITS}}})?",
    re.ASCII,
)
_NEGATIVE = re.compile(r"-\s*\d+(?:\.\d+)?", re.ASCII)
_COUNT = re.compile(r"\d{1,9}", re.ASCII)
_NEGATIVE_COUNT = re.compile(r"-\s*\d+", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: Exact arithmetic for money: a sum that would round raises instead.
MONEY: Final = Context(prec=60, traps=[Inexact, InvalidOperation])

#: How many other rows a duplicate's message lists.
ROWS_LISTED: Final = 10

_DATE_RULE = (
    f"must be a calendar date written YYYY-MM-DD, between {contract.EARLIEST_DATE} "
    f"and {contract.LATEST_DATE}"
)


@dataclass(frozen=True, slots=True)
class Problem:
    row: int
    field: str | None
    code: str
    message: str

    @property
    def order(self) -> tuple[int, int, str]:
        position = contract.FIELD_ORDER.get(self.field, -1) if self.field else -1
        return self.row, position, self.code


@dataclass(slots=True)
class Result:
    """What validating every row found. ``accepted`` holds every accepted snapshot."""

    accepted: list[dict[str, Any]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    rows: int = 0
    rejected: int = 0
    status_case_folded: int = 0
    status_translated: int = 0


class _Row:
    """One record's values by field, and the problems and warnings it collects."""

    def __init__(self, record: Record, targets: dict[str, Column]) -> None:
        self.number = record.row
        self.raw = {
            name: (record.fields[c.index].strip() if c.index < len(record.fields) else "")
            for name, c in targets.items()
        }
        self.mapped = set(targets)
        self.values: dict[str, Any] = {}
        self.problems: list[Problem] = []
        self.warnings: list[Problem] = []

    def fail(self, name: str | None, code: str, message: str) -> None:
        self.problems.append(Problem(self.number, name, code, message))

    def warn(self, name: str | None, code: str, message: str) -> None:
        self.warnings.append(Problem(self.number, name, code, message))

    def given(self, name: str) -> bool:
        return bool(self.raw.get(name))

    def ok(self, *names: str) -> bool:
        """Whether every named field was given and read without a problem."""
        return all(self.values.get(n) is not None for n in names)


# --- one field --------------------------------------------------------------------------------


def _date(row: _Row, name: str, text: str) -> None:
    if _DATE.fullmatch(text):
        try:
            value = date.fromisoformat(text)
        except ValueError:
            value = None
        if value is not None and contract.EARLIEST_DATE <= value <= contract.LATEST_DATE:
            row.values[name] = value
            return
    row.fail(name, "invalid_date", f"{name} {_DATE_RULE}")


def _decimal(row: _Row, name: str, text: str) -> None:
    if _DECIMAL.fullmatch(text):
        row.values[name] = Decimal(text)
    elif _NEGATIVE.fullmatch(text):
        row.fail(name, "negative_value", f"{name} must not be negative")
    else:
        row.fail(
            name,
            "invalid_decimal",
            f"{name} must be a plain decimal number such as 1250.50: digits, at most "
            f"{contract.MRR_FRACTION_DIGITS} after the point, no sign, symbol, thousands "
            "separator or exponent",
        )


def _count(row: _Row, name: str, text: str) -> None:
    if _COUNT.fullmatch(text):
        row.values[name] = int(text)
    elif _NEGATIVE_COUNT.fullmatch(text):
        row.fail(name, "negative_value", f"{name} must not be negative")
    else:
        row.fail(
            name,
            "invalid_integer",
            f"{name} must be a whole number from 0 to {contract.COUNT_MAX:,}, digits only",
        )


def _text(row: _Row, name: str, text: str, max_length: int) -> bool:
    if len(text) > max_length:
        row.fail(name, "too_long", f"{name} must be at most {max_length} characters")
    elif _CONTROL.search(text):
        row.fail(name, "invalid_characters", f"{name} must not contain control characters")
    else:
        return True
    return False


def _identifying(row: _Row, name: str, text: str, max_length: int) -> None:
    """An account identifier or name: bounded text that is not a person's contact detail."""
    if not _text(row, name, text, max_length):
        return
    if privacy.looks_like_personal_identifier(text):
        row.fail(
            name,
            "personal_identifier_value",
            f"{name} looks like an email address or a phone number; identify the account "
            "without a person's contact details",
        )
        return
    row.values[name] = text


def _status(row: _Row, text: str, translations: dict[str, str], result: Result) -> None:
    if text in contract.STATUSES:
        row.values["status"] = text
    elif text in translations:
        row.values["status"] = translations[text]
        result.status_translated += 1
    elif (folded := text.casefold()) in contract.STATUSES:
        row.values["status"] = folded
        result.status_case_folded += 1
    else:
        row.fail(
            "status",
            "invalid_status",
            f"status must be one of {list(contract.STATUSES)} (any case), or a value the "
            "request's status_mapping translates",
        )


def _choice(row: _Row, spec: contract.Field, text: str) -> None:
    assert spec.choices is not None
    if text in spec.choices:
        row.values[spec.name] = text
    else:
        row.fail(spec.name, "invalid_choice", f"{spec.name} must be one of {list(spec.choices)}")


def _read(row: _Row, translations: dict[str, str], result: Result) -> None:
    for spec in contract.FIELDS:
        text = row.raw.get(spec.name, "")
        if not text:
            if spec.required:
                row.fail(spec.name, "required", f"{spec.name} is required")
            continue
        match spec.kind:
            case "id":
                _identifying(row, spec.name, text, contract.ID_MAX_LENGTH)
            case "date":
                _date(row, spec.name, text)
            case "status":
                _status(row, text, translations, result)
            case "decimal":
                _decimal(row, spec.name, text)
            case "currency":
                if _CURRENCY.fullmatch(text):
                    row.values[spec.name] = text
                else:
                    row.fail(
                        spec.name,
                        "invalid_currency",
                        "currency must be a three-letter uppercase code such as USD",
                    )
            case "text":
                assert spec.max_length is not None
                if spec.name == "account_name":
                    _identifying(row, spec.name, text, spec.max_length)
                elif _text(row, spec.name, text, spec.max_length):
                    row.values[spec.name] = text
            case "choice":
                _choice(row, spec, text)
            case "count":
                _count(row, spec.name, text)


# --- between fields ---------------------------------------------------------------------------


def _not_after_snapshot(row: _Row, name: str) -> None:
    if row.ok(name, "as_of_date") and row.values[name] > row.values["as_of_date"]:
        row.fail(name, "after_snapshot", f"{name} must not be later than as_of_date")


def _cross_check(row: _Row) -> None:
    status = row.values.get("status")
    # canceled_at belongs to a cancelled account, and only to one.
    if status == "canceled" and not row.given("canceled_at"):
        row.fail(
            "canceled_at",
            "required_for_canceled",
            "canceled_at is required when status is canceled",
        )
    if status is not None and status != "canceled" and row.ok("canceled_at"):
        row.fail(
            "canceled_at",
            "forbidden_for_status",
            "canceled_at is allowed only when status is canceled",
        )
    # mrr is the sum of active paid subscriptions: above zero exactly when there is one.
    if status is not None and row.ok("mrr"):
        mrr = row.values["mrr"]
        if status in contract.PAID_STATUSES and mrr <= 0:
            row.fail(
                "mrr",
                "mrr_inconsistent_with_status",
                f"mrr must be above 0 when status is {status}: the account has an active "
                "paid subscription",
            )
        elif status in contract.UNPAID_STATUSES and mrr != 0:
            row.fail(
                "mrr",
                "mrr_inconsistent_with_status",
                f"mrr must be 0 when status is {status}: the account has no active paid "
                "subscription",
            )
    if (
        row.ok("renewal_date", "as_of_date")
        and row.values["renewal_date"] < row.values["as_of_date"]
    ):
        row.fail(
            "renewal_date", "renewal_before_snapshot", "renewal_date must not precede as_of_date"
        )
    for name in ("customer_since", "canceled_at", "last_activity_at", "usage_period_end"):
        _not_after_snapshot(row, name)
    if (
        row.ok("canceled_at", "customer_since")
        and row.values["canceled_at"] < row.values["customer_since"]
    ):
        row.fail(
            "canceled_at",
            "canceled_before_customer_since",
            "canceled_at must not precede customer_since",
        )
    # The usage period is a pair: both or neither, start first.
    start, end = row.given("usage_period_start"), row.given("usage_period_end")
    if start != end:
        missing = "usage_period_end" if start else "usage_period_start"
        row.fail(
            missing,
            "usage_period_incomplete",
            "usage_period_start and usage_period_end must be given together",
        )
    if (
        row.ok("usage_period_start", "usage_period_end")
        and row.values["usage_period_start"] > row.values["usage_period_end"]
    ):
        row.fail(
            "usage_period_end",
            "usage_period_order",
            "usage_period_end must not precede usage_period_start",
        )
    # Fields that do not apply to the status are kept as given, and named.
    if status in contract.UNPAID_STATUSES and row.ok("renewal_date"):
        row.warn(
            "renewal_date",
            "renewal_without_paid_subscription",
            f"renewal_date is given but status is {status}, which has no active paid subscription",
        )
    if status is not None and status != "canceled" and row.ok("cancellation_reason"):
        row.warn(
            "cancellation_reason",
            "reason_without_cancellation",
            "cancellation_reason is given but status is not canceled",
        )


# --- across rows ------------------------------------------------------------------------------


def _listed(rows: list[int]) -> str:
    shown = ", ".join(str(r) for r in rows[:ROWS_LISTED])
    return shown if len(rows) <= ROWS_LISTED else f"{shown} and {len(rows) - ROWS_LISTED:,} more"


def _duplicates(rows: list[_Row]) -> None:
    """Every row of a repeated account and date is rejected; none is kept silently."""
    seen: dict[tuple[str, date], list[_Row]] = defaultdict(list)
    for row in rows:
        if row.ok("account_external_id", "as_of_date"):
            seen[(row.values["account_external_id"], row.values["as_of_date"])].append(row)
    for group in seen.values():
        if len(group) < 2:
            continue
        numbers = [r.number for r in group]
        for row in group:
            others = [n for n in numbers if n != row.number]
            row.fail(
                "account_external_id",
                "duplicate_account_snapshot",
                "the same account_external_id and as_of_date are also in row(s) "
                f"{_listed(others)}; give each account one row per date",
            )


def _shared_names(rows: list[_Row]) -> None:
    """Accepted rows whose account_name another account also uses: a warning only."""
    names: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        if row.ok("account_name"):
            names[row.values["account_name"].casefold()].append(row)
    for group in names.values():
        if len({r.values["account_external_id"] for r in group}) < 2:
            continue
        for row in group:
            others = [
                r.number
                for r in group
                if r.values["account_external_id"] != row.values["account_external_id"]
            ]
            row.warn(
                "account_name",
                "duplicate_account_name",
                f"account_name is also used by another account_external_id in row(s) "
                f"{_listed(others)}",
            )


def validate(
    records: Iterable[Record],
    targets: dict[str, Column],
    header_width: int,
    translations: dict[str, str],
) -> Result:
    """Read and check every record mapped by ``targets``; reject each with any problem."""
    result = Result()
    rows: list[_Row] = []
    for record in records:
        result.rows += 1
        row = _Row(record, targets)
        if len(record.fields) != header_width:
            row.fail(
                None,
                "row_width_mismatch",
                f"row has {len(record.fields):,} fields but the header has {header_width:,}; "
                "check for an unquoted comma or a missing field",
            )
        else:
            _read(row, translations, result)
            _cross_check(row)
        rows.append(row)
    _duplicates(rows)
    accepted = [row for row in rows if not row.problems]
    _shared_names(accepted)
    for row in rows:
        if row.problems:
            result.rejected += 1
            result.problems.extend(row.problems)
        else:
            result.warnings.extend(row.warnings)
            result.accepted.append({"row": row.number, **row.values})
    result.problems.sort(key=lambda p: p.order)
    result.warnings.sort(key=lambda p: p.order)
    return result


def mrr_totals(accepted: list[dict[str, Any]]) -> list[tuple[date, str, int, Decimal]]:
    """Accepted mrr summed per snapshot date and currency, never across either.

    Adding one account's snapshots of different dates would count it twice, and
    adding currencies would add unlike amounts, so each total is one date's
    accounts in one currency.
    """
    sums: dict[tuple[date, str], Decimal] = {}
    counts: Counter[tuple[date, str]] = Counter()
    for snapshot in accepted:
        key = (snapshot["as_of_date"], snapshot["currency"])
        sums[key] = MONEY.add(sums.get(key, Decimal(0)), snapshot["mrr"])
        counts[key] += 1
    return [(d, c, counts[(d, c)], sums[(d, c)]) for d, c in sorted(sums)]
