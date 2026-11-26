# V2 Preview Workspace (M5)

The first front end of ChurnSense V2: a local, opt-in workspace over two V2
pieces that are already served.

| Page | Address | What it reads |
|---|---|---|
| Preview home | `/v2` | Nothing. It explains the two tools. |
| Data Hub | `/v2/data-hub` | `POST /api/v2/imports/preview` (M1, [V2_DATA_HUB_PREVIEW.md](V2_DATA_HUB_PREVIEW.md)) |
| Portfolio | `/v2/portfolio` | `GET /api/v2/portfolio/summary` (M2, [V2_RETENTION_WORKBENCH.md](V2_RETENTION_WORKBENCH.md)) |
| Accounts | `/v2/accounts` | `GET /api/v2/accounts` (M2) |
| Account | `/v2/account?id=…` | `GET /api/v2/accounts/{account_id}` (M2) |

The preview asks the API for these four routes and nothing else. The Action
Center (M3) and Impact & Experiments (M4) are contracts with no routes, and the
preview has no page, link or request for either.

**Local only.** The preview is switched off in every default and deployed build.
It runs on this machine, against a local API: on the Vite dev server, or from a
build of its own ([V2_PREVIEW_RUNTIME.md](V2_PREVIEW_RUNTIME.md)).

**Nothing is stored.** No page saves anything, in the browser or on the server.

## Switching it on

Two switches, both needed:

1. The API's V2 routes: `CHURNSENSE_V2_DATA_HUB_PREVIEW=1`, in a local
   deployment only (see [V2_DATA_HUB_PREVIEW.md](V2_DATA_HUB_PREVIEW.md)).
2. The front end's V2 pages: `VITE_CHURNSENSE_V2_PREVIEW=1`, read by Vite when it
   builds or serves the app.

```bash
# terminal 1: the API, with its V2 routes on (local deployment)
CHURNSENSE_V2_DATA_HUB_PREVIEW=1 make api

# terminal 2: the dev server, with the V2 pages on; it forwards /api to port 8000
cd frontend
VITE_CHURNSENSE_V2_PREVIEW=1 npm run dev
```

Then open <http://127.0.0.1:5173/v2>. The dashboard's navigation also gets one
link, **V2 Preview**, under a "Preview" group. With the API's switch off, the
preview's pages say that the local server does not serve the preview.

`make api` serves the built app (`frontend/dist`) with a fixed list of V1 pages
(`src/churnsense/api/spa.py`), so it answers `/v2` with its Not found page. A
default build has no V2 code in any case (next section).

M6 added one command that does both steps and checks them (`make v2-preview`), and
a separate V2 Preview build with a local server of its own. Both are described in
[V2_PREVIEW_RUNTIME.md](V2_PREVIEW_RUNTIME.md); the API's page list is unchanged.

## How it stays off

`VITE_CHURNSENSE_V2_PREVIEW` is a build-time constant. With any value other than
`1` (or none), the branches it guards are constant false, and the bundler drops
them along with every V2 module that only they load:

- `src/app/App.tsx` loads the workspace (`src/v2/Workspace.tsx`) lazily, behind
  `import.meta.env.VITE_CHURNSENSE_V2_PREVIEW === '1'`. The test is written in
  that file itself, because the bundler drops a dynamic import only behind a
  condition it sees in the same module.
- `src/components/shell/Sidebar.tsx` shows the V2 link only when `V2_PREVIEW`
  (`src/v2/flag.ts`) is true.

The checks:

- `tests/test_workspace_workbench_api.py` fails if any front-end file outside
  `src/v2/` asks for `/api/v2` or names a V2 figure. It also fails if a file
  other than those two imports the preview, or if the lazy import leaves the
  switch. When `frontend/dist` exists, it fails if the build holds a V2 page,
  link or request. CI's Python job has no build, so there this check skips; the
  built bundle is checked in CI by `e2e/v2-off.spec.ts` (below).
- `src/v2/gating.test.tsx` loads the app as each build would. With the switch
  off, every `/v2` address is the dashboard's Not found page, the navigation has
  no V2 link, and no request goes to `/api/v2`. With it on, the link and the
  workspace are there and the V1 pages are unchanged.
- `e2e/v2-off.spec.ts` checks the served build in a browser: no link, a 404 Not
  found at every `/v2` address, and no `/api/v2` request.

The `Dockerfile`, Caddy, Compose and the deployment configuration are unchanged,
and none of them sets `VITE_CHURNSENSE_V2_PREVIEW`.

## Two separate tools

The workspace holds two tools that share nothing:

- **Data Hub: your file.** A CSV is sent to the local server, checked in memory
  and discarded with the response. Nothing is imported, stored or committed
  (`committed: false`).
- **Portfolio and Accounts: synthetic demo data.** The Workbench reads its own
  generated dataset (`dataset.source: "synthetic"`, generator
  `synthetic_saas.v1`, seed 20261122, 500 accounts, four snapshots 30 days
  apart, snapshot date 31 October 2026). Every Workbench page shows a banner
  built from the response's `dataset`: "Synthetic demo data", the snapshot date
  written out ("31 October 2026"), the number of generated accounts, and "Not
  your uploaded file, not real customers".

**A checked file never populates the Workbench.** The API keeps nothing between
requests, so nothing connects the two. The preview home says this
("Two separate tools."), and so do the Data Hub's own text ("does not feed the
portfolio demo") and the Workbench's banner. The end-to-end test checks a file
whose account IDs exist only in that file, then confirms that none of them
appears in the account list.

The rules are transparent operating heuristics, not a model: there is no
probability. The pages say so, and show the API's own methodology statement and
rule texts.

## The browser computes nothing

Every figure on a V2 page is the response's own: counts, levels, indicators,
the order of the list, renewal windows, MRR per currency, exposed MRR,
accepted and rejected rows, MRR totals of an upload, and the mapping and its
match kinds. The pages do no arithmetic on them.

What the browser does:

- **Formats values.** It writes a date out (`2026-10-31` becomes
  "31 October 2026" or "31 Oct 2026", in UTC). It writes a count with
  separators. It writes an amount from the API's decimal text with its
  currency (`"2890.38"`, `EUR` becomes "€2,890.38"). `Intl` reads the text as an
  exact decimal and keeps up to four decimals, as the contract does. A code
  `Intl` has no symbol for is written as the code ("XYZ 2,890.38"). A code it
  cannot read at all is shown as given ("2890.38 XY").
- **Labels.** It turns codes into words (`past_due` becomes "Past due",
  `no_signals` becomes "No signals"). An indicator's observed value gets its unit
  (`"0"` with `days` becomes "0 days").
- **Gathers choices.** It gathers the reader's mapping, status translations and
  filters, and sends them. The server decides whether they are valid.

Amounts in two currencies are never added, on screen or anywhere else. "Highest
MRR" ordering needs one currency: the option stays disabled until a currency is
chosen, and clearing the currency returns the order to priority. The API also
refuses `sort=mrr_desc` without a currency. Exposed MRR is described as the API
defines it: the gross MRR of the accounts with an attention or watch signal. It
is not a forecast of lost revenue, and not revenue saved or recoverable.

## The Data Hub flow

1. **Choose a file.** Use the drop zone, or click or tab to it. The file is sent
   alone, with no mapping, so the server applies its own suggestions (exact and
   normalised name matches). The browser never reads the file's contents. It
   keeps the `File` in the page's memory only, so the same file can be checked
   again.
2. **Read the verdict.** The server returns `valid`, `partially_valid`, `invalid`
   or `mapping_required`. The page shows:
   - the status in words and the row counts;
   - MRR per snapshot date and currency, plus how many totals were left out;
   - each listed problem and warning, plus how many more the server left out;
   - the first accepted rows and the server's notes.
3. **Change the mapping.** Each mappable column (the applied and the ignored
   ones) has one choice: a contract field or "Not read". Required fields are
   marked. The unresolved and missing required fields are listed. Columns
   dropped as personal identifiers are listed as "Never read".
4. **Translate status values.** Add up to 100 pairs, the server's limit (for
   example `live` read as `active`). A row left empty, or holding only spaces,
   is not sent.
5. **Check again.** The same file is sent again with an explicit mapping. It is
   keyed by the column names the server reported, in column order, and is the
   complete mapping (a column set to "Not read" is left out). The status
   translations go with it if there are any. The mapping is written pair by
   pair, so a name given twice reaches the server, which refuses it, rather than
   being silently merged by the browser.

Each state:

| State | What the reader sees |
|---|---|
| Checking | A placeholder; "Choose another file" abandons the check |
| `422` on the first check | "The server could not read this file." with the server's reasons |
| `422` on a changed mapping | "The server refused this mapping." with its reasons; the last result stays |
| `413` | The file is larger than the local server accepts |
| `404` | The local server does not have the Data Hub preview on |
| `429`, `503` or no answer | A retry with the same file |

## Accounts

The filters live in the address, so a view can be linked and Back undoes a
change:

- `level` and `status` (repeatable);
- `currency`, whose options come from the summary's currencies;
- `segment` (the exact name);
- `renewal_within_days` (30, 60, 90, 180 or 366, or the address's own value);
- `q` (sent once typing pauses);
- `sort`, `page` and `page_size` (10, 25, 50 or 100).

Values outside the API's bounds are dropped when the address is read. A filter
the API still refuses (`422`) is shown with the API's reasons and a way to clear
the filters. An empty result offers the same. The attention, watch, no-signals
and not-assessed tiles of the portfolio link to the list filtered by that level.

An account's page shows:

- the subscription and the latest snapshot;
- each signal with its rule, its observed value and its evidence;
- the rules that could not be applied and why;
- the API's "where to look first" context;
- every snapshot with the level the rules gave it then.

An unknown ID gets "There is no such account" (`404`). With no ID, the page asks
for nothing.

## Types

`frontend/openapi.v2.json` and `src/api/v2.schema.d.ts` are generated from the
API with its V2 routes on:

```bash
cd frontend && npm run openapi:v2
```

`frontend/openapi.json` and `src/api/schema.d.ts` stay as they were: exported
with the switch off, with no V2 path. CI regenerates both and fails on any
difference. The canonical fields the Data Hub offers (`src/v2/fields.ts`) are
checked at compile time against the generated `AccountSnapshot`. A field added
to or removed from the contract fails the type check until the list matches.

## Tests

- **Vitest** (`src/v2/`), with MSW handlers typed by the V2 schema and fixtures
  shaped on the local API's real responses:
  - `gating.test.tsx`: both builds;
  - `pages/DataHubPage.test.tsx`: the upload, the first and second forms, the
    mapping and status translations, the 100-row limit, the results and each
    error state;
  - `pages/WorkbenchPages.test.tsx`:
    - the portfolio's banner, figures and links;
    - the list's rows, every filter, the sort, the page and an address read
      back;
    - the empty, refused, failed and loading states;
    - the account's detail, 404 and no-ID states;
    - that every request is an M1 or M2 route;
  - `source.test.ts`: every V2 source file checked for:
    - no style attribute or HTML string, as the CSP requires;
    - no causal wording (the project's `CAUSAL` pattern);
    - no M3 or M4 name, and no browser storage;
    - no reading of an uploaded file;
    - only its own pages and the M1 and M2 routes under `/v2`.
- **Playwright**, project `v2-preview` (`e2e/v2-preview.spec.ts`). It uses a
  third API server, with its V2 routes on, behind a Vite dev server built with
  the preview on. `e2e/global-setup.ts` proves that the server's Workbench is on
  and synthetic. The tests:
  - **The smoke test:** a file with non-standard column names needs a mapping,
    is mapped and translated, and passes. Then come the portfolio with
    "31 October 2026", the attention list by currency and MRR, and one account.
    The test also checks that the file's accounts are not in the list. axe runs
    on every page, and only M1 and M2 routes are asked for. The same spec runs
    on the V2 Preview's build (project `v2-built`,
    [V2_PREVIEW_RUNTIME.md](V2_PREVIEW_RUNTIME.md)).
  - **The phone drawer**, with axe.
- **pytest:** the front-end checks in `tests/test_workspace_workbench_api.py`
  (above).

The shared Playwright guard (`e2e/fixtures.ts`) fails a test for a request sent
again while the first is in flight. On the dev server, React's StrictMode mounts
each component twice, and the first mount's request is aborted as the second's
is sent. For the `v2-preview` project only, a repeated GET is therefore excused
once for each copy of it that the page cancelled. A third copy, or a repeat
with no copy cancelled, still fails. Every other project is checked as before.

## Limitations

- **Not served by the API.** The built app that `make api` serves has no `/v2`
  (above).
- **Some columns cannot be mapped.** A column whose header is itself an email
  address or a phone number is reported as `[column N]`. An explicit mapping is
  keyed by the real header, so it cannot name such a column. The page shows the
  column, disables its choice and says why. To use it, rename the header in the
  file. This is the M1 contract, and M5 does not change it.
- **The API does not list the contract's fields.** The Data Hub's list is
  compiled from the generated schema (above). It is not read from the server at
  run time.
- **Each check sends the file again.** Nothing is kept between requests, so a
  changed mapping is checked by sending the file once more.
- **Synthetic data only.** The Workbench pages show only the generated
  portfolio. An uploaded file never reaches them.

## Not here

M5 does not add:

- persistence, authentication or multi-tenancy;
- onboarding of real customer data, or Stripe and CRM integrations;
- M3 task execution or M4 experiment execution;
- email or notifications, autonomous agents or LLM features;
- new routes, or any change to an M1 or M2 response;
- any change to the V1 model, its training or its inference;
- any production, Docker, Caddy, VPS or deployment change.
