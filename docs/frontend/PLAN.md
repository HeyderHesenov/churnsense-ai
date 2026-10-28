# Front-end implementation plan

Approved 2026-10-21. The working record of how `docs/design/MASTER_PROMPT.md` is applied to this repository: decisions taken with the maintainer, the architecture, the phases and how each is verified.

## Context

The maintainer designed an 8-screen dashboard with Claude Design. Every file in the exported bundle was read:
- 8 artboards, plus `tools/src` (the same artboards before shell expansion);
- `canvas.json`, `mini-dc.js`, `build_shell.py`, `dc_check.js`, `export_all.py`, `scorer.js`, `risk.js`;
- the data files (structure checked), `README.md` and `MASTER_PROMPT.md`.

The goal is to reproduce the design 1:1 as production code, without weakening any repo guarantee: the single prediction path, withheld test outcomes, no causal language, SIMULATED money, and no typed numbers. The work principles also apply: verified, secure, optimal, light.

The prototype computed everything in the browser from exported JSON. Production renders only what the API returns.

**Decisions (asked 2026-10-21):**

| Topic | Decision |
|---|---|
| Test metrics | `make evaluate` also writes `reports/final_evaluation.json`. The maintainer runs `make evaluate CONFIRM=yes` once. Until then the UI shows a "not recorded" state. |
| Auth | Loopback-only now, enforced by the server. No login. |
| React | React 19. |
| Design in repo | Artboards plus `canvas.json` and `MASTER_PROMPT.md` go into `docs/design/` (the maintainer asked for "the most correct" option). The repo currently contains no real customer ID, and MODEL_CARD says the dataset is "not redistributed". So the 28 real rows embedded in two artboards (Main `QUEUE` 24, Customer `CUSTOMERS` 4) are replaced by synthetic `DEMO-` rows from `tests/fixtures`. This is documented. Aggregates stay, since the README already publishes them. Excluded: `preview/data/*.json` (the full dataset), `model.json`/`scorer.js` (the in-browser model) and `tools/`. |

**Facts that shaped the plan (verified):**
- `model_meta.json` has no `test_metrics`: `make train` on 2026-10-13 rewrote it.
- `shap_global.json` holds importance only.
- These live only in `app/`: book/provenance/outcome logic, CSV neutralisation, KPIs, `selection_table`.
- SHAP takes about 0.3 s per row when warm, and 2.4 s on the first call (numba JIT). It uses numpy's **global** RNG (`shap/explainers/_permutation.py:66,182`).
- `Mount("/")` matches everything with `Match.FULL` (`starlette/routing.py:426,694`).
- TestClient's default peer is `("testclient", 50000)`; it does accept `client=`.
- `reports/threshold_sweep.csv` plus `recommend_threshold_from` reproduce 0.36 and 0.88/0.50/0.36/0.23/0.22.
- The design's demo CSV is identical to `tests/fixtures/demo_customers.csv`.
- **Toolchain:** TS 5.9 (typescript-eslint and openapi-typescript need < 6) and ESLint 9 (jsx-a11y needs ≤ 9).

## Architecture

```
browser ──same origin──▶ FastAPI (uvicorn 127.0.0.1, --no-proxy-headers)
  middleware (outer→inner): SecurityHeaders → LoopbackOnly → TrustedHosts+SameOrigin → RateLimit → BodySizeLimit
  ├─ /api/*                     new JSON/CSV endpoints + /api aliases of the 4 legacy ones
  ├─ /health /model-info /predict /predict/batch   legacy paths, unchanged handlers
  ├─ /docs /redoc /openapi.json docs-only CSP
  ├─ GET / /explorer /patterns /performance /explainability /customer /simulator /batch → index.html
  └─ GET /assets/{file}         hashed bundle files (regex-checked name, immutable cache)
library: predict.py (only inference path) · book.py · insights.py · exports.py · analysis.py · evaluation/* · explainability/*
```

**Serving the SPA:**
- explicit routes, never `Mount("/")`, so 404, 405 and redirects keep working;
- the directory comes from a module-level `FRONTEND_DIST`, read at request time (the pattern `ARTIFACTS_DIR` already uses);
- if nothing is built, SPA routes answer 404 `frontend_not_built`.

## Phase 0 — setup (after approval)

- Create branch `feature/frontend-design` from `main` 883492f. Nothing is committed or pushed until the maintainer asks; landing is via PR per the protected-main rule.
- Copy the artboards to `docs/design/` with the synthetic-row substitution, and write `docs/design/README.md`.
- Write `docs/frontend/PLAN.md` (this plan).
- Add `frontend/node_modules`, `frontend/dist`, `playwright-report/` and `test-results/` to `.gitignore`.

## Phase 1 — backend (TDD; library first, then endpoints)

### 1a. Move the shared logic into the library (no behaviour change)

**`src/churnsense/book.py`** — moved from `app/data.py`:
- `Book`, `score_book`, `verified_split`, `is_sha256`, `labelled(frame, *parts, shown)`, `WITHHELD_REASONS`, `SHOWN_PARTITIONS`;
- `outcome_codes()`: `churned`/`stayed`/`withheld`, the one mapping;
- `selection_table()`.

Supporting changes:
- `app/data.py` keeps thin `st.cache_*` wrappers. `labelled(frame, *parts)` defaults `shown` to `outcomes()`, and the names that tests and conftest import are re-exported.
- Streamlit maps the outcome codes to its display words.
- The governance allowlist swaps `app/data.py` for `book.py`.

**`src/churnsense/exports.py`** — `neutralise_formulas`, `csv_bytes`, `FORMULA_TRIGGERS`, moved from `app/components.py` and re-exported there.

**`src/churnsense/insights.py`** — pure aggregates over a `Book`:
- `overview()`: KPIs; band table with train+val observed churn; 20-bin histogram with train+val observed churn per bin; campaign figures from the **validation** sweep;
- `lifecycle()` and `segment_rates()`: train only;
- `calibration_by_band()`: train+val;
- `customer_page()`: filter, sort, paginate, selection summary;
- `example_customers()`: a deterministic rule, so no IDs are committed. It returns the README example, then the validation customer at the median probability of each case: Low/unflagged, Moderate/unflagged, Moderate/flagged, High/flagged. This is the design picker's structure.
- `DIMENSIONS`: 14 whitelisted dimensions with the design's fixed level order.

**`analysis.cross_tab()`** — tidy cells: customers, churned, rate, or suppressed when n < 20, plus lift and marginals. `risk_concentration` is kept as is, with a test that the two agree.

**Never exposed:** `DataSplits.summary()`, which contains the test base rate, and any all-rows outcome aggregate.

### 1b. Caches and sidecar

**SHAP:**
- New `shap_matrix()` returns values plus the base value. `global_importance` is derived from it, and the `no_shap` fixture patches it.
- `compute_and_cache` keeps `"importance"`, so `load_cached_importance` and Streamlit are unchanged. It adds `base_value`, `values` (300×18), `x`, `max_additivity_gap` and the feature order.
- `load_cached_explanations()` refuses a cache whose `model_sha256` differs from the served artifact.
- **A process-wide `threading.Lock` wraps `_shap_values`** (explainer construction and call), because of the global RNG.
- **I re-run `make explain`.** It reads validation and a train background only.

**Evaluate sidecar** (`report.main`):
- also writes the tracked `reports/final_evaluation.json`: `model_sha256`, `evaluated_at`, metrics (plus metrics at 0.50 and ECE), confusion matrix, segment table with the unscanned flag, test-optimal threshold, reliability bins, and the "nobody churns" accuracy;
- written with `allow_nan=False`, NaN converted to `null`; fields are `float | None`. No probabilities.
- Served only when its `model_sha256` matches the artifact.

**After the maintainer runs `make evaluate CONFIRM=yes`:**
- review the regenerated `final_evaluation.md`. The archival note disappears and the format gains Wilson intervals and SeniorCitizen rows; the numbers must be identical.
- move the archival history into MODEL_CARD "Evaluation history";
- run `test_documentation`.

**Config:** `api.rate_limits` gets a `default_factory`. `_build` reads optional sections with `.get`. No business value changes.

### 1c. Endpoints

`/api` aliases of the 4 legacy endpoints share their handlers and appear in the schema, so the SPA gets typed calls. Static paths are registered **before** `/api/customers/{id}`; a test catches the wrong order.

| Endpoint | Returns (every figure server-side) |
|---|---|
| `GET /api/model-info` | Existing fields, plus optional `model_sha256`, `data_sha256`, `provenance{dataset, split, withheld_reason}`, `probability_epsilon`, `risk_bands`, `business_defaults` (`cfg.business`), `limits`, `served_locally`, `test_evaluation` (sidecar block or `null` + reason). |
| `GET /api/overview` | KPIs; bands; histogram; campaign (validation sweep at the defaults: net, flagged/n, P·R, cost, retained, peak and delta, sparkline); segment tabs with note parameters (spread ratio, price peak); lifecycle; median tenure; top 5 importance and top 5 association. |
| `GET /api/customers` | Filters: `q` (≤ 32 chars, `[A-Za-z0-9-]`, `regex=False`), repeated `bands`, `contract`, `internet`, `payment`, `tenure_min ≤ tenure_max`, `flagged`, `partition`, `sort` enum, `dir`, `page`, `page_size ≤ 100`. Returns rows (`outcome` is `churned`, `stayed` or `withheld`), `total` and a selection summary. Observed churn uses train+val rows only. |
| `GET /api/customers/export` | Same filters, or `ids` (≤ 200). Neutralised `text/csv`. Serves Main's "Export scored CSV" and "Export selection", and the Explorer export. |
| `GET /api/customers/examples` | `example_customers()`. |
| `GET /api/customers/{id}` | Profile (18 inputs), probability, band, flag, partition, outcome. |
| `POST /api/explain` | Body is `{customer_ids: [≤ 8]}` or `{record}`. Uses the verified train background (100), seed 42 and 10 permutations, under the lock. Returns all 18 contributions per customer. The LRU cache is keyed on the validated, coerced record. Additivity is asserted (≤ 1e-9, else 500). 503 `explanations_unavailable` if partitions are unverified. |
| `GET /api/segments?rows=&cols=` | Train cross-tab. A cell with n < 20 becomes `{"suppressed": true, "n": n}`. Returns marginals, base rate, the top 7 combinations and the association list (`association_ranking.csv`). |
| `GET /api/explain/global` | The cached matrix, metadata and importance; association ranks; the TotalCharges collinearity r, computed on train features. |
| `GET /api/threshold/sweep` | Inputs: `cost` (0–10,000), `success`, `margin` (0–1), `horizon` (1–120). Returns the validation sweep (101 points), peak, recommendation (`recommend_threshold_from`), indifference band and sensitivity at 10–50 %. |
| `GET /api/performance` | `model_comparison.csv`; selection (meta); calibrated vs uncalibrated at 0.50 (meta); ECE before/after; ε; calibration by band; recorded test block. |
| `POST /api/batch/score` | Multipart, same body limit and `validate_upload`. Returns rows (validated inputs as scored, id, label, prediction), a summary and notes. **Label P/R excludes rows whose `customerID` is in the book's test or unknown partition**, and reports how many were excluded. `/predict/batch` is unchanged. |

**Rules for every endpoint:**
- errors use `{"error","detail","problems"}` with explicit codes: `dataset_unavailable`, `rate_limited` (429 added to the map), `explanations_unavailable`;
- query errors get detail text about parameters;
- no paths or tracebacks in responses.

**Data access:**
- The Book, the reports directory and the SHAP cache are FastAPI dependencies, with paths taken from module-level settings. Tests use `dependency_overrides` built from the conftest synthetic `book`/`dashboard_predictor`, plus a guard that makes the real data loader raise.
- The Book loads lazily (not in the lifespan) and is keyed on `model_sha256`.

### 1d. Security middleware (built early, so the SPA is CSP-clean from day one)

**SecurityHeaders:**
- The headers go into the `SECURITY_HEADERS` that `_error_response` also uses, so 500s from ServerErrorMiddleware carry them too.
- Headers: the strict CSP exactly as in the Master Prompt; `nosniff`; `Referrer-Policy: no-referrer`; a `Permissions-Policy` denying camera, mic and geolocation; `X-Frame-Options: DENY`; COOP/CORP `same-origin`; HSTS only when the scheme is https; `no-store`, except `immutable` for `/assets`.
- `/docs` and `/redoc` get their own CSP allowing exactly what FastAPI's pages load: jsdelivr, inline scripts, the fastapi.tiangolo.com favicon, Google Fonts and `blob:` workers. A Playwright console check verifies it.

**LoopbackOnly:** a peer that is not 127.0.0.0/8 or ::1, or a missing peer, gets 403. One test helper builds `TestClient(…, client=("127.0.0.1", 50000))`, and it is used everywhere, including the `ci.yml` smoke tests. `make api`/`make serve` add `--no-proxy-headers`.

**Same-origin:**
- `hosts.is_same_origin(origin, host)` normalises case, default ports and IPv6.
- The existing allowlist check stays for all methods.
- On unsafe methods, an `Origin` that is present must be same-origin.
- On `/api/*`, `Sec-Fetch-Site` must be `same-origin` or `none`. This blocks other localhost ports, e.g. NexusBI on :8000, and `<img>`-triggered exports.
- This closes I-01/R-07. The params in `test_api.py:548` change to `http://localhost`, with new 403 cases for other ports. `test_hosts.py` is unchanged.
- The Vite dev proxy uses `changeOrigin: false`.

**RateLimit:**
- a token bucket per route group and client peer, size-capped, configured from `api.rate_limits`;
- explain: burst 10, 1 per 2 s; batch: burst 5, 1 per 6 s;
- covers `/api/explain`, `/api/batch/score` and `/api/predict/batch` together with the legacy `/predict/batch`. They share one bucket, so the alias is no bypass;
- runs after the host and origin checks, so refused requests cost no tokens;
- tests not about limits disable it through a module-level override;
- this is a documented behaviour change: 429 is now possible on `/predict/batch`.

### 1e. Backend tests (one per Master-Prompt requirement)

- **Endpoints**: every new endpoint gets happy path, validation, limits, error shape and a no-path check.
- **Leak and suppression scans**:
  - crawl every endpoint × parameter set: no test-row outcome, and aggregates use train+val only. The sidecar's recorded aggregate test block is allowlisted.
  - no rate is returned for n < 20.
- **Model guarantees**:
  - parity: `/api/customers/{id}` = `POST /api/predict` = `predict_one`, within 1e-12;
  - additivity ≤ 1e-9; parallel explanations equal sequential ones (lock);
  - threshold 0.36 and sensitivity 0.88/0.50/0.36/0.23/0.22, from the tracked sweep CSV through the library rule and cross-checked against `model_comparison.md`. No test reads the real dataset.
- **Routing**: route order; SPA routes, assets and traversal; JSON 404/405 preserved; the docs CSP.
- **Security**: headers on 200/4xx/500 (using `raise_server_exceptions=False`), 403 loopback, 403 origin and Sec-Fetch, 429 with `Retry-After`.
- **Data sources**: sidecar written, consistent and stale-refused; SHAP cache matrix and stale detection; batch label P/R exclusion.
- **Other**: CSV neutralisation vectors in `tests/fixtures/csv_neutralisation_cases.json`, shared with Vitest; causal-phrase scan over API strings and OpenAPI descriptions.
- **All 634 existing tests stay green.**

## Phase 2 — front-end shell

**Stack** (`frontend/`):
- React 19, TS 5.9 strict, Vite 8 (`assetsInlineLimit: 0`, no sourcemaps, a separate vendor chunk), TanStack Query v5, `d3-scale`/`d3-shape`;
- `@fontsource/geist` latin+latin-ext at 400/500/600/700, and `geist-mono` at 400/500/600. These are static weights matching the canvas's Google Fonts render, where 550 resolves to 600 and 650 to 700.
- No UI kit and no router library.

**Supply chain:**
- `.npmrc` sets `ignore-scripts`, `save-exact` and `engine-strict`;
- `.nvmrc` and `engines` set Node 22;
- versions are exact, each ≥ 7 days old, and `package-lock.json` is committed.

**Router:** ~100 lines, tested:
- `<Link>` intercepts plain left clicks only;
- `popstate`;
- focus goes to the `<h1>` and scroll to the top on navigation;
- pages are lazy-loaded.

**URL state:** typed search-param hooks with validation and defaults. Discrete changes push history; text replaces it after a 250 ms debounce. Only filters and IDs go into URLs. The live form stays in memory. `localStorage` holds only the sidebar collapse state.

**Styles:**
- `styles/tokens.css` holds every design colour, radius and type token;
- `base.css` ports the HELMET rules (focus ring, `.sr`, transitions, reduced motion, `overflow-x`/`overscroll-behavior-x`);
- each component has a CSS Module with the design's exact px values;
- dynamic values go through CSS custom properties set via CSSOM (CSP-safe);
- an ESLint rule bans `dangerouslySetInnerHTML`.

**API layer:**
- `schema.d.ts` is generated by `openapi-typescript` from `scripts/export_openapi.py`;
- `client.ts` passes an `AbortSignal`, does not retry 4xx and retries 5xx once, and never shows server error text; 422 `problems` are shown as text;
- one hook per endpoint.

**Shell**, as `build_shell.py` defines it:
- collapsible sidebar (232 ↔ 66 px) with the Serving card;
- header with breadcrumb and an actions slot;
- drawer at ≤ 860 px with focus trap, `Esc` and focus return.

**UI kit:**
- Button/Ghost, Chip, Segmented (`aria-pressed`), Tabs, Field/Select/Range, Card, StatTile, SimulatedBadge;
- Popover (dialog semantics);
- HTML tooltip that flips at the right edge and follows the pointer and keyboard focus;
- Skeleton, EmptyState (reason + reset), ErrorState (retry);
- Pagination, SortHeader (`aria-sort`).

**Charts:** `ChartFrame` provides `role="img"`, an `aria-label` and a visually hidden data table.

**`lib/`:**
- `format.ts`: percent, money with U+2212, signed pp, clamp-aware probability (4 decimals above 0.999 and below 0.001), integers;
- `csv.ts`: a client CSV builder with neutralisation, using the shared vectors;
- `labels.ts`: the design's per-context labels.

**Rule:** the client formats and positions. It never computes a probability, band, flag, SHAP value or threshold, and never aggregates over customers. Display arithmetic on API figures, such as deltas, is allowed.

**Tests:** Vitest, Testing Library, and MSW handlers typed from `schema.d.ts`.

## Phase 3 — pages, in this order, each with unit and e2e tests

1. **Overview (`/`)**
   - Header: search, provenance popover, export.
   - Operating-point scale.
   - 5 KPI tiles; two link to the queue.
   - Histogram: customers or charges toggle, band shading, observed-churn line and diagonal, tooltip, click filters the queue.
   - Band cards.
   - Campaign card (SIMULATED, assumptions shown).
   - Segment tabs, lifecycle bars with tooltip, importance toggle.
   - Retention queue: band chips, partition filter, `id ∋` chip, reset, sortable columns, pages of 8, selection with bulk bar.
   - Top-2 attributions per page, loaded lazily through one `/api/explain` call with skeleton chips.
   - Outcome pills; Explain link to `/customer?id=`.
2. **Explorer**
   - Presets with live counts.
   - Filter bar.
   - Selection summary (`aria-live`) with a mini histogram.
   - Sortable table with page size 15/25/50 and skeleton, error and empty states.
   - Detail panel: prev/next/close, attributes, "Explain this customer".
   - Export.
3. **Single customer**
   - Picker: examples plus "New customer".
   - Grouped profile with the top-4 contributors highlighted.
   - Live form: coherent add-on and phone rules; debounced `/api/predict` (250 ms) and `/api/explain` (500 ms idle, stale requests aborted); `=×`; reset to the README example.
   - Prediction card with scale and threshold.
   - Waterfall: Top 5, Top 8 or All 18.
   - Plain sentences.
   - Real API response panel.
   - CSV download.
4. **Batch**
   - Steps.
   - Drop zone with a client-side size pre-check (convenience only).
   - Demo file: `tests/fixtures/demo_customers.csv`, imported raw at build time.
   - Required-columns toggle; validation panel (422 problems and notes).
   - Summary, including label P/R with the exclusion note.
   - Rows: all or flagged, sort, pages of 12.
   - Scored CSV; template download.
5. **Simulator**
   - 4 sliders; the threshold follows the recommendation or is set by hand.
   - Net-benefit chart: band, peak, recommendation, hover, click-to-set.
   - Precision/recall chart.
   - Result tiles.
   - Pins (≤ 4, kept in the URL).
   - "Why not the peak" panel.
   - Sensitivity pills.
   - 150 ms debounce; the previous result stays visible while the next loads.
6. **Performance**
   - Recorded test tiles, or the "not recorded" state.
   - 1-SE chart and candidate table.
   - Confusion matrix: recorded test / validation explore with slider.
   - PR curve with F1 contours, hover, and click to load the cut.
   - Calibration by band and the uncalibrated-vs-shipped table.
   - Test segment table (NOT SCANNED rows).
7. **Explainability**
   - Method tiles.
   - Selectable swarm rows.
   - Dependence plot, numeric or categorical, with hover.
   - Slope chart with the rank chips.
8. **Patterns**
   - Rows × cols selects, swap, measure, suggested views.
   - Focus line.
   - Grid: hover, pin, suppressed hatch, marginals.
   - Ranked combinations.
   - Association list.

### Deliberate deviations from the artboards (correctness over copy)

- **Copy that claims in-browser scoring becomes truthful API wording:**
  - the Batch intro, steps and notes;
  - the Explorer SHAP note, which says "12 orderings in your browser";
  - the Customer live notes;
  - the host in the API panel.
- **The Main queue is the real paginated flagged list** ("Showing 1–8 of 2,306 flagged"). The prototype loaded only the top 8 per band.
- **"Add to campaign list" is omitted.** It is a dead button, and no such feature exists. "Export selection" works.
- **Numbers typed into the copy become API values:** "17×", "r = 0.9996", "0.88 at 10% to 0.22 at 50%", "Cramér's V 0.419 … fourth", "0.27", "34 churners", ECE 0.144 → 0.027, ε.
- **The Single-customer picker uses a deterministic rule.** The design's four case types are kept, but no hard-coded IDs; the chosen customers may differ from the prototype's.

## Phase 4 — hardening, CI, docs

**Wording:** `scripts/check_wording.py` scans our own bundle chunks (not vendor) and the API strings for causal claims (`causes|caused by|drives|driven by|will prevent|prevents|guarantee(s|d)`). It runs in pytest when `dist` exists and always in CI.

**Frontend quality:**
- ESLint 9 with react-hooks, jsx-a11y and security, plus Prettier and `tsc --noEmit`, with zero warnings;
- `scripts/` is added to the ruff targets.

**Playwright e2e:**
- all 8 pages at 375 and 1440 px: drawer, filters, sort, paging, tooltips, keyboard-only navigation;
- batch with the demo file and with a deliberately broken file;
- axe with 0 serious or critical violations;
- `scrollWidth ≤ clientWidth` at 375, 768, 1024 and 1440;
- base URL `127.0.0.1`.

It runs against `scripts/e2e_server.py`: a synthetic dataset, model, reports and SHAP cache (`--sample 30`) built in a temp directory, run with `CHURNSENSE_CONFIG` and raised rate limits.

**CI**, a new `frontend` job with SHA-pinned actions and Node 22 plus Python 3.12:
- the Python side installs the hashed lock, so the schema is stable;
- `npm ci`, lint, typecheck, Vitest, build, wording check;
- OpenAPI type freshness (`git diff --exit-code`);
- `npm audit --audit-level=high`;
- Playwright with Chromium.

Dependabot gets an `npm` entry (`/frontend`, 7-day cooldown, grouped). The `ci.yml` smoke tests use a loopback client.

**Makefile:** `frontend-install`, `frontend`, `frontend-dev` (proxy to `API_PORT`), `serve` (API + SPA, no reload), `openapi`, `frontend-test`, `e2e`.

**Docs:**
- SECURITY.md: loopback-only, rate limits, CSP and the docs exception, same-origin, I-01 closed;
- README: SPA section, with only measured numbers and none of the phrases `test_documentation` bans;
- MODEL_CARD: evaluation history;
- memory handoff.

## Verification (after every phase; output shown, nothing claimed unrun)

**Backend:**
- `make test`, in the repo and in a data-free copy; `make lint`; `make audit`;
- manually, with real data: `make api API_PORT=8001` (NexusBI holds 8000) and `curl` on every endpoint;
- headers, 403/429 and the docs CSP checked by hand.

**Frontend:** lint, typecheck, test, build and e2e. Bundle size measured and reported.

**1:1 fidelity:**
- serve the bundle's `design/preview` with `python3 -m http.server`, next to `make serve`;
- take Playwright screenshots of each page at 1440 and 375;
- compare side by side, with a pixel-diff helper in the scratchpad.

Same data and same model, so the numbers must match. Every remaining difference is listed.

**Behaviour checks:**
- Overview recommends 0.36.
- Simulator sensitivity: 0.88/0.50/0.36/0.23/0.22.
- Customer API panel = `POST /api/predict`.
- Playwright response capture shows no test outcome in any network response.

**Reviews:** the `code-reviewer` agent after every phase, and the `security-review` skill before the PR.

## Risks and notes

- **The first explanation takes about 2.4 s** (numba JIT); later ones about 0.3 s per row. The queue uses one batched call per page, behind skeletons.
- **The new CI job is not a required check** until the maintainer adds it to the ruleset.
- **`make evaluate CONFIRM=yes` is one more test read.** It is recorded in MODEL_CARD, and the numbers must reproduce exactly (the artifact sha is unchanged).
- **429 is now possible on the legacy `/predict/batch` path.** This is intentional and documented.

## Decisions recorded during Phase 1 review (2026-10-22)

An independent review of the Phase 1 backend raised three findings against the
rules above. Each was fixed or decided explicitly; none is left implicit.

- **Small groups, everywhere.** A group of fewer than 20 customers never gets
  an outcome rate or churn count, wherever it appears: cross-tab cells and
  margins, band and histogram observations, an explorer selection's summary,
  and the recorded test segments. `insights._observed` is the single place
  observed churn is counted. Two clarifications:
  - **Reliability, not disclosure control.** This is a threshold on what a
    page presents. Training and validation customers' own outcomes are listed
    one by one, and a suppressed cell can be recovered from its margins.
  - **The threshold sweep is exempt.** Its precision and recall are
    operating-point metrics of the whole validation partition, always shown
    beside their denominator (the flagged count).
- **The recorded test evaluation is shown as recorded.** It is the one
  documented exception to "no test figure", and it covers aggregates only:
  - the published report `make evaluate CONFIRM=yes` writes, with the same
    figures `reports/final_evaluation.md` prints, including counts and the
    confusion matrix;
  - served only for the artifact it describes, never recomputed;
  - per-row test outcomes are never sent.
  
  The leak tests run with the record present and allowlist exactly these
  blocks.
- **Batch label checks never reach held-out customers.** An uploaded row is
  held out when either test holds:
  - its customer ID matches a test or unverified customer, ignoring case and
    surrounding space;
  - its scored inputs equal one such customer's.
  
  A held-out row is left out of precision and recall, and its label is not
  echoed back. Without the dataset no row can be told apart, so no label is
  compared or shown. A deliberately perturbed copy of a test customer cannot
  be recognised; the page states that the figure is on the file's own labels
  and is not a performance estimate.
- **Explanations stay bounded.** At most 3 explanation requests are in
  flight; the next gets 503 `explanations_busy` with `Retry-After`. Each
  explanation is checked against the probability `Predictor` served, not
  against the explainer's own model call.
- **Broken files degrade.** A malformed final-evaluation record, a damaged
  SHAP cache or a report with a blank number reads as "not recorded", is
  logged, and never fails a page. A missing dataset is retried at most every
  30 s, so polling does not reload or log it on every request.

## Decisions recorded during Phase 2 (2026-10-24)

- **Fonts: the latin subset only.** fontsource's per-subset files
  (`latin-400.css`, `latin-ext-400.css`) declare no `unicode-range`, so two
  subsets of one weight would make the second replace the first for every
  character. The interface copy is English and the data is ASCII, so latin
  covers it; a rare symbol outside it (∋, ≥) falls back to the system font.
- **Focus after navigation goes to `<main>`, not the `<h1>`.** Pages load
  lazily, so the heading does not exist yet when the address changes; `main`
  always does, and a screen reader announces the landmark and reads on.
- **The open drawer is modal for real.** `main` is `inert` while it is open
  (not only `aria-modal`), and closing it commits synchronously
  (`flushSync`) so focus can return to the menu button. jsdom does not
  implement `inert`, so this is checked in Chromium (Playwright e2e, phase 4).
- **UI kit scope.** `components/ui` holds only primitives two or more pages
  use. A piece only one page draws (drop zone, stepper, waterfall, heat cell,
  confusion cell, CI whisker) lives with that page.
- **Accessibility fixes over the artboards.** `aria-sort` on every sortable
  header, table roles on every grid table, hover and disabled states for
  solid buttons, `aria-current="step"` on the batch stepper.
- **Toolchain.** `tsconfig.json` is a solution file referencing
  `tsconfig.app.json` and `tsconfig.node.json`, so `vite.config.ts` is
  type-checked and linted with Node's types. ESLint uses `defineConfig` and
  `no-confusing-void-expression` with `ignoreArrowShorthand`
  (`onClick={() => setOpen(true)}`). MSW 3 imports handlers from `msw/http`
  and fails a test on any request nobody answers (`onUnhandledFrame`).

### Follow-ups found in this phase

- **Phase 4 e2e:** in Chromium, open the drawer at 375 px, press Escape, and
  check that `main` is no longer inert and that focus is on the menu button.
- **Proposed hardening (not yet approved):** refuse requests that carry
  `Forwarded`, `X-Forwarded-For` or `X-Real-IP`. A reverse proxy on the same
  host makes every peer look like loopback, and such headers are the only
  sign of one the service can see.

### Phase 2 review (code-reviewer, 2026-10-24)

0 critical, 0 high, 4 medium, 11 low and 6 nits. Every one was fixed with a
test that failed first, and each fix was mutation-checked.

- **The query is rebuilt from the address at the moment of each change**, not
  from the last render. Two changes made in one handler both land.
- **"Reload the page" instead of "Try again" after a page fails.** `lazy`
  keeps a failed download, and after a rebuild only a fresh load knows the
  new file names. The way back is a plain link to the same address.
- **An unanswered request fails its test.** MSW 3's `'error'` strategy only
  logs, so the harness records every unanswered request and asserts there
  are none after each test.
- **No band chosen means no request.** The API reads a missing `bands` as
  "any band", so the listing hooks stay idle for an empty choice and the page
  says so.
- **Addresses.**
  - They compare canonically: query keys sorted, fragment included.
  - A list parameter is written in its declared order, and the default in
    any order is the default.
  - `navigate` and `Link` throw on another origin.
  - A customer ID outside the API's pattern (`..`, `.`, empty) never reaches
    a path.
- **Retries.** A server or network failure is retried once; `Retry-After`
  is capped at 10 s.
- **Truthful states.**
  - The Serving card says "Unreachable" when the health check fails, and "—"
    until the model's description arrives.
  - The skeleton announces its words.
  - A sign is taken from the rounded figure, so there is no "−0.0", and an
    attribution that rounds to zero is coloured as neither.
- **Accessibility.**
  - A skip link.
  - The provenance panel takes focus when it opens.
  - Tab ids are built from position.
  - Choosing the page already shown closes the drawer.
- **Lint closes its bypasses.**
  - `window.localStorage`, `sessionStorage`, `insertAdjacentHTML` and
    `document.write` are restricted.
  - `dangerouslySetInnerHTML` is caught inside an object as well.
  - Only `lib/preferences.ts` and the tests may touch storage.
- **Test fixtures are typed by the schema.** `answer(method, path, body)`
  does not compile for a body or path the API does not have.

### API additions for the overview (2026-10-24)

- **`GET /api/customers?ids=…`** lists exactly those customers (at most 200;
  with any filter it is a 422, as for the export). The overview's selection
  bar reads its "$ / mo at risk" from `summary.at_risk`, so the browser still
  sums nothing. The ids-or-filters rule is one dependency
  (`customer_selection`), shared by the listing and the export.
- **`OverviewResponse.importance_basis`**: `{partition, customers}` of the
  cached SHAP sample, so "Mean |SHAP| on 300 validation customers" is a
  figure from the API. It is `null` exactly when `importance` is.

## Phase 3.1 — Executive overview (2026-10-24)

The page reproduces `Main.dc.html` with the API's figures. With the real
data and model, the figures compared by eye came out as the artboard shows
them: the KPIs, the band cards, the campaign card, and the first queue row's
attributions (Tenure +31.7, Monthly −20.9).

**Copy that depended on the data now depends on it explicitly** (`overview/copy.ts`, unit-tested):
- Every number in a sentence comes from the API: the 17× spread, the price
  peak, the median tenures, "300 validation customers", "first … fourth",
  "$32 higher", the assumptions and the Low band's figures.
- A claim is made only when the figures support it:
  - "Not monotonic" only when the price peak lies inside the range.
  - "Within one offer's cost of the peak" only when the gap is at most the
    offer's cost.
  - The importance note names whichever feature ranks first on its own,
    and its rank in the model.
- Parts that cannot be checked are dropped, for example "then falls where
  long-tenure, multi-service customers dominate" and "it overlaps with
  tenure".
- The histogram's "tracks the diagonal" claim becomes an instruction for
  reading the chart.

**Deviations from the artboard:**
- **The queue's default bands are every band that holds flagged customers**
  (`flagged > 0`), read from the data rather than fixed to
  Critical/High/Moderate. A new threshold then never hides flagged
  customers from the default view. The address writes nothing until a band
  is chosen, and an empty choice is kept as `bands=`.
- **Highlight in "What moves the score".** The amber row is the
  strongest association (`association[0]`), not a hard-coded "Contract
  type".
- **Bar scales come from the data.** Segment and lifecycle bars run to the
  largest rate rounded up to the next tenth; rankings run to their largest
  value; the histogram's axis is a d3 `nice()` maximum. They replace the
  artboard's fixed 0.5, 0.6, 16 pp, 0.45, 2,000 and $100k.
- **The export is a plain download link** to `/api/customers/export`. There
  are no "Preparing…" or "Exported N rows" states, which belonged to an
  in-browser build of the file.
- **"Served on" is the address the browser used** (`location.host`). "Local
  only" holds because the server answers loopback peers only.
- **States the artboard does not draw:**
  - withheld outcomes (KPI "Withheld", the lead sentence, the campaign and
    segment cards);
  - suppressed cells ("fewer than 20 customers");
  - the Low-only empty state, with API figures;
  - attributions unavailable (429/503), shown as one status line;
  - provenance not verified, with its reason.
- **Layout.**
  - The threshold label sits under the x-axis ticks, so it never runs into
    the 0.50 tick.
  - The outcome column is 140 px, room for "Withheld · test".

**Accessibility beyond the artboard:**
- The histogram is a slider over its 20 bins: arrow keys, Home and End move;
  Enter filters the queue to the bin's band. Its figures are also a hidden
  table.
- The lifecycle bars show their tooltip on focus.
- The segment tabs move with the arrow keys.
- The provenance panel takes focus and closes on Escape.
- A chart's data table is hidden inside a `div.sr`: a `<table>` ignores a
  1 px width, and at 375 px it pushed the page 93 px wide.

### Overview review (code-reviewer, 2026-10-24)

There were no critical or high findings. Every medium and low finding was
fixed test-first, and the 13 mutations of the fixes were all caught.

- **A key figure opens the queue it counts.** Its link clears the partition
  and the search, and focus moves to the queue's heading. A polite status
  says how many customers are in scope, and only once the new figures are in.
- **No stale figure stands in for a new one.** A selection's total reads "…"
  while the new total loads, and the queue is `aria-busy` while the last
  view's rows stand in.
- **Claims follow the data.**
  - "Risk is front-loaded" only when the first months churn above the
    average; otherwise the card is "Churn by tenure".
  - "Tops out below the threshold" only when the band's top is below it.
  - The High-or-Critical link promises the whole count only when the
    threshold flags every customer in those bands.
  - "Within one offer's cost of the peak" only for the threshold the
    server's rule chose (`recommended_threshold`).
  - The calibration hint is left out while outcomes are withheld.
  - When nobody is flagged at all, the queue says so instead of asking for a
    band.
- **Accessible names carry what is visible.**
  - A band card's name is its figures, and its action is its description.
  - The search chip's name keeps its visible words (label in name).
  - Chart tooltips are for the eye only: the slider's value text and the
    bars' names say the same, so nothing is announced twice.
- **The lifecycle bars are exact.** The rate label sits above each bar,
  outside the flow, so a tall bar is no longer squeezed. Checked in
  Chromium: every bar measured `rate / top × column height`.
- **Smaller fixes.**
  - At the selection limit, the page checkbox can still clear.
  - A tap names its bin by pointer, not focus.
  - The slider keeps a keyboard user's place when the mouse leaves.
  - Explain links build their query with `URLSearchParams`.
  - A band choice equal to the default is forgotten, so "Reset filters"
    shows only off the default view.
- **Resetting keeps the focus (2026-10-28).** Each "Reset filters" (the
  bar's and both empty states') and the search chip go away with what they
  clear, which left the focus on the page itself. The focus now moves to the
  queue's heading, where a key figure's link also sends it. Tested from the
  keyboard; the pager's last button, shared with the explorer, keeps its
  focus at the last page.

## Phase 3.2 — Customer explorer (2026-10-27)

The page reproduces `Explorer.dc.html` with the API's figures. With the real
data and model, the five saved views count 7,043 / 2,276 / 1,481 / 1,595 /
1,695 customers, the same as `GET /api/customers` asked directly.

**State lives in the address** (`explorer/explorer.ts`, unit-tested):
- The keys are `q`, `bands`, `contract`, `internet`, `payment`, `tenure_min`
  and `tenure_max`, `flagged`, `part`, `sort` and `dir`, `page`, `size` and
  `id` (the open customer). A value that does not parse reads as the default.
- The Overview's "flagged" link (`/explorer?flagged=true`) opens the same
  view.
- A choice (chip, select, flag, sort, page, preset, row) adds a history
  entry. Typed text (the ID, a tenure bound) settles for 250 ms and edits the
  entry in place. Every filter change goes back to the first page.
- Tenure bounds use the API's range, 0–120 months (`param.optionalInt`; 0 is
  a bound like any other). A box shows the bound it means: "06" is 6, and 999
  is 120.
- No band chosen (`bands=`), or a minimum tenure above the maximum, asks for
  nothing. The API would read the first as every band and refuses the
  second. `idleReason` and the query layer's `choosesSomething` are pinned
  equal by a test, and `customersQuery` uses `skipToken`, so fetching ahead
  refuses such a query too.

**Every figure is the API's.**
- The selection's tiles, its status line and the export's count come from
  one request per filter (`useSelection`: one row and the summary of every
  match). The saved views' counts and the lede share that cache, so a new
  page, order or page size keeps the figures and announces nothing new.
- The mini histogram is split at the served bands' edges, and its threshold
  line is the served one. The client assigns no customer to a band.
- Whether outcomes are withheld is the book's own answer, falling back to
  the model's provenance. Until either answers, the lede says nothing about
  outcomes.
- Previous and next walk the whole filtered, sorted list and wrap at both
  ends. Another page is fetched first, and the address then changes once
  (page and customer together, one history entry).

**Deviations from the artboard:**
- **The presets drop "(not scanned)".** That is a Performance notion. A view
  is pressed only while every filter is exactly what it sets.
- **The attributions are the server's** (`/api/explain`, ordered by size,
  strongest seven drawn). The note gives the server's own figures
  (background customers and feature orderings), not "12 orderings in your
  browser".
- **The export is a download link** with the filters and the order shown. No
  "Exported N rows" state.
- **A new page size keeps the reader's place:** the open customer if it is
  shown, otherwise the first row of the page asked for.
- **States the artboard does not draw:**
  - nothing chosen;
  - a range that holds nobody;
  - the band that tops out below the threshold, or starts at it, with the
    served figures;
  - a partition filter while provenance is not verified;
  - a customer not in the book;
  - a customer off the page shown;
  - a step that could not load;
  - attributions unavailable;
  - figures that could not be loaded ("—", never an endless "…").

### Explorer review (code-reviewer and qa-expert, 2026-10-27)

There were no critical or high findings. One medium, nine low findings and the
nits were fixed test-first. qa-expert's 38 surviving mutants and 10 missing
tests were covered.

A second code-reviewer pass over the fixes found one medium (a step landing
on a search typed while it loaded) and four low findings. All were fixed
test-first.

The mutation check was applied while transforming, so no source was edited.
It covered the 35 earlier survivors and the new code, 66 mutants in all,
and every one was caught. Three equivalent mutants pointed at redundant code,
which was removed: a draft reset, a step failure's customer key and a guard
in the panel.

Checks run:
- 369 front-end tests;
- lint, type check, format and build;
- `npm audit`;
- the backend's 829 tests, which include the wording scan of the bundle.

Checked in Chromium against the real API:
- focus stays on Next across pages and on "Next page" at the last page;
- `?page=2` survives;
- typing then a preset;
- two tenure boxes typed in turn;
- typing during a slow step;
- 0 console messages;
- no horizontal scroll at 375, 768, 1024 or 1440 px.

- **Focus never drops.** Previous and next, and the pager's ends, are
  `aria-disabled` and do nothing instead of becoming `disabled`. A focused
  button that became disabled sent focus to the body. While a step loads,
  the panel is `aria-busy`. (The pager is shared with the Overview.)
- **Typed text is written before any other change**
  (`app/useSettledDraft.ts` and the router's `holdDraft`). It lands in the
  entry it was typed in, and the change applies on top of it. Typing and
  then applying a preset within 250 ms leaves the preset pressed, and Back
  returns to the search. Two boxes typed in turn both land. Back or Forward
  drop unsettled text. The reviewer had proposed dropping the draft on any
  outside change, which would have lost a tenure bound typed just before the
  other box settled.
  - A draft settling on its timer writes only itself. Another box still
    being typed in is left to settle in its own time, so "1" on the way to
    "12" never reaches the address.
- **A step the reader has moved on from never lands.** Every change of
  address moves a counter on (`onNavigate`), including leaving the page and
  coming back to the same address. The panel then stops being busy.
  - A step writes any typed text first, when it is pressed and again before
    it lands. If that text changed the list, the step belonged to the old
    list and is dropped.
  - The stale request is not cancelled: cancelling a query another observer
    may mount on would revert it to idle. Its page just fills the cache.
- **A failed step stays put.** The panel says "The next customer could not
  be loaded." (a polite status), and the buttons stay usable to try again.
  The list is no longer paged away from the open customer.
- **While the last view's rows stand in,** the pager and the page-size
  anchor use the page in the address, not the placeholder's.
- **Figures that could not be loaded** read "—", and the summary says so
  with a way to ask again. The lede drops the count it could not get.
- **Resetting keeps the focus.** The bar's Reset is `aria-disabled` with
  nothing to reset, and does nothing then (not even move to page 1). The
  empty state's "Reset filters" goes away with the filters, so the focus
  moves to the list.
- **Smaller fixes.**
  - An invalid tenure range is flagged even while no band is chosen.
  - The two bounds are a group named by their caption.
  - Bands compare as sets.
  - The band hints word the equal case ("stops short of 0.36, the
    threshold"). They never claim it of the top band, which holds its top.
  - The copy test's causal pattern mirrors `tests/test_wording.py`.
  - The idle summary stays quiet, since the list already says why.
- **Kept on purpose:**
  - presets use `aria-pressed` (as the artboard does);
  - `?page=99` stays in the address (the API clamps it, and the pager shows
    3 / 3);
  - a failed explanation is asked for again when its customer is revisited
    (the hook is shared with the Overview queue);
  - the panel is not modal, so it has no Escape;
  - while the last view's rows stand in, the note describes those rows and
    the pager the page asked for, until the new rows arrive;
  - within 250 ms of typing, a click on the old list (a row, the pager, the
    page size) applies on top of the typed search, as it would a moment
    later; the export link is not a change of address and leaves typed
    text out;
  - the export is a link or, with nothing to export, a disabled button.
    The total goes unknown only while a new filter's figures load. A reader
    on the link reaches that only by Back or Forward, which usually find the
    figures cached.

### API addition for the single customer page (2026-10-28)

- **`ModelInfoResponse.numeric_bounds`**: `{tenure, MonthlyCharges,
  TotalCharges}`, each `{min, max}`, read from `schema.NUMERIC_BOUNDS`, the
  bounds the request model enforces. The page's form refuses an amount
  outside them before asking, from the served values rather than a copy in
  the browser, which could drift and either refuse a customer the API takes
  or send one it refuses. It is optional, like `limits`, so older callers see
  no change.
