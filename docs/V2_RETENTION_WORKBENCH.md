# V2 Customer Intelligence and Retention Workbench (M2)

The second piece of ChurnSense V2. Three read-only endpoints answer the questions
a Customer Success team asks first: which accounts need attention, why, and how
much recurring revenue sits with them.

**Everything here is synthetic.** The accounts come from a deterministic
generator, not from a customer, an upload or the V1 telecom dataset. Nothing is
evidence of model performance or of business outcomes.

**Rules, not a model.** Every assessment is `method: "rules"` with a
`method_version`. Nothing is trained, nothing is a probability, and no response
has a probability field. The thresholds are initial operational heuristics. They
were not learned from, or validated against, real churn outcomes. A signal says
what was observed. It does not identify a cause of churn, and no response
recommends a discount or an offer as if it were proven to work.

The code is in `src/churnsense/workspace/`, apart from V1:

| Module | What it does |
|---|---|
| `snapshot.py` | The canonical `account_snapshot.v0` record, typed and immutable |
| `synthetic.py` | The deterministic synthetic dataset |
| `risk.py` | The rules, their thresholds (`RULES`) and `METHOD_VERSION` |
| `portfolio.py` | Exact per-currency MRR totals |
| `workbench.py` | Filters, ordering, pages, account detail and the summary |
| `workbench_schemas.py` | The typed responses |
| `workbench_api.py` | The three routes |

## Switching it on (local only)

It uses the Data Hub preview's switch and no other. The routes are served only
when `CHURNSENSE_V2_DATA_HUB_PREVIEW` is exactly `1` **and** `deployment.mode` is
`local`. They are installed with the preview, or not at all.

```bash
CHURNSENSE_V2_DATA_HUB_PREVIEW=1 .venv/bin/uvicorn churnsense.api.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

In a public demo (`configs/deploy.yaml`) the routes are not added, even with the
variable set. They are not routed and not in the OpenAPI schema. Each route also
checks the mode again on every request. `configs/deploy.yaml`, `deploy/Caddyfile`,
Docker Compose and the deployment scripts are unchanged. The committed
`frontend/openapi.json` is exported with the switch off, so it has no V2 path.

Every V1 layer stands in front of the routes:

- loopback peers only;
- the allowed hosts and origins, and the cross-site check on `/api`;
- the 64 KiB body limit, since these routes take no upload;
- the security headers and the CSP.

The list and the summary also spend the per-client `query` budget, because they
compute over the whole dataset on every request, like V1's customer list. The
local configuration sets no `query` budget, so locally they are not throttled.
One account is generated on its own, so the detail route is not budgeted, like
V1's single customer.

## The synthetic dataset

`synthetic.generate(accounts=500, seed=20261122, as_of=2026-10-31)` is the
dataset every route serves. It is generator version `synthetic_saas.v1`.

- **Reproducible.** The same seed, count and date give the same accounts in any
  process. Each account draws from its own `random.Random`, seeded from the
  seed, the generator version and the account number (not from `hash()`). One
  account can be generated alone and is the same whatever the count. Nothing is
  cached, stored or shared between calls.
- **Shape.** 500 accounts, each with 4 snapshots 30 days apart. The last snapshot
  is on `as_of`. Each snapshot's 30-day usage window ends on its own date, so
  consecutive windows are adjacent and do not overlap. Every snapshot passes the
  Data Hub's validation with no warning (a test runs all 2,000 through it).
- **Plainly fake.** IDs read `DEMO-ACC-000001`. Names read
  `Synthetic Cobalt Labs #1`. There is no email address, phone number, contact
  or address field. There is no label, outcome, probability, campaign or result.
- **Variation.** Steady and expanding usage, declining usage, a recent stop in
  activity, overdue payments, upcoming renewals, low active-user coverage, and
  trialing, paused and canceled accounts. Accounts are in USD, EUR or GBP, in
  three segments with size bands and industries.
- **Renewals** are yearly, on the anniversary of `customer_since`. About one paid
  account in ten has no stated renewal date.
- **Simplification.** A trialing account is trialing at all four snapshots, as a
  long pilot would be.

## The rules (`workbench_rules.v1`)

The thresholds live in one place, `risk.RULES`. Every rule's text in a response
is written from them (`Rules.text`), so no endpoint repeats a number. Every
comparison is made in whole numbers, so rounding never decides a level. A
percentage shown to a reader is cut to one decimal place and never rounded up, so
29.99% shows as 29.9% and reads as watch.

**Who is assessed.** Only `active` and `past_due` accounts are assessed.
`trialing`, `paused` and `canceled` accounts get `eligibility: "not_assessed"`,
`level: null` and a stated reason. They are never shown as accounts without risk,
and no renewal urgency is ever read from them.

| Code | Reads | Attention | Watch |
|---|---|---|---|
| `payment_past_due` | `status` | `past_due` | — |
| `renewal_due` | days from `as_of_date` to `renewal_date` | 0–30 | 31–60 |
| `inactivity` | days from `last_activity_at` to `as_of_date` | 30 or more | 14–29 |
| `sessions_decline` | `sessions_30d` against the preceding 30 days | a fall of 30% or more | 15% to under 30% |
| `key_actions_decline` | `key_actions_30d` against the preceding 30 days | a fall of 30% or more | 15% to under 30% |
| `low_active_user_coverage` | `active_users / seats_purchased` | below 10% | 10% to below 25% |

- **Usage trend.** The baseline is the latest earlier snapshot 30 to 45 days
  older: its window is the preceding period and does not overlap. There is no
  indicator if no such snapshot exists, if a count is missing, or if the earlier
  count is zero. Each of those cases is listed in `rules_not_applied` with its
  reason. Nothing is divided by zero and no baseline is invented.
- **Coverage** is active-user coverage of purchased seats. It is not verified
  seat utilisation. It needs seats above zero and an active-user count.
- **Aggregation.** Any attention indicator makes the account `attention`.
  Otherwise any watch indicator makes it `watch`. Otherwise it is `no_signals`.
  Indicators are listed attention first, then in the table's order.
- **Each indicator** carries its `code`, `severity`, `title`, a plain
  `explanation`, the `observed` value and `unit`, the `rule` in words, the
  `observed_on` date, `compared_with` (for a trend), and the `evidence`: each
  canonical field it read, with its snapshot date and value.
- **History never reads the future.** `risk.assess` takes the snapshot and only
  earlier snapshots of the same account. It refuses a later or a foreign one.

## Money

- Amounts are `Decimal` end to end and leave as JSON strings (`"1250.50"`).
  Sums run in the Data Hub's exact context, which raises rather than rounds.
- **No total adds two currencies.** The summary has one record per currency,
  holding `active_mrr`, `attention_mrr`, `watch_mrr`, `no_signals_mrr` and
  `exposed_mrr`, the account counts by level, and the eligible and excluded
  counts. Account counts may be added across currencies; amounts may not.
- **`exposed_mrr = attention_mrr + watch_mrr`.** It is the gross monthly recurring
  revenue of the accounts showing attention or watch signals. It is **not**
  predicted revenue loss, expected churn loss, revenue saved, retention impact or
  recoverable revenue.
- `sort=mrr_desc` needs a `currency` filter. Without one it is a 422, because
  amounts in different currencies are not comparable.

## The endpoints

### `GET /api/v2/portfolio/summary`

This endpoint takes no parameters. It returns:

- the totals: accounts, eligible and excluded accounts, not-assessed accounts by
  status, and counts by level;
- `currencies`, as described under Money;
- `renewals`: assessed accounts renewing in 0–30 and in 31–60 days;
- `methodology`: the method, its version and every rule in words;
- `definitions` and `notes`.

### `GET /api/v2/accounts`

The prioritised account list.

| Parameter | Values |
|---|---|
| `level` | `attention`, `watch`, `no_signals`, `not_assessed`; repeatable |
| `status` | the contract's statuses; repeatable |
| `currency` | three uppercase letters |
| `segment` | an exact segment name, up to 64 characters |
| `renewal_within_days` | 0–366: assessed accounts renewing within that many days |
| `q` | up to 100 characters, case-blind, matched in the account ID or name |
| `sort` | `priority` (default), `renewal_asc`, `mrr_desc` (needs `currency`) |
| `page`, `page_size` | `page` from 1; `page_size` from 1 to 100, default 25 |

- **`priority` order.** Attention, then watch, then no signals, then not assessed.
  Within a level, the nearest valid renewal comes first and accounts without one
  come last. The account ID breaks every tie.
- **Strict parameters.** An unknown parameter, a single-valued parameter given
  twice, or an unsupported value is refused with a 422. A misspelt filter
  therefore never returns an unfiltered list.
- **Pages.** A page past the end returns the last page.
- **Each row** holds the account's identity, status, segment, MRR and currency,
  its renewal and `days_to_renewal`, the assessment with its indicators, and
  `next_step_context`. The context names what the leading signal points to. It
  does not name an offer or an action. There is no owner, task, case or playbook
  state: those belong to the Action Center.

### `GET /api/v2/accounts/{account_id}`

One account. It returns:

- the latest snapshot and its `subscription` (status, MRR, currency, tenure,
  renewal, cancellation);
- the current assessment and `next_step_context`;
- the `history`: all four snapshots, oldest first, each assessed with only itself
  and earlier snapshots.

An ID the dataset does not have returns the service's structured 404:
`{"error": "not_found", "detail": "No account with that ID.", "problems": []}`.

## Trying it

Start the server as shown above, then:

```bash
curl -s http://127.0.0.1:8000/api/v2/portfolio/summary | python3 -m json.tool
curl -s 'http://127.0.0.1:8000/api/v2/accounts?level=attention&page_size=5' | python3 -m json.tool
curl -s 'http://127.0.0.1:8000/api/v2/accounts?sort=mrr_desc&currency=EUR&page_size=5' | python3 -m json.tool
curl -s 'http://127.0.0.1:8000/api/v2/accounts?renewal_within_days=30&status=past_due' | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/v2/accounts/DEMO-ACC-000001 | python3 -m json.tool
curl -s -i http://127.0.0.1:8000/api/v2/accounts/DEMO-ACC-999999
```

## Not here

M2 does not include:

- authentication, organisations or persistence;
- imports kept from the Data Hub;
- native integrations;
- Action Center work items or playbooks (M3 adds their contracts only:
  `docs/V2_RETENTION_ACTION_CENTER.md`);
- campaigns, intervention outcomes or experiments (M4 adds outcome and experiment
  contracts only: `docs/V2_RETENTION_IMPACT_EXPERIMENTS.md`);
- a SaaS model;
- UI pages (M5 adds a local, opt-in preview front end over these reads:
  `docs/V2_PREVIEW_WORKSPACE.md`).

The V1 telecom model is never applied to these accounts.
