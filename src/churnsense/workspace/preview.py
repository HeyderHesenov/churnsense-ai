"""An upload, mapped and validated, as the Data Hub preview reports it.

The work of ``POST /api/v2/imports/preview`` without the HTTP: bytes in, a typed
response out. It reads nothing but its arguments and writes nothing at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import contract
from churnsense.workspace import schemas as out
from churnsense.workspace.mapping import (
    Assignment,
    Column,
    Mapping,
    parse_mapping,
    parse_status_mapping,
    resolve,
)
from churnsense.workspace.reader import CsvFile
from churnsense.workspace.validate import Problem, mrr_totals, validate

logger = logging.getLogger(__name__)

#: Accepted rows returned in full.
PREVIEW_ROWS: Final = 20
#: Problems, and warnings, listed; the rest are counted.
MAX_ISSUES: Final = 200
#: Per-date, per-currency mrr totals listed.
MAX_TOTALS: Final = 100


@dataclass(frozen=True, slots=True)
class Limits:
    max_columns: int
    max_rows: int


NOTE_PREVIEW = (
    "Preview only: nothing was stored or committed, and the upload is discarded once this "
    "response is sent."
)
NOTE_MRR = (
    "mrr is read exactly as given: the account's monthly recurring revenue, summed over its "
    "active paid subscriptions and normalised to a month before upload. Nothing is derived, "
    "converted or rounded."
)
NOTE_TOTALS = (
    "mrr totals are per snapshot date and per currency: amounts on different dates or in "
    "different currencies are never added together."
)
NOTE_CURRENCY = (
    "currency is checked for its form only (three uppercase letters); whether the code is a "
    "current ISO 4217 currency is not checked."
)
NOTE_TRIM = "Surrounding spaces are trimmed from every value; nothing else in a value is changed."
NOTE_SUGGESTED = (
    "No mapping was sent, so every unambiguous column match (mapping.applied) was used. "
    "Send an explicit mapping to change it."
)


def _ref(column: Column) -> out.ColumnRef:
    return out.ColumnRef(position=column.position, name=column.name)


def _match(a: Assignment) -> out.FieldMatch:
    return out.FieldMatch(column=_ref(a.column), field=a.target, match=a.match)


def _issue(p: Problem) -> out.RowIssue:
    return out.RowIssue(row=p.row, field=p.field, code=p.code, message=p.message)


def _mapping(m: Mapping) -> out.MappingReport:
    return out.MappingReport(
        source=m.source,
        applied=[_match(a) for a in m.applied],
        suggestions=[_match(a) for a in m.suggestions],
        unresolved=[
            out.UnresolvedField(
                field=u.target,
                required=u.required,
                reason=u.reason,
                candidates=[_ref(c) for c in u.candidates],
            )
            for u in m.unresolved
        ],
        missing_required=list(m.missing_required),
    )


def build_preview(
    payload: bytes,
    limits: Limits,
    mapping_text: str | None = None,
    status_mapping_text: str | None = None,
) -> out.ImportPreviewResponse:
    """Map, read and validate ``payload``; ``SchemaValidationError`` for a file or a mapping
    that cannot be read at all, a preview of every row otherwise."""
    explicit = parse_mapping(mapping_text, limits.max_columns)
    translations = parse_status_mapping(status_mapping_text)
    file = CsvFile(payload, max_columns=limits.max_columns, max_rows=limits.max_rows)
    header = file.header()
    mapping = resolve(header.names, explicit)
    ready = not mapping.missing_required

    if ready:
        result = validate(file.records(), mapping.targets, len(header.names), translations)
        rows = result.rows
    else:
        # Every row is still read, so the file is held to the same limits and quoting.
        rows = sum(1 for _ in file.records())
        result = None
    if rows == 0:
        raise SchemaValidationError("file contains a header but no data rows")

    blank = [Problem(r, None, "blank_row", "blank row skipped") for r in file.tally.blank_rows]
    warnings = sorted([*blank, *(result.warnings if result else [])], key=lambda p: p.order)
    warning_total = file.tally.blank_count + (len(result.warnings) if result else 0)
    problems = result.problems if result else []
    accepted = result.accepted if result else []
    totals = mrr_totals(accepted)

    notes = [NOTE_PREVIEW]
    if mapping.source == "suggested" and mapping.applied:
        notes.append(NOTE_SUGGESTED)
    if result is None:
        notes.append(
            "Rows were not validated: map the required field(s) "
            f"{', '.join(mapping.missing_required)} and send the file again with a mapping."
        )
    else:
        notes += [NOTE_MRR, NOTE_TOTALS, NOTE_CURRENCY, NOTE_TRIM]
        if result.status_case_folded:
            notes.append(
                f"{result.status_case_folded:,} status value(s) written in another case were "
                "read as the lowercase canonical status."
            )
        if result.status_translated:
            notes.append(
                f"{result.status_translated:,} status value(s) were translated by status_mapping."
            )

    if result is None:
        status = "mapping_required"
    elif not accepted:
        status = "invalid"
    elif result.rejected:
        status = "partially_valid"
    else:
        status = "valid"
    logger.info(
        "v2 import preview: status=%s rows=%d accepted=%d rejected=%d",
        status,
        rows,
        len(accepted),
        result.rejected if result else 0,
    )
    return out.ImportPreviewResponse(
        status=status,
        contract_version=contract.CONTRACT_VERSION,
        mapping=_mapping(mapping),
        columns=out.ColumnsReport(
            ignored=[_ref(c) for c in mapping.ignored],
            dropped_personal=[_ref(c) for c in mapping.dropped],
        ),
        summary=out.PreviewSummary(
            rows_processed=rows,
            accepted=len(accepted),
            rejected=result.rejected if result else 0,
            warnings=warning_total,
            blank_rows=file.tally.blank_count,
            mrr_totals=[
                out.MrrTotal(as_of_date=d, currency=c, accounts=n, mrr=total)
                for d, c, n, total in totals[:MAX_TOTALS]
            ],
            mrr_totals_omitted=max(0, len(totals) - MAX_TOTALS),
        ),
        preview=[out.AccountSnapshot(**snapshot) for snapshot in accepted[:PREVIEW_ROWS]],
        problems=[_issue(p) for p in problems[:MAX_ISSUES]],
        problems_truncated=len(problems) > MAX_ISSUES,
        problems_omitted=max(0, len(problems) - MAX_ISSUES),
        warning_details=[_issue(p) for p in warnings[:MAX_ISSUES]],
        warnings_truncated=warning_total > MAX_ISSUES,
        warnings_omitted=max(0, warning_total - min(len(warnings), MAX_ISSUES)),
        notes=notes,
    )
