"""The V2 Data Hub preview's domain: the contract, the mapping and every row rule.

Every test runs on synthetic snapshots (``tests/workspace_csv.py``) through
``churnsense.workspace.preview.build_preview``, the endpoint's work without the
HTTP. Every rule has a case that passes - at its boundary where it has one - and
a case that fails.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import contract, mapping, privacy
from churnsense.workspace import schemas as out
from churnsense.workspace.preview import MAX_ISSUES, PREVIEW_ROWS, Limits, build_preview
from tests.workspace_csv import payload, row, text

LIMITS = Limits(max_columns=1000, max_rows=10_000)


def preview(rows=None, *, raw: bytes | None = None, limits: Limits = LIMITS, **kwargs):
    body = raw if raw is not None else payload(rows if rows is not None else [row()])
    return build_preview(body, limits, **kwargs)


def refused(rows=None, *, raw: bytes | None = None, limits: Limits = LIMITS, **kwargs) -> str:
    with pytest.raises(SchemaValidationError) as info:
        preview(rows, raw=raw, limits=limits, **kwargs)
    return " ".join([str(info.value), *info.value.problems])


def codes(result: out.ImportPreviewResponse) -> set[tuple[int, str | None, str]]:
    return {(p.row, p.field, p.code) for p in result.problems}


def warning_codes(result: out.ImportPreviewResponse) -> set[tuple[int, str | None, str]]:
    return {(p.row, p.field, p.code) for p in result.warning_details}


def accepted_one(**values: str) -> out.AccountSnapshot:
    result = preview([row(**values)])
    assert result.status == "valid", result.problems
    return result.preview[0]


def rejected_one(**values: str) -> set[tuple[int, str | None, str]]:
    result = preview([row(**values)])
    assert result.summary.rejected == 1 and result.status == "invalid"
    return codes(result)


# --- the contract -----------------------------------------------------------------------------


def test_the_contract_requires_exactly_the_five_account_snapshot_fields():
    assert contract.REQUIRED == ("account_external_id", "as_of_date", "status", "mrr", "currency")
    assert contract.STATUSES == ("trialing", "active", "past_due", "paused", "canceled")
    assert set(contract.STATUS_MEANINGS) == set(contract.STATUSES)
    assert set(contract.STATUSES) == contract.PAID_STATUSES | contract.UNPAID_STATUSES
    assert not contract.PAID_STATUSES & contract.UNPAID_STATUSES


def test_the_contract_has_no_billing_interval_and_no_personal_field():
    names = {f.name for f in contract.FIELDS}
    assert "billing_interval" not in names
    assert not any(privacy.sensitive_header(name) for name in names)


def test_the_response_snapshot_has_every_contract_field_and_the_status_vocabulary():
    assert set(out.AccountSnapshot.model_fields) == {"row"} | {f.name for f in contract.FIELDS}
    status = out.AccountSnapshot.model_fields["status"].annotation
    assert set(status.__args__) == set(contract.STATUSES)


def test_the_contract_is_its_own_and_not_the_telecom_one():
    from churnsense.data import schema

    assert not {f.name for f in contract.FIELDS} & set(schema.RAW_COLUMNS)


# --- a valid file -----------------------------------------------------------------------------


def test_a_valid_canonical_file_is_accepted_and_normalised():
    rows = [
        row(),
        row(
            account_external_id="SYN-0002",
            status="past_due",
            mrr="99.0001",
            currency="EUR",
            account_name="Synthetic Co 2",
            customer_since="2024-01-15",
            renewal_date="2027-01-15",
            segment="SMB",
            company_size_band="11-50",
            industry="Software",
            active_users="12",
            seats_purchased="15",
            sessions_30d="340",
            key_actions_30d="0",
            last_activity_at="2026-10-30",
            usage_period_start="2026-10-01",
            usage_period_end="2026-10-31",
        ),
        row(account_external_id="SYN-0003", status="canceled", mrr="0", canceled_at="2026-09-30"),
        row(account_external_id="SYN-0004", status="trialing", mrr="0"),
        row(account_external_id="SYN-0005", status="paused", mrr="0.00"),
    ]
    result = preview(rows)
    assert result.status == "valid"
    assert result.committed is False
    assert result.contract_version == "account_snapshot.v0"
    assert (result.summary.rows_processed, result.summary.accepted) == (5, 5)
    assert (result.summary.rejected, result.summary.warnings) == (0, 0)
    assert result.problems == [] and result.warning_details == []
    second = result.preview[1]
    assert second.row == 3
    assert second.mrr == Decimal("99.0001")
    assert str(second.usage_period_end) == "2026-10-31"
    assert second.active_users == 12 and second.key_actions_30d == 0
    assert result.preview[2].canceled_at is not None


def test_a_response_validation_error_never_quotes_the_value():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as info:
        out.RowIssue(row=0, field=None, code="x", message="y")
    assert "input_value" not in str(info.value)


def test_money_leaves_as_an_exact_decimal_string():
    body = json.loads(preview([row(mrr="1250.50")]).model_dump_json())
    assert body["preview"][0]["mrr"] == "1250.50"
    assert body["summary"]["mrr_totals"][0]["mrr"] == "1250.50"


def test_mrr_totals_are_exact_and_per_date_and_currency_only():
    rows = [
        row(account_external_id="SYN-1", mrr="0.1"),
        row(account_external_id="SYN-2", mrr="0.2"),
        row(account_external_id="SYN-3", mrr="7", currency="EUR"),
        row(account_external_id="SYN-1", as_of_date="2026-09-30", mrr="5"),
    ]
    totals = [
        (str(t.as_of_date), t.currency, t.accounts, t.mrr) for t in preview(rows).summary.mrr_totals
    ]
    assert totals == [
        ("2026-09-30", "USD", 1, Decimal("5")),
        ("2026-10-31", "EUR", 1, Decimal("7")),
        ("2026-10-31", "USD", 2, Decimal("0.3")),
    ]


def test_mrr_totals_beyond_the_limit_are_counted(monkeypatch):
    from churnsense.workspace import preview as module

    monkeypatch.setattr(module, "MAX_TOTALS", 2)
    rows = [row(account_external_id=f"SYN-{c}", currency=c) for c in ("AAA", "BBB", "CCC")]
    summary = preview(rows).summary
    assert len(summary.mrr_totals) == 2 and summary.mrr_totals_omitted == 1


def test_the_preview_holds_the_first_twenty_accepted_rows_only():
    rows = [row(account_external_id=f"SYN-{i:04d}") for i in range(PREVIEW_ROWS + 5)]
    result = preview(rows)
    assert result.summary.accepted == PREVIEW_ROWS + 5
    assert [s.row for s in result.preview] == list(range(2, PREVIEW_ROWS + 2))


# --- mapping ----------------------------------------------------------------------------------


def test_exact_headers_are_matched_and_applied():
    result = preview([row()])
    assert result.mapping.source == "suggested"
    assert {m.field: m.match for m in result.mapping.applied} == dict.fromkeys(
        contract.REQUIRED, "exact"
    )
    assert result.mapping.unresolved == [] and result.mapping.missing_required == []


def test_surrounding_spaces_in_a_header_still_match_exactly():
    body = b" account_external_id ,as_of_date,status,mrr,currency\nSYN-1,2026-10-31,active,1,USD\n"
    result = preview(raw=body)
    assert result.mapping.applied[0].match == "exact"
    assert result.status == "valid"


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Account External ID", "account_external_id"),
        ("accountExternalId", "account_external_id"),
        ("ACCOUNT-EXTERNAL-ID", "account_external_id"),
        ("As Of Date", "as_of_date"),
        ("MRR", "mrr"),
        ("Sessions30d", "sessions_30d"),
        ("key actions 30d", "key_actions_30d"),
    ],
)
def test_normalised_headers_are_matched(header, field):
    assert privacy.normalise(header) == field
    names = [header if f == field else f for f in (*contract.REQUIRED, field)]
    names = list(dict.fromkeys(names))
    matches = {a.target: a.match for a in mapping.resolve(names, None).applied}
    assert matches[field] == "normalised"


def test_an_explicit_mapping_is_used_as_sent_and_is_complete():
    body = "Customer Ref,Snapshot,State,Monthly,Cur,segment\nSYN-1,2026-10-31,active,10,USD,SMB\n"
    explicit = {
        "Customer Ref": "account_external_id",
        "Snapshot": "as_of_date",
        "State": "status",
        "Monthly": "mrr",
        "Cur": "currency",
    }
    result = preview(raw=body.encode(), mapping_text=json.dumps(explicit))
    assert result.status == "valid", result.problems
    assert result.mapping.source == "explicit"
    assert {m.field for m in result.mapping.applied} == set(contract.REQUIRED)
    assert all(m.match == "explicit" for m in result.mapping.applied)
    # 'segment' matches exactly, but the explicit mapping does not name it: not read.
    assert result.preview[0].segment is None
    assert [c.name for c in result.columns.ignored] == ["segment"]
    assert any(m.field == "segment" for m in result.mapping.suggestions)


def test_without_a_mapping_a_missing_required_field_asks_for_one_and_validates_nothing():
    rows = [{"account_external_id": "SYN-1", "Snapshot": "2026-10-31", "status": "x"}] * 3
    result = preview(rows)
    assert result.status == "mapping_required"
    assert result.mapping.missing_required == ["as_of_date", "mrr", "currency"]
    assert {u.field for u in result.mapping.unresolved} == {"as_of_date", "mrr", "currency"}
    assert all(u.reason == "missing" and u.required for u in result.mapping.unresolved)
    assert result.summary.rows_processed == 3
    assert result.summary.accepted == result.summary.rejected == 0
    assert result.preview == [] and result.problems == []
    assert any("not validated" in note for note in result.notes)


def test_an_explicit_mapping_without_a_required_field_is_refused():
    explicit = {f: f for f in contract.REQUIRED if f != "currency"}
    reason = refused([row()], mapping_text=json.dumps(explicit))
    assert "required field(s) not mapped: currency" in reason


def test_a_required_field_two_columns_match_is_ambiguous_and_not_chosen():
    header = ["account_external_id", "as_of_date", "status", "mrr", "MRR", "currency"]
    body = (",".join(header) + "\nSYN-1,2026-10-31,active,10,10,USD\n").encode()
    result = preview(raw=body)
    assert result.status == "mapping_required"
    (unresolved,) = result.mapping.unresolved
    assert (unresolved.field, unresolved.reason) == ("mrr", "ambiguous")
    assert [c.name for c in unresolved.candidates] == ["mrr", "MRR"]
    assert "mrr" not in {m.field for m in result.mapping.applied}


def test_an_ambiguous_optional_field_is_left_unmapped_and_the_rows_still_validate():
    header = [*contract.REQUIRED, "segment", "Segment"]
    body = (",".join(header) + "\nSYN-1,2026-10-31,active,10,USD,A,B\n").encode()
    result = preview(raw=body)
    assert result.status == "valid"
    assert [(u.field, u.reason, u.required) for u in result.mapping.unresolved] == [
        ("segment", "ambiguous", False)
    ]
    assert result.preview[0].segment is None
    assert [c.name for c in result.columns.ignored] == ["segment", "Segment"]


@pytest.mark.parametrize(
    ("mapping_text", "reason"),
    [
        ('{"mrr": "mrr", "mrr": "currency"}', "names the same key more than once"),
        (
            json.dumps({**{f: f for f in contract.REQUIRED}, " mrr": "mrr"}),
            "'mrr' is mapped more than once",
        ),
        (
            json.dumps({**{f: f for f in contract.REQUIRED}, "segment": "mrr"}),
            "field 'mrr' is mapped from more than one column",
        ),
        (
            json.dumps({**{f: f for f in contract.REQUIRED}, "segment": "plan_tier"}),
            "'segment' is mapped to an unknown field",
        ),
        (
            json.dumps({**{f: f for f in contract.REQUIRED}, "nowhere": "industry"}),
            "'nowhere' is not a column of the file",
        ),
        ("[1, 2]", "must be a JSON object of strings to strings"),
        ('{"mrr": 5}', "must be a JSON object of strings to strings"),
        ("{not json", "is not valid JSON"),
    ],
)
def test_an_invalid_explicit_mapping_is_refused_with_its_reason(mapping_text, reason):
    rows = [row(segment="SMB")]
    assert reason in refused(rows, mapping_text=mapping_text)


def test_a_mapping_cannot_name_a_header_the_file_repeats():
    header = ["account_external_id", "as_of_date", "status", "mrr", "currency", "x", "x"]
    body = (",".join(header) + "\nSYN-1,2026-10-31,active,10,USD,a,b\n").encode()
    explicit = json.dumps({**{f: f for f in contract.REQUIRED}, "x": "segment"})
    assert "names 2 columns of the file" in refused(raw=body, mapping_text=explicit)


def test_a_valid_explicit_mapping_passes_the_checks_an_invalid_one_fails():
    explicit = json.dumps({**{f: f for f in contract.REQUIRED}, "segment": "segment"})
    assert preview([row(segment="SMB")], mapping_text=explicit).preview[0].segment == "SMB"


def test_unknown_columns_are_ignored_and_their_values_never_returned():
    result = preview([row(plan_notes="UNKNOWN-VALUE-7731", crm_owner="UNKNOWN-VALUE-7732")])
    assert result.status == "valid"
    assert [c.name for c in result.columns.ignored] == ["plan_notes", "crm_owner"]
    assert "UNKNOWN-VALUE-77" not in result.model_dump_json()


@pytest.mark.parametrize(
    "header",
    [
        "email",
        "Contact Email",
        "contactEmail",
        "E-mail",
        "billing_email_address",
        "phone",
        "Phone Number",
        "mobile",
        "cell",
        "telephone",
        "WorkPhone",
        "fax",
        "first_name",
        "Last Name",
        "contact_name",
        "date_of_birth",
        "street address",
        "postal_code",
        "ip_address",
    ],
)
def test_personal_columns_are_dropped_by_name_and_their_values_never_returned(header):
    result = preview([row(**{header: "PERSONAL-VALUE-5501"})])
    assert result.status == "valid"
    assert [c.name for c in result.columns.dropped_personal] == [header]
    assert result.columns.ignored == []
    assert "PERSONAL-VALUE-5501" not in result.model_dump_json()


@pytest.mark.parametrize(
    "header", ["account_name", "segment", "industry", "status", "last_activity_at", "notes"]
)
def test_company_and_account_columns_are_not_mistaken_for_personal_ones(header):
    assert not privacy.sensitive_header(header)


def test_a_personal_column_cannot_be_mapped_explicitly():
    explicit = json.dumps({**{f: f for f in contract.REQUIRED}, "contact_email": "account_name"})
    reason = refused([row(contact_email="x")], mapping_text=explicit)
    assert "holds personal identifiers and cannot be mapped" in reason


def test_a_column_named_like_an_email_address_or_phone_is_shown_by_position_only():
    """A file without a header row has a customer's values for column names."""
    header = [*contract.REQUIRED, "someone@synthetic.example", "+1 555 010 0199"]
    body = (",".join(header) + "\nSYN-1,2026-10-31,active,10,USD,a,b\n").encode()
    result = preview(raw=body)
    names = [c.name for c in result.columns.ignored + result.columns.dropped_personal]
    assert names == ["[column 6]", "[column 7]"]
    dumped = result.model_dump_json()
    assert "synthetic.example" not in dumped and "555" not in dumped


def test_a_status_mapping_translates_source_values_and_says_so():
    rows = [row(status="unpaid"), row(account_external_id="SYN-2", status="Active")]
    result = preview(rows, status_mapping_text=json.dumps({"unpaid": "past_due"}))
    assert [s.status for s in result.preview] == ["past_due", "active"]
    assert any("translated by status_mapping" in n for n in result.notes)
    assert any("written in another case" in n for n in result.notes)


@pytest.mark.parametrize(
    ("status_mapping_text", "reason"),
    [
        ('{"active": "canceled"}', "cannot translate a canonical status"),
        ('{"ACTIVE": "active"}', "cannot translate a canonical status"),
        ('{"unpaid": "overdue"}', "translates to a value that is not one of"),
        ('{" ": "active"}', "blank source value"),
        ('{"unpaid": "past_due", " unpaid": "past_due"}', "same source value more than once"),
        ('{"a": "active", "a": "paused"}', "names the same key more than once"),
    ],
)
def test_an_invalid_status_mapping_is_refused(status_mapping_text, reason):
    assert reason in refused([row()], status_mapping_text=status_mapping_text)


# --- row rules: one field ---------------------------------------------------------------------


@pytest.mark.parametrize("value", ["2026-02-28", "2024-02-29", "1900-01-01", "2199-12-31"])
def test_a_calendar_date_in_range_is_accepted(value):
    accepted_one(as_of_date=value, renewal_date="2199-12-31")


@pytest.mark.parametrize(
    "value",
    ["2026-02-29", "2026-13-01", "2026/01/31", "20260131", "31-01-2026", "2026-1-31",
     "1899-12-31", "2200-01-01", "2026-01-31T00:00:00", "\uff12\uff10\uff12\uff16-01-31", "soon"],
)  # fmt: skip
def test_anything_but_an_iso_calendar_date_in_range_is_rejected(value):
    assert (2, "as_of_date", "invalid_date") in rejected_one(as_of_date=value)


@pytest.mark.parametrize("name", contract.REQUIRED)
def test_a_missing_required_value_is_rejected(name):
    assert (2, name, "required") in rejected_one(**{name: "  "})


def test_every_canonical_status_is_accepted_with_a_consistent_mrr():
    for status, mrr in [("active", "1"), ("past_due", "1"), ("paused", "0"), ("trialing", "0")]:
        assert accepted_one(status=status, mrr=mrr).status == status
    assert accepted_one(status="canceled", mrr="0", canceled_at="2026-10-31").status == "canceled"


def test_a_status_in_another_case_is_read_as_the_canonical_one():
    assert accepted_one(status=" PAST_DUE ").status == "past_due"


@pytest.mark.parametrize("value", ["churned", "act ive", "1"])
def test_an_unknown_status_is_rejected(value):
    assert (2, "status", "invalid_status") in rejected_one(status=value)


@pytest.mark.parametrize("value", ["0.0001", "1250.5", "007", "999999999999.9999"])
def test_a_plain_non_negative_decimal_is_accepted_exactly(value):
    assert accepted_one(mrr=value).mrr == Decimal(value)


@pytest.mark.parametrize("value", ["-5", "-0.01", "- 3"])
def test_a_negative_mrr_is_rejected_as_negative(value):
    assert (2, "mrr", "negative_value") in rejected_one(mrr=value)


@pytest.mark.parametrize(
    "value",
    ["1.23456", "1,250.00", "$5", "5 USD", "1e3", "NaN", "Infinity", "+5", ".5", "5.",
     "1000000000000"],
)  # fmt: skip
def test_anything_but_a_plain_decimal_mrr_is_rejected(value):
    assert (2, "mrr", "invalid_decimal") in rejected_one(mrr=value)


@pytest.mark.parametrize("value", ["USD", "EUR", "AZN", "XYZ"])
def test_a_three_letter_uppercase_currency_is_accepted(value):
    assert accepted_one(currency=value).currency == value


@pytest.mark.parametrize("value", ["usd", "Usd", "US", "USDX", "$", "U5D"])
def test_any_other_currency_form_is_rejected(value):
    assert (2, "currency", "invalid_currency") in rejected_one(currency=value)


def test_an_account_id_is_trimmed_and_kept_as_text():
    assert accepted_one(account_external_id="  0001234567  ").account_external_id == "0001234567"
    assert accepted_one(account_external_id="x" * 128).account_external_id == "x" * 128


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("x" * 129, "too_long"),
        ("SYN\x07-1", "invalid_characters"),
        ("owner@synthetic.example", "personal_identifier_value"),
        ("+994 50 000 00 00", "personal_identifier_value"),
        ("(555) 010-0199", "personal_identifier_value"),
    ],
)
def test_an_account_id_that_is_too_long_odd_or_personal_is_rejected(value, code):
    assert (2, "account_external_id", code) in rejected_one(account_external_id=value)


def test_an_account_name_is_bounded_and_never_a_contact_detail():
    assert accepted_one(account_name="S" * 200).account_name == "S" * 200
    assert (2, "account_name", "too_long") in rejected_one(account_name="S" * 201)
    found = rejected_one(account_name="Synthetic Co (billing@synthetic.example)")
    assert (2, "account_name", "personal_identifier_value") in found


@pytest.mark.parametrize("name", ["segment", "industry"])
def test_labels_are_bounded(name):
    assert getattr(accepted_one(**{name: "L" * 64}), name) == "L" * 64
    assert (2, name, "too_long") in rejected_one(**{name: "L" * 65})


@pytest.mark.parametrize(
    ("name", "good", "bad"),
    [
        ("cancellation_reason", "too_expensive", "Too expensive"),
        ("company_size_band", "5001+", "5000+"),
    ],
)
def test_a_controlled_field_takes_its_listed_values_only(name, good, bad):
    extra = {"status": "canceled", "mrr": "0", "canceled_at": "2026-10-01"}
    values = extra if name == "cancellation_reason" else {}
    assert getattr(accepted_one(**values, **{name: good}), name) == good
    assert (2, name, "invalid_choice") in rejected_one(**values, **{name: bad})


@pytest.mark.parametrize(
    "name", ["active_users", "seats_purchased", "sessions_30d", "key_actions_30d"]
)
@pytest.mark.parametrize("value", ["0", "999999999"])
def test_a_usage_count_from_zero_to_the_limit_is_accepted(name, value):
    assert getattr(accepted_one(**{name: value}), name) == int(value)


@pytest.mark.parametrize(
    "name", ["active_users", "seats_purchased", "sessions_30d", "key_actions_30d"]
)
@pytest.mark.parametrize(
    ("value", "code"),
    [("-1", "negative_value"), ("1.5", "invalid_integer"), ("1000000000", "invalid_integer"),
     ("12 users", "invalid_integer"), ("1,000", "invalid_integer")],
)  # fmt: skip
def test_a_usage_count_that_is_not_a_bounded_whole_number_is_rejected(name, value, code):
    assert (2, name, code) in rejected_one(**{name: value})


# --- row rules: between fields ----------------------------------------------------------------


def test_canceled_at_is_required_when_canceled():
    assert accepted_one(status="canceled", mrr="0", canceled_at="2026-10-31").canceled_at
    found = rejected_one(status="canceled", mrr="0")
    assert (2, "canceled_at", "required_for_canceled") in found


@pytest.mark.parametrize("status", ["active", "past_due", "paused", "trialing"])
def test_canceled_at_is_forbidden_for_any_other_status(status):
    mrr = "1" if status in contract.PAID_STATUSES else "0"
    found = rejected_one(status=status, mrr=mrr, canceled_at="2026-10-01")
    assert (2, "canceled_at", "forbidden_for_status") in found


@pytest.mark.parametrize("status", ["active", "past_due"])
def test_a_paid_status_needs_mrr_above_zero(status):
    assert accepted_one(status=status, mrr="0.0001")
    assert (2, "mrr", "mrr_inconsistent_with_status") in rejected_one(status=status, mrr="0")


@pytest.mark.parametrize("status", ["paused", "trialing", "canceled"])
def test_an_unpaid_status_needs_mrr_of_zero(status):
    extra = {"canceled_at": "2026-10-01"} if status == "canceled" else {}
    assert accepted_one(status=status, mrr="0.0000", **extra)
    found = rejected_one(status=status, mrr="0.01", **extra)
    assert (2, "mrr", "mrr_inconsistent_with_status") in found


def test_a_renewal_date_on_or_after_the_snapshot_is_accepted_and_before_it_rejected():
    assert accepted_one(renewal_date="2026-10-31")
    found = rejected_one(renewal_date="2026-10-30")
    assert (2, "renewal_date", "renewal_before_snapshot") in found


def test_a_renewal_date_without_a_paid_subscription_is_a_warning_only():
    result = preview([row(status="paused", mrr="0", renewal_date="2027-01-01")])
    assert result.status == "valid"
    assert warning_codes(result) == {(2, "renewal_date", "renewal_without_paid_subscription")}
    assert result.preview[0].renewal_date is not None  # kept as given


def test_a_cancellation_reason_without_a_cancellation_is_a_warning_only():
    result = preview([row(cancellation_reason="other")])
    assert result.status == "valid"
    assert warning_codes(result) == {(2, "cancellation_reason", "reason_without_cancellation")}


@pytest.mark.parametrize("name", ["customer_since", "last_activity_at"])
def test_a_past_date_may_equal_the_snapshot_but_not_follow_it(name):
    assert accepted_one(**{name: "2026-10-31"})
    assert (2, name, "after_snapshot") in rejected_one(**{name: "2026-11-01"})


def test_canceled_at_must_not_follow_the_snapshot_or_precede_customer_since():
    cancelled = {"status": "canceled", "mrr": "0", "customer_since": "2025-01-01"}
    assert accepted_one(**cancelled, canceled_at="2025-01-01")
    assert accepted_one(**cancelled, canceled_at="2026-10-31")
    found = rejected_one(**cancelled, canceled_at="2026-11-01")
    assert (2, "canceled_at", "after_snapshot") in found
    found = rejected_one(**cancelled, canceled_at="2024-12-31")
    assert (2, "canceled_at", "canceled_before_customer_since") in found


def test_a_usage_period_is_a_pair_in_order_that_ends_by_the_snapshot():
    assert accepted_one(usage_period_start="2026-10-31", usage_period_end="2026-10-31")
    found = rejected_one(usage_period_start="2026-10-01")
    assert (2, "usage_period_end", "usage_period_incomplete") in found
    found = rejected_one(usage_period_end="2026-10-31")
    assert (2, "usage_period_start", "usage_period_incomplete") in found
    found = rejected_one(usage_period_start="2026-10-02", usage_period_end="2026-10-01")
    assert (2, "usage_period_end", "usage_period_order") in found
    found = rejected_one(usage_period_start="2026-10-01", usage_period_end="2026-11-01")
    assert (2, "usage_period_end", "after_snapshot") in found


def test_an_unmapped_half_of_the_usage_period_counts_as_missing():
    body = payload([row(usage_period_start="2026-10-01")])
    result = preview(raw=body)
    assert (2, "usage_period_end", "usage_period_incomplete") in codes(result)


def test_a_field_that_failed_its_own_check_is_not_checked_against_another():
    found = rejected_one(renewal_date="2026-02-30")
    assert found == {(2, "renewal_date", "invalid_date")}


# --- row rules: across rows -------------------------------------------------------------------


def test_every_row_of_a_repeated_account_and_date_is_rejected_with_the_others_named():
    rows = [
        row(),
        row(account_external_id="SYN-0002"),
        row(mrr="10"),
        row(mrr="20", account_name="Synthetic Co 1"),
    ]
    result = preview(rows)
    assert result.status == "partially_valid"
    assert (result.summary.accepted, result.summary.rejected) == (1, 3)
    duplicates = {
        p.row: p.message for p in result.problems if p.code == "duplicate_account_snapshot"
    }
    assert set(duplicates) == {2, 4, 5}
    assert "row(s) 4, 5" in duplicates[2]
    assert "row(s) 2, 5" in duplicates[4]
    assert [s.account_external_id for s in result.preview] == ["SYN-0002"]


def test_a_duplicate_with_another_problem_is_still_rejected_as_a_duplicate():
    result = preview([row(), row(mrr="-1")])
    assert {(2, "duplicate_account_snapshot"), (3, "duplicate_account_snapshot")} <= {
        (p.row, p.code) for p in result.problems
    }


def test_the_same_account_on_different_dates_is_accepted():
    rows = [row(as_of_date=d) for d in ("2026-08-31", "2026-09-30", "2026-10-31")]
    assert preview(rows).summary.accepted == 3


def test_account_ids_are_compared_case_sensitively():
    rows = [row(account_external_id="syn-1"), row(account_external_id="SYN-1")]
    assert preview(rows).summary.accepted == 2


def test_a_name_shared_by_different_accounts_is_a_warning_and_the_rows_are_accepted():
    rows = [
        row(account_external_id="SYN-1", account_name="Synthetic Co"),
        row(account_external_id="SYN-2", account_name="synthetic co"),
        row(account_external_id="SYN-1", account_name="Synthetic Co", as_of_date="2026-09-30"),
        row(account_external_id="SYN-3", account_name="Other Co"),
    ]
    result = preview(rows)
    assert result.status == "valid" and result.summary.accepted == 4
    assert warning_codes(result) == {
        (2, "account_name", "duplicate_account_name"),
        (3, "account_name", "duplicate_account_name"),
        (4, "account_name", "duplicate_account_name"),
    }


def test_one_account_keeping_its_name_across_dates_is_not_a_warning():
    rows = [row(account_name="Synthetic Co", as_of_date=d) for d in ("2026-09-30", "2026-10-31")]
    assert preview(rows).warning_details == []


# --- the file ---------------------------------------------------------------------------------


def test_a_row_wider_or_narrower_than_the_header_is_rejected_alone():
    body = text([row()]) + "SYN-2,2026-10-31,active,10,USD,extra\nSYN-3,2026-10-31,active\n"
    body += "SYN-4,2026-10-31,active,10,USD\n"
    result = preview(raw=body.encode())
    assert codes(result) == {(3, None, "row_width_mismatch"), (4, None, "row_width_mismatch")}
    assert [s.account_external_id for s in result.preview] == ["SYN-0001", "SYN-4"]


def test_rows_are_numbered_as_a_spreadsheet_shows_them():
    """Header row 1; a blank line is a row; a quoted line break is not."""
    body = (
        "account_external_id,as_of_date,status,mrr,currency,notes\n"
        "\n"
        'SYN-1,2026-10-31,active,10,USD,"two\nlines"\n'
        ",,,,,\n"
        "SYN-2,2026-10-31,active,-1,USD,B\n"
    )
    result = preview(raw=body.encode())
    assert codes(result) == {(5, "mrr", "negative_value")}
    assert warning_codes(result) == {(2, None, "blank_row"), (4, None, "blank_row")}
    assert result.summary.blank_rows == 2
    assert result.summary.rows_processed == 2
    assert result.preview[0].row == 3


def test_a_byte_order_mark_does_not_rename_the_first_column():
    result = preview(raw="﻿".encode() + payload([row()]))
    assert result.status == "valid"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"", "file is empty"),
        (b"\n\n  \n", "file is empty"),
        (payload([row()]).splitlines(keepends=True)[0], "header but no data rows"),
        (b'account_external_id,as_of_date\n"SYN-1,2026-10-31\n', "could not be read as CSV"),
        (b'account_external_id,as_of_date\n"SYN-1"x,2026-10-31\n', "could not be read as CSV"),
        (b"account_external_id,mrr\nSYN-1,\x0010\n", "NUL bytes"),
        ("account_external_id,mrr\nSYN-1,10\n".encode("utf-16"), "NUL bytes"),
        (b"account_external_id,mrr\nSYN-\xe9,10\n", "not UTF-8"),
        (b"account_external_id;as_of_date;status\nSYN-1;2026-10-31;active\n", "semicolon"),
    ],
)
def test_a_file_that_cannot_be_read_is_refused_whole(body, reason):
    assert reason in refused(raw=body)


def test_the_column_and_row_limits_refuse_the_whole_file():
    narrow = Limits(max_columns=5, max_rows=2)
    assert preview([row(), row(account_external_id="SYN-2")], limits=narrow).status == "valid"
    assert "the limit is 5" in refused([row(segment="x")], limits=narrow)
    rows = [row(account_external_id=f"SYN-{i}") for i in range(3)]
    assert "more than 2 data rows" in refused(rows, limits=narrow)


def test_blank_rows_do_not_count_against_the_row_limit():
    body = text([row()]) + "\n\n\n"
    assert preview(raw=body.encode(), limits=Limits(5, 1)).summary.blank_rows == 3


def test_problems_beyond_the_limit_are_counted_not_listed():
    rows = [row(account_external_id=f"SYN-{i}", mrr="-1") for i in range(MAX_ISSUES + 30)]
    result = preview(rows)
    assert len(result.problems) == MAX_ISSUES
    assert result.problems_truncated and result.problems_omitted == 30
    assert [p.row for p in result.problems] == list(range(2, MAX_ISSUES + 2))


def test_warnings_beyond_the_limit_are_counted_not_listed():
    body = text([row()]) + "\n" * (MAX_ISSUES + 7)
    result = preview(raw=body.encode())
    assert result.summary.warnings == MAX_ISSUES + 7
    assert len(result.warning_details) == MAX_ISSUES
    assert result.warnings_truncated and result.warnings_omitted == 7


# --- no value from the file in any message, response or log -----------------------------------

SECRET = "SECRET-7f3a"


def _secret_rows() -> list[dict[str, str]]:
    """One row per field, each holding SECRET where the field rejects it."""
    bad = {
        "account_external_id": f"{SECRET}@synthetic.example",
        "as_of_date": f"2026-{SECRET}",
        "status": SECRET,
        "mrr": SECRET,
        "currency": SECRET,
        "account_name": SECRET * 40,
        "customer_since": SECRET,
        "renewal_date": SECRET,
        "canceled_at": SECRET,
        "cancellation_reason": SECRET,
        "segment": SECRET * 10,
        "company_size_band": SECRET,
        "industry": SECRET * 10,
        "active_users": SECRET,
        "seats_purchased": SECRET,
        "sessions_30d": SECRET,
        "key_actions_30d": SECRET,
        "last_activity_at": SECRET,
        "usage_period_start": SECRET,
        "usage_period_end": SECRET,
    }
    assert set(bad) == {f.name for f in contract.FIELDS}
    return [
        {**row(account_external_id=f"SYN-{i}"), name: value, "notes": SECRET, "email": SECRET}
        for i, (name, value) in enumerate(bad.items())
    ]


def test_no_rejected_value_reaches_the_response_or_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    result = preview(_secret_rows(), status_mapping_text=json.dumps({"unpaid": "past_due"}))
    assert result.summary.rejected == len(contract.FIELDS)
    assert result.preview == []
    assert SECRET not in result.model_dump_json()
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        f'account_external_id,mrr\n"{SECRET},10\n'.encode(),
        f'account_external_id,mrr\n"{SECRET}"x,10\n'.encode(),
        f"account_external_id;{SECRET}\nSYN;1\n".encode(),
        f"{SECRET},{SECRET}\n".encode(),
        f"account_external_id,mrr\n{SECRET}\x00,1\n".encode(),
        f"account_external_id,mrr\n{SECRET}\xff,1\n".encode("latin-1"),
    ],
)
def test_no_value_of_a_refused_file_reaches_the_refusal_or_the_log(body, caplog):
    caplog.set_level(logging.DEBUG)
    reason = refused(raw=body)
    assert SECRET not in reason
    assert SECRET not in caplog.text


def test_no_value_of_a_refused_mapping_reaches_the_refusal():
    explicit = json.dumps({f"{SECRET}@synthetic.example": "mrr"})
    reason = refused([row()], mapping_text=explicit)
    assert SECRET not in reason
