# V2 Data Hub: import preview (M1)

The first piece of ChurnSense V2, the retention platform for B2B SaaS companies.
It takes a CSV of customer-account snapshots, maps its columns to the canonical
contract, checks every row, and reports what the file would import.

**It is a preview only.** Nothing is stored: no database, no file, no cache, no
import history, no saved mapping, and there is no endpoint that commits anything.
The upload lives in memory for one request and is gone when the response is sent.
**Use synthetic or test data only.** The preview has no authentication, no tenants
and no persistence, so it is not a place for real customer records.

The code lives in `src/churnsense/workspace/`, apart from V1's telecom domain: it
does not import V1's contract, book, model or evaluation code, and V1 does not
depend on it.

## Switching it on (local only)

The route is **off by default**. It is served only when both hold:

- the environment variable `CHURNSENSE_V2_DATA_HUB_PREVIEW` is exactly `1`;
- the configuration's `deployment.mode` is `local` (as in `configs/config.yaml`).

```bash
CHURNSENSE_V2_DATA_HUB_PREVIEW=1 .venv/bin/uvicorn churnsense.api.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`make api` runs the same server, so `CHURNSENSE_V2_DATA_HUB_PREVIEW=1 make api`
works too. No configuration file changes. When the preview is on, the log says
so at startup and `/docs` lists `POST /api/v2/imports/preview`.

**A public demo never serves it.** With `deployment.mode: public_demo`
(`configs/deploy.yaml`), the route is not added to the application even when the
variable is set. It is not routed, not in the OpenAPI schema and not in `/docs`.
The service logs an error and starts as usual. The route also checks the mode
again on every request. Any value other than `1` (`true`, `yes`, `0`) leaves the
preview off.

Where it is served, every V1 layer still stands in front of it:

- loopback peers only (`LoopbackOnly`);
- the allowed hosts and origins (`TrustedHosts`);
- the upload body limit (`BodySizeLimit`), which applies to this path only while
  the route is installed. Everywhere else the path gets the 64 KiB limit.
- the per-client `batch` budget (`RateLimit`);
- one of the service's two batch slots: past them, 429 at once.

The CSP and the security headers are the service's own. `deploy/Caddyfile` and
`configs/deploy.yaml` are not changed.

## Trying it

```bash
cat > /tmp/synthetic_accounts.csv <<'CSV'
Account External ID,As Of Date,Status,MRR,Currency,account_name,renewal_date,contact_email
SYN-0001,2026-10-31,active,1250.50,USD,Synthetic Co 1,2027-01-31,someone@synthetic.example
SYN-0002,2026-10-31,Past_Due,99.00,EUR,Synthetic Co 2,2026-11-15,
SYN-0003,2026-10-31,canceled,0,USD,Synthetic Co 3,,
SYN-0001,2026-10-31,active,10,USD,Synthetic Co 1,,
CSV

curl -s http://127.0.0.1:8000/api/v2/imports/preview \
  -F "file=@/tmp/synthetic_accounts.csv;type=text/csv" | python3 -m json.tool
```

Expected outcome:

- the headers are matched by normalised name;
- `contact_email` is dropped by name, and none of its values are returned;
- `Past_Due` is read as `past_due`, and a note says so;
- row 4 is rejected because `canceled_at` is missing for a cancelled account;
- rows 2 and 5 are both rejected as the same account on the same date.

To send an explicit mapping (and a status translation):

```bash
curl -s http://127.0.0.1:8000/api/v2/imports/preview \
  -F "file=@/tmp/synthetic_accounts.csv;type=text/csv" \
  -F 'mapping={"Account External ID":"account_external_id","As Of Date":"as_of_date","Status":"status","MRR":"mrr","Currency":"currency"}' \
  -F 'status_mapping={"unpaid":"past_due"}' | python3 -m json.tool
```

## The contract: `account_snapshot.v0`

**One row is one customer account at one point in time.** The key is
`account_external_id` + `as_of_date`. An account may appear on several dates,
never twice on the same date.

### One row per account, not per subscription

An account with several subscriptions is one row. The aggregation happens before
upload (a future integration may do it; nothing here does):

- `mrr` is the **sum of the monthly recurring revenue of every active paid
  subscription** on `as_of_date`:
  - each subscription is normalised to a month first (an annual amount ÷ 12, a
    quarterly one ÷ 3);
  - past-due subscriptions count while they have not ended;
  - trials, paused and cancelled subscriptions do not count.
- Every subscription in a row is in the one reporting `currency`. Conversion
  happens upstream. Different currencies are never added, here or in any total.
- There is no account-level billing interval: subscriptions may bill on
  different intervals, and `mrr` is already monthly.
- A repeated account and date is an error. Rows are never merged.

### Status, aggregated over the account's subscriptions

| `status` | Meaning | `mrr` |
|---|---|---|
| `active` | at least one active paid subscription, none past due | above 0 |
| `past_due` | at least one active paid subscription has an overdue payment | above 0 |
| `paused` | no active paid subscription remains; a paid relationship is paused | 0 |
| `trialing` | no active paid subscription remains; the account is in a trial | 0 |
| `canceled` | the account had a paid relationship; its last active paid subscription has ended | 0 |

Trial non-conversion is not logo churn. Cancelling one of several paid
subscriptions while another stays active is contraction: the account stays
`active` with a lower `mrr`.

### Fields

Dates are ISO calendar dates, `YYYY-MM-DD`, between 1900-01-01 and 2199-12-31.
No other form is read, and a missing date is never filled in with the upload
date. Counts are whole numbers from 0 to 999,999,999.

| Field | Required | Type and rule |
|---|---|---|
| `account_external_id` | yes | text, 1–128 characters, trimmed, case-sensitive; not an email address or phone number |
| `as_of_date` | yes | date the snapshot describes |
| `status` | yes | one of the five statuses above |
| `mrr` | yes | plain decimal, at most 12 digits before the point and 4 after, no sign, symbol, separator or exponent; exact (`Decimal`), returned as a JSON string |
| `currency` | yes | three uppercase letters (`USD`) |
| `account_name` | no | text, up to 200 characters; not an email address or phone number |
| `customer_since` | no | date of the earliest known paid relationship; not after `as_of_date` |
| `renewal_date` | no | earliest upcoming renewal among active paid subscriptions; not before `as_of_date` |
| `canceled_at` | when canceled | date the last active paid subscription ended. Required when `status` is `canceled`, not allowed otherwise. Not after `as_of_date`, not before `customer_since`. |
| `cancellation_reason` | no | one of `too_expensive`, `budget_cut`, `missing_features`, `product_issues`, `support_experience`, `switched_to_competitor`, `low_adoption`, `business_closed`, `acquired_or_merged`, `payment_failure`, `other` |
| `segment` | no | text, up to 64 characters |
| `company_size_band` | no | one of `1-10`, `11-50`, `51-200`, `201-500`, `501-1000`, `1001-5000`, `5001+` |
| `industry` | no | text, up to 64 characters |
| `active_users` | no | count of distinct active users in the usage period (an aggregate, never a list) |
| `seats_purchased` | no | count of seats paid for on `as_of_date` |
| `sessions_30d` | no | count of sessions in the 30 days ending on `as_of_date` |
| `key_actions_30d` | no | count of key product actions (as the business defines them) in those 30 days |
| `last_activity_at` | no | date any user of the account was last active; not after `as_of_date` |
| `usage_period_start`, `usage_period_end` | no, but both or neither | the period `active_users` covers; start not after end, end not after `as_of_date` |

Text fields must not contain control characters. Surrounding spaces are trimmed
from every value; nothing else in a value is changed.

**Currency is checked for its form only.** No ISO 4217 registry exists in the
repository, so a well-formed code that is not a real currency (`XYZ`) passes.
The response says so in its notes.

## Column mapping

Without a `mapping`, every column is matched against the field names, two ways
only:

- **exact**: the column's name, trimmed, is the field name;
- **normalised**: the name as lowercase words joined by `_` is the field name.
  `Account External ID`, `accountExternalId` and `ACCOUNT-EXTERNAL-ID` all read
  `account_external_id`.

No fuzzy matching, synonyms or model is used. A field that two columns match is
**ambiguous** and is not chosen. Every unambiguous match is applied, and the
response lists it under `mapping.applied` with `source: suggested`. When a
required field is missing or ambiguous, the status is `mapping_required`: no row
is validated, and `mapping.suggestions` and `mapping.unresolved` say what to map.

With a `mapping` (a JSON object `{"source column": "field"}`), that mapping is
**complete and the only one used**. A column it does not name is ignored, even
if its name matches a field. It is refused as a whole (422) when it:

- names a column the file lacks, or a name the file repeats;
- names a personal column;
- names an unknown field, or one field twice (including the same key twice);
- leaves a required field out.

A `status_mapping` (a JSON object `{"source value": "status"}`, at most 100
entries) translates source status values exactly, case included. It cannot
retranslate a canonical status. Canonical statuses written in another case
(`Active`, `PAST_DUE`) are read as the canonical value, and the notes count
them.

### Personal columns

A column whose name says it holds a direct personal identifier is dropped before
mapping and can never be mapped. That covers names containing `email` or `phone`,
and words such as `mail`, `mobile`, `cell`, `tel`, `fax`, `address`, `street`,
`postcode`, `birth` or `dob`, and pairs such as `first name`, `last name`,
`contact name` or `postal code`. Only its name is returned
(`columns.dropped_personal`); its values are not read.

`account_external_id` and `account_name` values that are or contain an email
address, or that are a whole phone number (`+` and 7–15 digits, or the
`(555) 123-4567` form), are rejected. A column whose *name* looks like an email
address or phone number (a file without a header row) is shown as `[column N]`.

These checks narrow what reaches the service. They are not a guarantee: a column
can hide personal data under any name, and a phone number written as bare
digits looks the same as a numeric ID. That is why only synthetic or test data is
uploaded here.

## Validation

- **The whole file is refused (422, the service's `ErrorResponse`)** when:
  - it is empty, or has a header and no data rows;
  - it is not UTF-8, contains NUL bytes, or is semicolon-separated;
  - its quoting is malformed;
  - it has more columns than `api.max_upload_columns` (1,000) or more data rows
    than `api.max_batch_rows` (10,000);
  - the file is over `api.max_upload_bytes` (5 MiB);
  - the form or a mapping cannot be read.

  Every refusal lists its reasons.
- **Otherwise every row is checked and the response is a preview (200).** A row
  with any problem is rejected; the others are accepted. Every problem has `row`,
  `field`, `code` and `message`.
- **Rows are numbered as a spreadsheet shows them.** The header is row 1 and the
  first data row is row 2. A blank line is a row too; a line break inside a quoted
  value is not.
- **Blank rows** (or rows whose fields are all blank) are skipped and listed as
  warnings.
- **A row wider or narrower than the header** is rejected by itself
  (`row_width_mismatch`).
- **Every row of a repeated account and date is rejected**, each naming the
  others (`duplicate_account_snapshot`). None is kept silently.
- **An `account_name` shared by different accounts** is a warning, never a
  rejection.
- **Fields that do not apply to the status are warnings and kept as given:** a
  `renewal_date` on an account without an active paid subscription, or a
  `cancellation_reason` on one that is not cancelled.
- **Rules between fields** apply only to values that were read. A field that
  fails its own check is not checked against another.
- **No message repeats a value from the file**, and no value is logged. The log
  records counts only.

Codes:

- **errors:** `required`, `invalid_date`, `invalid_status`, `invalid_decimal`,
  `negative_value`, `invalid_currency`, `invalid_integer`, `invalid_choice`,
  `too_long`, `invalid_characters`, `personal_identifier_value`,
  `required_for_canceled`, `forbidden_for_status`, `mrr_inconsistent_with_status`,
  `renewal_before_snapshot`, `after_snapshot`, `canceled_before_customer_since`,
  `usage_period_incomplete`, `usage_period_order`, `row_width_mismatch`,
  `duplicate_account_snapshot`;
- **warnings:** `blank_row`, `duplicate_account_name`,
  `renewal_without_paid_subscription`, `reason_without_cancellation`.

## The response

`ImportPreviewResponse` holds:

- `status` (`valid`, `partially_valid`, `invalid`, `mapping_required`);
- `committed: false`;
- `contract_version`;
- the `mapping` report;
- the `ignored` and `dropped_personal` columns;
- a `summary`: rows processed, accepted, rejected, warnings, blank rows, and
  `mrr_totals`;
- `preview`: the first 20 accepted snapshots, normalised;
- up to 200 `problems` and up to 200 `warning_details`, each list with
  `*_truncated` and `*_omitted`;
- `notes` that state every assumption.

`mrr_totals` sums accepted `mrr` **per snapshot date and currency**, exactly
(a sum that would round raises instead). Adding one account's snapshots on
different dates would count the account twice, and adding currencies would add
unlike amounts, so no total spans either.

## Limits and what is not here

The limits are V1's own: 5 MiB per file, 10,000 data rows, 1,000 columns, one
file per request, two uploads scored at once in the process, and the per-client
`batch` budget. Starlette would spool an upload over 1 MB to a temporary file,
so the form is parsed by a parser whose spool never reaches the disk before the
body limit. Tests check this with an audit hook on every file the process opens.

Not in M1: authentication, organisations, persistence of any kind, import
commit or history, saved mappings, native integrations, risk scoring and UI
pages. The V1 telecom model is never applied to these snapshots.

M2 adds the Retention Workbench's reads behind the same switch
([V2_RETENTION_WORKBENCH.md](V2_RETENTION_WORKBENCH.md)). They read their own
synthetic dataset: nothing uploaded here reaches them, because nothing uploaded
here is kept.
