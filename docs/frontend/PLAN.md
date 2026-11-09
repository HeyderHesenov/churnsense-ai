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
- `shap_global.json` holds importance only. (Since 1b it also holds the matrix, `x`,
  the base value and the additivity gap; see "API additions for the explainability page".)
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
  middleware (outer→inner): SecurityHeaders → LoopbackOnly → TrustedHosts+SameOrigin → BodySizeLimit → RateLimit
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
- New `shap_matrix()` returns values plus the base value. (Built as `_shap_values` and
  `explanation_cache`; the cache is validated on read since 3.7.) `global_importance` is derived from it, and the `no_shap` fixture patches it.
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
| `GET /api/segments?rows=&cols=` | Train cross-tab. A cell with n < 20 becomes `{"suppressed": true, "n": n}`. Returns marginals, base rate, the top 7 combinations and the association list (`association_ranking.csv`). Since 3.8: bound-safe suppression (a hidden cell carries `reason` `too_few` or `complementary`), each reported rate's Wilson `rate_low`/`rate_high`, `book_customers` and `min_customers` (see "API additions for the patterns page"). |
| `GET /api/explain/global` | The cached matrix, metadata and importance; association ranks; the TotalCharges collinearity r, computed on train features. Since 3.7: `dependence` summaries and `dependence_min_customers`; a damaged cache or one that does not add up is a 503 (see "API additions for the explainability page"); `/api/explain` names its `model_sha256`. |
| `GET /api/threshold/sweep` | Inputs: `cost` (0–10,000), `success`, `margin` (0–1), `horizon` (1–120). Returns the validation sweep (101 points), peak, recommendation (`recommend_threshold_from`), indifference band and sensitivity at 10–50 %. |
| `GET /api/performance` | `model_comparison.csv`; selection (meta); calibrated vs uncalibrated at 0.50 (meta); ECE before/after; ε; calibration by band; recorded test block. Since 3.6: the served model measured on validation (`validation_evaluation`), true negatives per cut, `leader_name`, `model_sha256` (see "API additions for the performance page"). |
| `POST /api/batch/score` | Multipart, same body limit and `validate_upload` (on the served model's columns). Returns rows (validated inputs as scored, id, label, prediction), a summary, notes and `csv`, the rows as a spreadsheet-safe file without a label. **Label P/R excludes rows whose `customerID` is in the book's test or unknown partition**, reports how many were excluded, gives no rate under 20 labelled rows, and says why a rate is missing. `/predict/batch` shares the reader: a file-level 422 now names its reason in `problems` there too (see "API additions for the batch page"). |

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
   - Detail panel: prev/next/close, attributes, "Explain this customer" (inline,
     in the panel), and since Phase 4 a link to the customer's own page.
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
   - Demo file: `tests/fixtures/demo_customers.csv`, imported raw into its own chunk.
   - Required-columns toggle; validation panel (422 problems and notes).
   - Summary, including label P/R with the exclusion note.
   - Rows: all or flagged, sort, pages of 12.
   - Scored CSV; template download.
5. **Simulator**
   - 4 sliders; the threshold follows the recommendation or is set by hand.
   - Net-benefit chart: band, peak, recommendation, hover, click-to-set.
   - Precision/recall chart.
   - Result tiles.
   - Pins (≤ 4). As built they are kept in memory, not in the URL (Phase 4 note).
   - "Why not the peak" panel.
   - Sensitivity pills.
   - 150 ms debounce; the previous result stays visible while the next loads.
6. **Performance** (built 2026-11-01; see "Phase 3.6" for what changed and why)
   - Provenance strip, then a scorecard table: validation, plus the recorded
     test column only when a record exists ("not recorded" otherwise).
   - 1-SE chart and candidate table.
   - Errors at a cut: validation explore with slider, recorded test tab.
   - PR curve with F1 contours, hover, and click to load the cut.
   - Validation reliability diagram and the uncalibrated-vs-shipped table.
   - Segment table with Wilson intervals (NOT SCANNED rows), validation or
     recorded test.
7. **Explainability** (built in 3.7)
   - Method tiles.
   - Selectable swarm rows.
   - Dependence plot, numeric or categorical, with a group slider
     (instead of hover; see "Phase 3.7").
   - Slope chart with the rank chips.
   - One customer placed on the figures (`?id=`).
8. **Patterns** (built in 3.8; see "Phase 3.8" for what changed and why)
   - Rows × cols selects, swap, measure, suggested views.
   - Focus line, with the pinned group's link into the explorer.
   - Grid as a table: hover, pin, suppressed hatch, marginals.
   - Ranked combinations with 95% intervals against the overall rate.
   - Association list.

### Deliberate deviations from the artboards (correctness over copy)

- **Copy that claims in-browser scoring becomes truthful API wording:**
  - the Batch intro, steps and notes;
  - the Explorer SHAP note, which says "12 orderings in your browser";
  - the Customer live notes;
  - the host in the API panel.
- **The Main queue is the real paginated flagged list** ("Showing 1–8 of 2,306 flagged"). The prototype loaded only the top 8 per band.
- **"Add to campaign list" is omitted.** It is a dead button, and no such feature exists. "Export selection" works.
- **Numbers typed into the copy become API values:** "17×", "r = 0.9996", "0.88 at 10% to 0.22 at 50%", "Cramér's V 0.419 … fourth", "0.27", "34 churners", validation ECE 0.144 → 0.027, ε.
- **The Single-customer picker uses a deterministic rule.** The design's four case types are kept, but no hard-coded IDs; the chosen customers may differ from the prototype's.

## Phase 4 — hardening, CI, docs

**Wording:** `tests/test_wording.py` (as built; planned as `scripts/check_wording.py`) scans our own bundle chunks (not vendor) and the API strings for causal claims (`causes|caused by|drives|driven by|will prevent|prevents|guarantee(s|d)`). It runs in pytest when `dist` exists and always in CI.

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

**CI** (as built in Phase 4: two jobs, `frontend` with Node alone and `e2e` with Node plus Python 3.12; see "Phase 4"), with SHA-pinned actions:
- the Python side installs the hashed lock, so the schema is stable;
- `npm ci`, lint, typecheck, Vitest, build, wording check;
- OpenAPI type freshness (`git diff --exit-code`);
- `npm audit --audit-level=high`;
- Playwright with Chromium.

Dependabot gets an `npm` entry (`/frontend`, 7-day cooldown, grouped). The `ci.yml` smoke tests use a loopback client.

**Makefile:** `frontend-install`, `frontend`, `frontend-dev` (proxy to `API_PORT`), `serve` (API + SPA, no reload), `openapi`, `frontend-test`, `e2e`. As built in Phase 4: `frontend-install`, `frontend`, `frontend-test` and `e2e`; `frontend-dev`, `serve` and `openapi` are deferred (`npm run dev`, `make api` and `npm run openapi` cover them).

**Docs:**
- SECURITY.md: loopback-only, rate limits, CSP and the docs exception, same-origin, I-01 closed (as built in Phase 4: SECURITY.md's scope names the CSP and the per-client limits; the README's Security list holds loopback, the Host and Origin checks and, new, the CSP with the docs exception; I-01 is an ID of the untracked local audit, so no tracked document cites it);
- README: SPA section, with only measured numbers and none of the phrases `test_documentation` bans (as built: "Web front end");
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
  - **Reliability first, and since 3.8 no subtraction either.** The threshold
    is on what a page presents. Breakdowns (`/api/segments`, the overview's
    tabs and lifecycle) also hide every further count that would let a small
    group's churn, alone or summed with other hidden groups, be worked out
    from the published counts and `0 ≤ churned ≤ n` (`churnsense.suppression`;
    see "API additions for the patterns page"). This is not disclosure control
    for the whole app: training and validation customers' own outcomes are
    still listed one by one in the explorer, by design.
  - **The threshold sweep is exempt.** Its precision and recall are
    operating-point metrics of the whole validation partition, always shown
    beside their denominator (the flagged count).
  - **The batch label check follows the rule** (2026-10-30): its precision and
    recall are null for fewer than 20 labelled rows outside the held-out test
    partition, as the recorded segments' are. The gate counts labelled rows,
    not each rate's denominator, like the segments; an empty denominator gets
    its own null and reason.
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

## Phase 3.3 — Single customer (2026-10-28)

The page reproduces `Customer.dc.html` with the API's figures. With the real
data and model, the README example reads 0.8029793735676088 (Critical,
flagged at 0.36), the number `POST /api/predict` returns when asked directly,
and its waterfall matches the artboard's (Tenure +30.8, Monthly charges
−16.1, ten other inputs +15.5). An example customer's detail and
`POST /api/predict` on its inputs agree to the last digit.

**Who is shown lives in the address** (`customer/customer.ts`, unit-tested):
- `id` is a customer in the book, as the Overview's "Explain" link writes it;
  `live=true` is the new customer; with neither, the README example. An ID
  outside the API's pattern reads as none and is never put in a path.
- What is typed for a new customer stays in memory (data minimisation): it
  survives looking at another customer and coming back, never a reload.
- The waterfall's Top 5 / Top 8 / All choice is a view setting, in memory, as
  the Overview's toggles are.

**Every figure is the API's.**
- A customer in the book is drawn from its detail (an example's comes with
  the examples, so it is not asked for again); the README example and the new
  customer from `POST /api/predict`. Each kind has its own requests, so one
  customer's figures never stand in for another's while the next loads.
- The attributions are the server's, by ID for a customer in the book and by
  record otherwise. A typed record is scored once typing settles (250 ms) and
  explained only once the API has scored that very record (another 250 ms), so
  a record the API refuses never spends a request of the explain budget.
- The waterfall's running sum, the "N other inputs combined" row and the bars'
  places are arithmetic on one customer's own attributions; its last row is
  the explanation's own probability. "All 18" counts the attributions.
- The scale's bands and dashed line are the served bands and threshold.
- The API panel is the response of `POST /api/predict` for the inputs shown,
  field by field; for a customer in the book it shows that the public endpoint
  gives the number the page draws.
- The CSV is built in the browser from those figures (`lib/csv.ts`, defused),
  with the customer's ID first for one in the book, and only once the figures
  are for the inputs shown.

**Deviations from the artboard:**
- **No model in the browser.** The artboard scored and explained the new
  customer with an exported copy of the model and 16 orderings; the page asks
  the API. Its note says so ("Scored by the API as you edit…").
- **The picker is links, not tabs.** Each card is an address, so a choice is a
  history entry, Back returns, a card opens in a new tab, and the focus stays
  on the card followed. The selected card is `aria-current="page"`. A customer
  a link opened is the first card, and stays while the page is open.
- **The form refuses what the API would.** Besides the artboard's rules (no
  internet means no add-on, no phone means no multiple lines, no tenure means
  nothing billed), nothing billed with a tenure and an amount outside the
  API's bounds (`numeric_bounds`) are flagged beside their box and not sent.
  An amount is text checked as a number (digits; one decimal point), so a box
  is never silently reset. Before the bounds are in, the API is left to
  refuse, and its own 422 problems are shown as text.
- **Narrow screens.** Picker cards are at least 150 px (two a row at 375 px);
  the profile's facts and the waterfall lay out by their card's width
  (container queries): one fact a line, and the bar under its label.
- **States the artboard does not draw:** loading; a customer not in the book;
  a customer or the examples that could not be loaded; inputs that cannot be
  scored ("—"); attributions refused for the rate limit (asked again once,
  after the server's wait), unavailable, or never computable without a
  verified background (no retry); figures still for the inputs typed a moment
  ago (dimmed, `aria-busy`); a server that can explain no one (said once,
  then nothing more is asked for).

### Single customer review (code-reviewer and qa-expert, 2026-10-28)

The first code-reviewer pass found one high, three medium and seven low
findings, and qa-expert two defects and a list of untested paths. A second
code-reviewer pass over the fixes found no critical, high or medium findings,
two low and four nits, and a third, over those fixes, two low and three nits.
Everything was fixed test-first, except what is kept on purpose below.

- **Only the record typed is explained** (high). The attributions are asked
  for once the reader has paused on a record that is still the one typed, and
  the API has scored that very record (a subscribed query, not a cache read).
  Before, a record replaced while typing could be explained and drawn under
  the new one.
- **A retry keeps the reader's place** (medium). A failed query that is asked
  again goes back to loading at once, so its error state and button unmount.
  The focus moves to the prediction card, the waterfall's heading or `#main`.
- **One status line speaks** (medium): once per settled figure, empty while
  the figures are stale or the inputs cannot be scored; "not in the book"
  goes there too.
- **Nothing loads forever** (medium, and qa-expert's D2). Every request ends
  in figures, a failure, "not in the book" or "—". The README example's card
  says "not loaded" when its prediction fails or the examples do, as a book
  customer's does.
- **A linked customer joins the picker once its detail is in** (qa-expert's
  D1): not while the examples load, and never one the book does not hold.
- **The amounts are checked against the served bounds** (low): an amount
  outside them is `aria-invalid`, its message gives the bounds, and nothing is
  sent (see the API addition above). The record is read from the form alone
  and the bounds are checked apart, so bounds that arrive after the figures
  leave them as they are, rather than dimming them and announcing them again.
- **Smaller fixes.**
  - Once the server says it can explain no one, the page asks no more and
    says so on every view, instead of a request and an announcement per edit.
  - "All" is always a waterfall choice, so it stays chosen while the next
    customer's attributions load.
  - "The browser did not save the file" is about the figures whose file was
    refused: it goes once another customer is shown or the form is edited,
    and does not come back. A second refusal in a row is announced again (the
    status is emptied, then speaks 100 ms later; one timer at a time).
  - The explanation hook for a typed record retries a rate limit once, after
    the server's `Retry-After`, and a transient 5xx once; never "unavailable".

The mutation check was applied while transforming, so no source was edited.
The last run covered the page, its hook and every fix: 84 mutants, all
caught, and an identity control that survives as it should. Two equivalent
mutants pointed at redundant code, which was removed: a check for a second
decimal point (`Number` already refuses one) and a flag for inputs changed
since the request (a changed record already leaves the query on its
placeholder). One survivor was a missing test (the
API panel's "No response for these inputs." for a new customer), now added.

Checks run:
- 462 front-end tests;
- lint, type check, format and build (the page's chunk is 9.6 kB gzipped);
- `npm audit`;
- the backend's 830 tests, which include the wording scan of the bundle.

Checked in Chromium against the real API:
- the API panel and `curl POST /api/predict` agree on the README example to
  the last digit (0.8029793735676088), and so do an example's and a linked
  customer's detail with `/predict` on their inputs;
- a typed tenure of 40 gives 0.28808586047145707, the same as curl, and "4"
  is never sent; 500 is never sent and reads "Tenure is 0 to 120 months.";
- bounds that arrive 1.5 s late leave the figures as they are (nothing is
  said again, nothing turns busy);
- a retry keeps the focus on the prediction card, the waterfall's heading or
  `#main`;
- a real rate limit (`Retry-After: 2`) is waited out and asked again once;
- a test-partition customer shows "withheld", and an ID outside the pattern
  sends nothing;
- no horizontal scroll at 375, 768, 1024 or 1440 px on the README example,
  a book customer or the live form;
- 0 console errors or warnings in use (a customer not in the book logs the
  browser's own 404 line).

**Kept on purpose:**
- `useExplanations` (shared with the Overview queue and the Explorer) keeps
  its own retry policy here; it is a follow-up for all three pages;
- the API panel shows the response's `caveat` too, field by field;
- the CSV puts the ID first for a customer in the book;
- Download is `aria-disabled`, never `disabled`, so Back or Forward never
  drop the focus from it;
- the Overview's `queue-title` id stays a literal.

### Follow-ups found in this phase

- The Overview's and the Explorer's retry buttons drop the focus the way the
  customer page's did; `useExplanations` retries an "unavailable" answer and
  never a rate limit.
- `probabilityPct`/`probability` would print 100.00% / 1.0000 for p ≥ 0.99995
  if an artifact were ever served with ε < 5e-5 (today's ε is 0.000118).
- A jsdom "navigation to another Document" line on stderr comes from an
  existing, non-customer test.

### Retries keep the reader's place on every page (2026-10-28)

The first follow-up above, fixed test-first:
- **A retry names where the reader continues.** `ErrorState` takes `focusTo`
  with `onRetry`, and the type requires it, so a page cannot forget it. A
  query asked again goes back to loading at once, which takes the error and
  its button away. The focus moves to the page (`#main`) when the Overview
  failed, to the queue's heading, to the Explorer's list, to its selection
  summary, and to the open customer's heading. The customer page's own
  handlers now use the same mechanism.
- **The Explorer's "Explain this customer"** turns into a skeleton when
  pressed, the first time too. The focus moves to "What moved this score".
- **These places show no focus ring**, as `main`, the list and the detail
  heading already did: a place the page moves the reader to is not a control.
- **The ids are constants** (`MAIN_ID`, `QUEUE_TITLE_ID`, `CUSTOMERS_ID`,
  `DETAIL_TITLE_ID` and the pages' own), so a rename cannot silently drop the
  focus to the body. The Overview's `queue-title` is no longer a literal.
- **`useExplanations` retries as the record's hook does.** It asks again once
  after a rate limit (after the server's `Retry-After`) or a busy or failed
  server, and never after "unavailable". Before, it retried "unavailable"
  once and a rate limit never. Paging on cancels a pending retry, so the
  queue's chips spend at most one more request of the explain budget.

Checks: 467 front-end tests; lint, type check, format and build; 17 mutants
of the fix, all caught (and the identity control). code-reviewer found no
critical or high finding, one medium (two tests now waited out a real retry
close to their timeout: they have room now, and the Overview's counts its
requests) and three low; all were fixed. In Chromium against the real API,
each retry left the focus where described above.

**Kept on purpose:** "Explain this customer" moves the focus even in the
unreachable case where a cached response lacks the customer. Landing on
`main` reads the page's text to a screen reader, but while the Overview has
failed there is no heading left to land on.

### API additions for the batch page (2026-10-30)

- **Every refusal names its reason.** `_schema_handler` sends
  `problems = exc.problems or [str(exc)]`. A refusal of the whole file (empty,
  semicolons, too wide, a row wider than the header, too many rows, a header
  with no rows, unreadable, an over-long field) used to send `problems: []`,
  so a page that shows `problems` said "refused" and nothing else.
  `/predict/batch` shares the handler, so its 422s change the same way.
- **`ModelInfoResponse.upload_contract`**, optional like `limits`:
  `categories` (each served categorical column's values, in the served
  order), `integer_columns`, `id_column` and `rules`.
  - The rules are `upload_rules(feature_columns)`, written in
    `models/predict.py` beside the reader they describe.
  - Each rule has a test file that is refused or accepted as its sentence says
    (`test_every_upload_rule_holds_as_written`). A guard test fails if a rule
    has no case.
  - A value rule is given only under the condition `schema._inconsistencies`
    checks it on.
  - `api/schemas.py` takes the rules as an argument and so never imports the
    model stack. The middleware imports the schemas, and a subprocess test pins
    that no scikit-learn is loaded with them.
- **The served model's columns decide what a file must hold.**
  `validate_upload(..., columns=)` validates the served `feature_columns`, and
  both batch endpoints and the Streamlit batch section pass them. Before, the
  config's columns were required. After a config drift, the page's own
  template, and Streamlit's, could be refused (`missing columns: gender`)
  while the contract said it was fine.
- **`BatchScoreResponse.csv`**: the scored rows as a spreadsheet-safe file
  (`csv_bytes`). It holds `row`, `customerID` if the file has one,
  `churn_probability`, `risk_band`, `flagged`, then the inputs as scored.
  - **It never carries a label.** The leak tests assert its exact header for
    uploads that hold every test customer with their `Churn`.
  - **It repeats `rows`** (about 2–3 MB at 10,000 rows), so one request, one
    scoring and one rate-limit token give both the table and the file.
- **`LabelCheck`**:
  - precision and recall are null for fewer than `insights.MIN_CELL` (20)
    labelled rows outside the held-out test partition;
  - `reason` says why a rate is missing: "A rate needs at least 20 labelled
    rows.", or which denominator is empty.

## Phase 3.4 — Batch scoring (2026-10-30)

The page reproduces `Batch.dc.html`, but the API checks and scores the file.

**With the real data and model:**
- the demo file gives 120 rows, P 0.571 · R 0.457 on its own labels;
- 10,000 rows are accepted in about 1.3 s;
- the downloaded CSV of a file with formula-shaped IDs has the same sha256 as
  the API's `csv`, its IDs defused.

**Every figure is the API's.** The page neither validates a value nor scores
a row; the artboard scored the file in the browser.
- **Stages** (`batch/batch.ts`, a reducer): idle, reading, staged, pending,
  then one of the outcomes below.
  - accepted;
  - refused (422);
  - too large (413, or over the served limit and never sent);
  - rate limited (429);
  - unavailable (503);
  - failed (5xx, 408, no answer);
  - denied (any other 4xx);
  - unreadable;
  - several files dropped.
- **Every choice of a file has an id.** A read or an answer for an earlier
  choice is dropped, and a new choice, Remove or leaving the page aborts the
  request still out.
- **The file is read once into a copy of its bytes**, so a retry sends exactly
  what was chosen. A file larger than the served limit is "Not sent". A file
  of exactly the limit is sent, as the API takes it.
- **One action button stays in place**, whatever it says ("Score file", "Try
  again", "Choose another file", "Remove file"), so pressing it never drops the
  focus.
  - It is `aria-disabled` until the file can be sent. `score()` refuses a
    second press both by the live request and by the stage, and each guard
    has its own test.
  - **After a 429** the button waits out `Retry-After`. Then the title, which
    is the status line, changes to "Ready to send … again", so a screen reader
    hears that the wait is over.
  - **A denied request offers only "Remove file"** on that button: any file
    would be refused alike.
- **The label tile** shows the API's figures and its `reason`. The page holds
  no copy of the 20-row rule.
- **Rows:**
  - riskiest first, ties in file order; sortable by row, customer,
    probability and monthly charges;
  - All or Flagged; pages of 12, 25 or 50;
  - the first row shown stays on the page when the page size changes;
  - the pager reads "Showing 1–12 of 120", the Explorer's words, not row
    numbers.
- **Row numbers are 0-based** (the user asked for the logical choice):
  - the table's Row, the CSV's `row` and the API's JSON `row` are one
    position, so a person or a script can join them, and the artboard says
    "0-based";
  - refusals come from the library, which Streamlit also uses, and count data
    rows from 1;
  - the note under the rows says so ("a refusal's data row 1 is row 0 here");
  - a refusal and results never show together, since a file is scored whole
    or not at all.
- **Downloads:**
  - "Scored CSV" saves the API's `csv`;
  - the template holds only the header, the served ID column and the served
    columns, and is offered once the contract is in;
  - a refused save is said through `lib/useDownload`, now shared with the
    single customer page. It empties the status, speaks 100 ms later, and is
    forgotten when the choice changes.
- **The demo** is `tests/fixtures/demo_customers.csv`, imported with `?raw`
  into its own chunk (2.90 kB gzip).
  - Vite admits that import only from an allowed folder.
  - `server.fs.allow` is the front end's folder plus `tests/fixtures`, both
    found from the config file (`import.meta.dirname`).
  - Before, `searchForWorkspaceRoot(process.cwd())` allowed the repository
    root when Vite was started there. `/@fs/` then served `data/raw` and the
    model artifact: 200 before, 403 after, from both start folders.

**Deviations from the artboard:**
- the server checks and scores (the third step reads "Score");
- the demo note gives no row count, which is unknown before the chunk loads;
- the label tile's figure is 18px mono;
- the results header reads name · size · time, since the row count is in its
  tile;
- the drop zone's keyboard focus is the global outline, drawn with `:has()`,
  with a `:focus-within` fallback for browsers without it.

**States the artboard does not draw:**
- reading;
- too large (not sent, or refused);
- several files;
- unreadable;
- a demo that could not be loaded;
- rate limited, then ready again;
- unavailable;
- failed;
- denied;
- labels not compared (no dataset);
- no rate (fewer than 20 labelled rows, or an empty denominator);
- nothing flagged, with "Show all rows".

### Batch review (code-reviewer and qa-expert, 2026-10-30)

**The first pass.**
- code-reviewer found no critical or high finding: two medium, five low and
  some nits.
- **M1: the upload rules did not match the reader.**
  - A repeated contract column is refused, yet a rule said "any other column
    is ignored".
  - A row wider than the header is refused, and no rule said so.
  - "Every value must be present" contradicted the blank TotalCharges at
    tenure 0.
- **M2: the 429 panel kept saying "Try again in N s"** after the wait.
- qa-expert listed overlapping guards, untested paging and size boundaries,
  and harness flaws: a request counter that read files asynchronously, and a
  useless sleep.

All of it was fixed test-first.

**The second code-reviewer pass**, over the fixes, found no critical or high
finding, one medium and five low. All were fixed test-first:
- **Medium: the contract and the reader used different column lists.** Fixed
  by validating the served columns (above).
- **The blank TotalCharges rule.** It now follows `clean_frame`: the file's
  tenure decides, scored or not.
- **The 429 test.** It keeps 10 s of fake time in hand, more than the test's
  own timeout.
- **`forget()` mid-wait** is tested.
- **"Denied" no longer suggests another file** would work, and 408 is
  retried.
- **The `:has()` focus ring** has its fallback.

**Found while fixing:** the Churn rule said "never mixed", but `clean_frame`
encodes Yes/No/0/1 before `validate_frame` checks them, so mixed labels are
accepted. The rule now says "each value Yes, No, 0 or 1"; the check itself is
a follow-up.

**Checks:**
- backend 876 passed, with `make lint`;
- front end 574 tests (run twice, no `act` warning), with lint, type check,
  format and build (Batch chunk 6.88 kB gzip), and `npm audit` 0;
- mutants: 47 front-end and 25 backend, all caught, plus an identity control
  each;
- in Chromium against the real API:
  - the demo;
  - 10 labelled rows show the API's reason;
  - a semicolon file and broken values give their 422 reasons;
  - 6 MB is "Not sent";
  - a real 429 turns to "Ready to send demo_customers.csv again", and Enter
    scores it with the focus kept;
  - the drop zone's focus ring;
  - no horizontal scroll at 375/768/1024/1440;
  - the console holds only the deliberate 422/429 lines;
  - after the second review, a file shaped like the template (`customerID`
    and the 18 served columns) is scored, and the console stays empty.

**Kept on purpose:**
- **The add-on rule names the add-ons as a group.** In a model scoring only
  some, only those are checked.
- **The rules do not say that gender's values go unchecked.** The rule on
  repeated columns names it, and the notes say it is ignored.
- **A line of only commas** is a row of blanks, refused by the blank rule,
  not a blank line.
- **`useDownload` takes `about: unknown`.** Lint refuses a single-use type
  parameter, and only identity is compared.
- **`score()` has no "is this still the current choice" guard.** A choice and
  a press cannot land in one task.
- **`LabelCheck`'s description says 20**, the OpenAPI text, while the code
  reads `MIN_CELL`.

### Follow-ups found in this phase

- **The mixed-label check never runs.** `validate_frame`'s "mixes Yes/No
  labels with 0/1" is unreachable through `clean_frame`, which encodes the
  labels first, so mixed labels are accepted everywhere, training included.
  - Either remove the check or move it before the encoding.
  - It touches the training path, so the decision is the user's.
- **A blank TotalCharges can be read as 0 from an unchecked tenure.**
  `clean_frame` does so from the file's tenure even when tenure is not scored.
  That happens only in an ablation without tenure, and the rule says so.

### API addition for the simulator (2026-10-31)

- **`ModelInfoResponse.assumption_bounds`**: `{retention_offer_cost,
  offer_success_rate, expected_horizon_months, gross_margin}`, each `{min,
  max, integer}`, from one constant, `api/schemas.ASSUMPTION_BOUNDS`.
  - The sweep's `Query(ge, le)` reads the same constant, so the page refuses
    what `/api/threshold/sweep` would refuse rather than a copy that could
    drift. Optional, like `numeric_bounds`, so older callers see no change.
  - **Tests:**
    - the served bounds equal the sweep's OpenAPI parameters (minimum,
      maximum, integer type);
    - each served end answers 200 and a step beyond it answers 422 (a whole
      number of months also refuses a half);
    - the configured defaults lie inside the bounds (`Business` caps neither
      the cost nor the horizon);
    - the configured defaults are as precise as the page shows them (whole
      percents, cents for USD), since it sends only what it shows.
  - **`BusinessAssumptions` keeps `Business`'s own invariants** (rates in
    [0, 1]), not the sweep's input bounds. It is the echo of a `Business`:
    narrower bounds must refuse a request, never break `/model-info`.
- Nothing else changed: the simulation itself (`insights.economics`) is
  untouched.

## Phase 3.5 — Retention simulator (2026-10-31)

The page answers "where should the cut-off sit?" with the API's own sweep of
the validation partition (`GET /api/threshold/sweep`). With the real data and
model at the defaults ($50, 30%, 12 months, 65%), it shows the recommendation
0.36 at +$26,547 (448 flagged of 1,409, 260 of 374 churners reached, precision
0.580, recall 0.695), the peak 0.30 at +$26,579 and the sensitivity rows
0.88 / 0.50 / 0.36 / 0.23 / 0.22: each the figure `curl` returns for the same
query.

**Every figure is the API's.** The page computes no business figure: counts,
rates, money, the recommendation, the peak, the band and the sensitivity rows
are read from the response, and a test fixture whose figures cannot be
derived from one another (241 of 512 is not 0.123) proves it.
- **Three kinds of figure, kept apart.** "Your assumptions" (amber, ASSUMED),
  "Measured on validation" (the model's flags at the cut against what those
  customers did) and "Simulated money" (SIMULATED). A static note says the
  dataset holds no campaign outcomes; the API's `disclaimer` sits under the
  figures. No "saved", no causal word: a copy test and a page test scan for
  both families.
- **Undefined rates are not measurements.** Precision is "—" where nothing is
  flagged and recall "—" without churners (the API writes 0.0 there), in the
  results, the tooltip, the tables, the pins, and as a break in the
  precision line.

**The scenario lives in the address** (`simulator/simulator.ts`):
- `cost`, `success`, `horizon` and `margin` hold the text as typed
  (`param.decimal`: absent is the default, empty is "required", any text
  without spaces is kept for the page to judge, up to 64 characters), and `t`
  the cut as `cut()` writes it ("0.40", 0 to 1 only); empty follows the
  recommendation.
- A field is sent only once all four can be: within the served bounds, a
  whole number where the bounds say `integer`, and no more precise than it is
  shown (money to its currency's minor unit, rates in whole percents). Then
  all four always go, so a default left out and one written share a request.
- Nothing typed is turned into another number: "12,50", "1e3" and "$50" are
  refused with a message, never sent as 1250, 13 or 50, and text longer than
  12 characters is refused as too long, never cut down to one that fits
  ("0000000000050" is not 5).
- Typed edits and the rate sliders settle (250 ms) and replace the history
  entry; Reset, Use recommended, a chart click, a rate Apply and a pin Apply
  add one. Reset also takes an edit made a moment before, not yet settled.

**Figures for other assumptions always say so.**
- `statusOf`: `current` (the response's echo equals what was sent),
  `updating` (an earlier response kept while the next loads), `mismatch` (the
  server echoed other assumptions), `unsent` (a field cannot be sent, so
  nothing was asked for), `failed`, `withheld`.
- Every card, the cut's included, names the assumptions its figures are
  for, from the echo ("For $50 · 30% · 12 mo · 65%"), and while they are not
  the ones set it is dimmed and marked "Not the assumptions set."; the
  results open with a status line, and the charts' accessible names carry
  the mark.
- Only current figures can be pinned or applied as a rate; a cut chosen on
  stale figures is kept as chosen, not read as "follow".

**The cut.** An index slider over the response's points is the keyboard
control; the chart is a pointer-only shortcut (hover, click). The cut follows
the recommendation only while the address sets none: one set by hand stays set
by hand, even where it is the recommendation now. Points are found by label,
never by float, since ten grid values are inexact (0.35000000000000003). A
pin's REC tag says its cut was the recommendation, followed or set by hand;
Apply restores the address as it was. A response with no point at its own
recommendation says so instead of drawing a cut.

**Charts** (`SweepCharts.tsx`): the net across every cut, with the strip of
nets within one offer of the peak (`band.floor` to the peak's net: the
qualifying cuts need not be side by side, so it is a strip across the chart,
not a span between two cuts), the zero line, the recommended, peak, selected
and served cuts; and precision and recall by cut. Each has a screen-reader
table of every 0.05 plus the marked cuts.

**Reading the recommendation.** "Why not simply take the peak?" in the
response's figures (both cuts and nets, the offer as the tolerance; a free
offer, a recommendation that is the peak and a campaign that never pays each
have their own words). "If the success rate were…" lists the API's five rows;
a rate outside the served bounds, the rate already set, or any rate while the
figures are stale cannot be applied, and says why (that explanation is never
dimmed with the stale figures).

**Compare scenarios.** Up to four pins, each a response as it came (its echo
and the point at its cut), beside a NOW row while the figures are current.
No deltas are shown: the values sit side by side. Apply sets a pin's
assumptions and cut again for the API to answer afresh; Remove moves the
focus to the next Remove, the previous one, or Pin.

**States.** Model-info pending: a skeleton. Model-info failed: retry.
Model-info without the bounds or defaults: a plain note, no retry (it would
answer the same). `outcomes_withheld`: a plain note, latched for the page's
life, and nothing is asked at all when model-info says the partitions are
unverified. 5xx or no answer: retried once on its own (`retrySweep`), then
"Try again", which keeps the focus on the results heading; a failure,
withheld outcomes or a response with no cut to draw that takes away the
focused control do the same. 429 (the sweep is not rate
limited, so only from something in between): never retried on its own. 422:
the API's problems, or "The API refused these assumptions."

**Money in the served currency.** `lib/format` gained a `currency` argument
(USD output byte-identical), the currency's minor unit (`minorDigits`: 2 for
USD, 0 for JPY, 3 for KWD), `price` (cents only when there are some) and an M
tier for compact money ("$14.1M", switched at the rounded value). A currency
`Intl` cannot read is written as given.

**Deviations from this plan's Phase 3 list (`Simulator` above).**
- Pins live in memory, not the URL: a pin is a response, and a URL could
  only hold the assumptions, which the API would answer afresh anyway.
- 250 ms settle, as every other box in the app, not 150 ms.
- Two typed inputs (cost, horizon) and two sliders (the rates), not four
  sliders: a cost or a horizon is a number someone has, not a position.
- No cost-versus-retained bar and no "vs now" column: both would picture
  figures the API does not send.
- The cut is an index slider in its own card ("Where to cut"), not part of
  the assumptions: it is a decision, not an assumption.

**Kept on purpose.**
- A rate the address gives outside the served bounds parks its slider at the
  nearest end; reaching that end from the keyboard takes two presses (away
  and back), since a range input fires no change for its own value. The
  field says why the value cannot be sent.
- Each field settles on its own timer, so two fields edited within 250 ms
  send one superseded request: valid, never a duplicate.
- The withheld latch is per visit: a new visit asks once more.
- `moneyShort` has no B tier; the validation partition's money peaks around
  $14M.

**Checks:**
- backend 880 passed, with `make lint`;
- front end 756 tests (run twice, no `act` warning), with lint, type check,
  format and build (Simulator chunk 10.51 kB gzip JS, 2.49 kB CSS);
  `npm audit --omit=dev` 0 (the dev toolchain's 14 moderate: follow-up below);
- in Chromium against the real API, through vite and through the built app
  on :8001 under its CSP:
  - every figure equals curl's (the defaults' figures above);
  - "12.5" typed slowly sent one request; 20000 refused on the page, with no
    request;
  - the cut slider's arrow keys, Reset, a pin's Apply restoring it, the
    chart's hover and click;
  - "12,50" refused and every card marked stale;
  - no console message, and no horizontal scroll at 375, 768, 1024 and 1440;
- after the second pass, rechecked there: the cut card's echo and mark; the
  rates' explanation at full opacity (#8b949e on #10151c, about 6:1); a cut
  picked on stale figures reads "Set by hand" once they are current; Reset
  with an edit not yet settled; the description of the rate already set; no
  console message, no horizontal scroll at 375.

### Simulator review (code-reviewer and qa-expert, 2026-10-31)

**code-reviewer**: no critical; one high, one medium, ten low, nine nits.
- **High: the reading cards showed figures for other assumptions unmarked**,
  and called them "your other assumptions". Every card now names its echo
  and is marked while stale (above).
- **Medium: the focus dropped to the body** when a rate applied led to a
  failed sweep (the card holding the button goes away). It now goes to the
  results heading whenever a failure removes the focused control.
- **Low, fixed:** a cut chosen on stale figures read as "follow"; the echo
  model enforced the input bounds (`/model-info` could 500); the currency's
  minor unit was ignored (JPY "¥12.50"); the empty-pins hint predicted what
  the API would say; a retry that could never succeed for model-info
  without bounds; the fixture's band floor differed from the server's
  (`peak − cost`); missing tests (stale cards, mismatch on the page, the last
  pin's Remove, a chart click adding a history entry, another currency).
- **Low, answered with a guard:** the page's precision rule is not served;
  a backend test holds the configured defaults to it.
- **Nits, fixed:** the chart table lists every mark of a cut; a pasted true
  minus reads as a minus; the sensitivity note when the first and last rows
  share a cut; the free-offer wording; the live region is always mounted;
  a disabled rate says why; no pin panel while outcomes are withheld.

**qa-expert**: 92 hand-written mutants on a copy, then 22 proposed tests.
- The weak spots it proved: a fixture consistent enough that a page
  recomputing precision, recall, missed or offers to stayers would pass; a
  recommendation that was also the peak in every test; pins checked only at
  the moment of pinning; rate sliders never moved; the sensitivity card, the
  currency, loading and busy states, history replace-versus-push and
  Forward untested; typing tests that relied on keystrokes under 250 ms.
- **Behaviour it found:** "12,50" was sent as 1250 and "1e3" as 13; a sweep
  with no point at its own recommendation showed a skeleton for ever; a 422
  with no problems showed an empty list; the horizon box offered a decimal
  keypad.
- All fixed test-first; its 22 tests were taken in (lint-clean, folded into
  the existing describes), the fixture's point made non-derivable, and the
  typing helper now pastes (the one test about quick typing types).

**The second code-reviewer pass**, over the fixes, confirmed each first-pass
fix (each with a test that fails without it) and found no critical or high
finding: one medium, six low and five nits. All were fixed test-first, except
where said:
- **Medium: following the recommendation was index equality.** A cut chosen
  on stale figures that is the recommendation once they are current claimed
  to follow: Use recommended was disabled, `t` stayed in the address, and a
  pin of it followed on Apply. Following now means the address sets no cut;
  the REC tag stays a fact about the point.
- **Low:**
  - Reset ignored an edit not yet settled (the address was still at the
    defaults). It now always sets, which writes the edit first and then
    resets; at the defaults with no edit the address does not change.
  - The focus fell to the body when withheld outcomes, or a response with no
    cut to draw, took away the focused control. The recovery now follows
    every such change, not only a failure.
  - The cut card was the one card without the echo and its mark.
  - The rates' explanations were dimmed with the stale figures. They are not.
  - `typedNumber` cut a paste to 12 characters, so "0000000000050" became 5.
    Text over 12 characters is now refused as too long; the box and the
    address keep up to 64.
  - Tests: `/model-info` with configured defaults outside the sweep's bounds
    (200, now tested); the history entries Reset, a pin's Apply and Use
    recommended add; fixed waits replaced by waiting for the address; the
    quick-typing test types back to back (`delay: null`), so a busy machine
    cannot settle between two keys.
- **Nits:** a `t` beyond the grid ("1.50") is refused on read; the rate
  already set says why it cannot be applied; a problem the API lists twice
  no longer shares a key; the bounds models are frozen. Left as it is: the
  bounds note says "outside the served bounds" for any refused rate, and the
  rows are whole percents, so no other refusal can happen.

**Mutants**, each run on its own: a Vite plugin swaps the text as the module
loads (`cache` and `fsModuleCache` off, or Vitest reuses the unmutated
module), and a mutant that no longer matches is reported, never counted as
caught.
- Front end: 68 for the first pass's fixes, then 12 for the second's.
  - Five of the first survived, each an untested place: the live region's
    role, YOURS while the figures are stale, the peak's mark in the chart
    table, the net chart's stale mark, and the band's served floor. Each got
    a test.
  - One of the second survived, the REC tag: the case the medium made
    reachable (a cut set by hand at the recommendation). It is now tested.
  - All 80 are caught; the identity control survived each run.
- Backend: six plus a control, run on `test_api` and `test_api_dashboard`.
  `served-max` (the cost bound 10,000 → 10,001 in the one constant)
  survived, since the sweep and `/model-info` move together. The served
  bounds are now pinned to the contract above; that test and the echo test
  catch it and an echo capped at the bounds.

### Follow-ups found in this phase

- **`npm audit` reports 14 moderate entries in the dev toolchain: one advisory,
  reviewed 2026-11-01 and deferred on purpose.**
  - **The advisory:** GHSA-g3hv-75wg-m249 / CVE-2026-118764, minimatch
    `< 11.0.1`, globparts amplification (CWE-405).
    - Published 2026-10-30. The first patch is 11.0.1, and 10.x has no
      backport.
    - The other 13 entries are packages npm lists `via` minimatch, not
      advisories of their own.
  - **Installed copies:** two, both dev-only.
    - 10.2.6, used by eslint, `@eslint/config-array`,
      `@typescript-eslint/typescript-estree` and `eslint-plugin-jsx-a11y-x`.
    - 5.1.9, pinned exactly by `@redocly/openapi-core` 1.34.21 under
      `openapi-typescript`.
  - **Production is not affected.** `npm audit --omit=dev` is 0, `dist/`
    holds no minimatch code, and no CI job ran the front end then (Phase 4
    added the `frontend` and `e2e` jobs).
  - **The vulnerable code is not reachable.**
    - It runs only at `optimizationLevel >= 2`. The default is 1, and none of
      the four callers sets it.
    - 5.1.9 does not have that code at all.
    - The patterns come only from our own `eslint.config.js`. Anyone who can
      edit that file can already run code through it.
  - **Rejected: `npm audit fix --force`.** Its "fixes" are downgrades (eslint
    4.0.0, typescript-eslint 8.14.0, openapi-typescript 6.7.6) that would
    break the toolchain.
  - **Rejected: an `overrides` to minimatch 11.**
    - 11.0 is a rewrite with a new dependency
      (`@isaacs/nonrecursive-glob-match`).
    - It changes `partial` and `**` matching, and eslint calls
      `match(path, true)`. So the set of linted files could change silently.
    - It would push redocly six majors past its exact pin, all to close a
      path we never reach.
  - **Re-check when** eslint, `@eslint/config-array` or typescript-eslint
    depend on `minimatch ^11.0.1`.
    - Then a normal bump clears 12 of the 14.
    - Run lint, typecheck, vitest and build, and check that lint still covers
      the same files.
    - The redocly pair waits for `openapi-typescript` to move.
    - The CI gate (`npm audit --audit-level=high`, added in Phase 4) does not
      fail on these; the advisory is listed in SECURITY.md.
- ~~There is still no frontend CI job and no Playwright e2e or axe run~~
  Resolved in Phase 4: the range inputs' arrow keys are now also driven in
  Chromium by `e2e/performance.spec.ts` and `e2e/simulator.spec.ts`.
- `explorer.ts:42` hard-codes the API's tenure bounds (seen while planning).

### API additions for the performance page (2026-11-01)

All additive and validation-only; no new endpoint, and nothing reads the test
partition. The Streamlit performance page (`app/sections/performance.py`)
already computed these live on validation; the API now serves the same
figures from the same functions.

- **`PerformanceResponse.validation_evaluation`**
  (`insights.validation_evaluation`, memoised per dashboard):
  - `metrics` = `evaluation.metrics.evaluate(y, p, threshold)` on the book's
    validation rows and the served (calibrated, clamped) probabilities, at
    the served threshold and the served flag rule `p >= threshold`;
  - `churn_rate`, `no_skill_accuracy` (computed on the server);
  - `ece` = `expected_calibration_error` over the same probabilities as the
    bins;
  - `reliability`: `reliability_table(..., with_edges=True)` bins
    `{low, high, customers, mean_predicted, churned, rate}` (right-closed,
    the first holds 0), through `insights.reliability_bins`;
  - `segments`: `segment_table` over `schema.VALIDATION_SEGMENTS` (Contract,
    InternetService, SeniorCitizen, gender), with Wilson bounds,
    within-segment ROC-AUC and `unscanned`.
  - `None` when validation outcomes are withheld or hold one outcome only
    (no ranking metric is defined; never a 500).
- **One small-group rule for segments:** `insights.small_group` nulls every
  key in `SEGMENT_OUTCOMES` below `MIN_CELL` (20): counts, rates, the four
  Wilson bounds, within-segment ROC-AUC, and `unscanned` (with nobody
  flagged, "not scanned" says whether anyone churned). The recorded test
  segments go through the same helper. Reliability bins under 20 lose
  `churned` and `rate`. `_OUTCOME_KEYS` in the leak scan gained the bounds,
  `roc_auc` and `unscanned`; the scan caught the `unscanned` leak in the
  fixture before the fix.
- **`ValidationCurve.points` are `ValidationCut`s:** a `SweepPoint` plus
  `true_negatives` (stayers less false positives), added in the API layer,
  so `/threshold/sweep`, the simulator and `threshold_sweep.csv` are
  unchanged.
- **`Selection.leader_name`** (`DISPLAY_NAMES`, the key itself if unknown),
  so the page never takes an argmax. **`PerformanceResponse.model_sha256`**,
  so the page can tell when the two responses describe different artifacts.
- **`HoldoutEvaluation.churn_rate`** (positives over n of the record);
  `HoldoutSegment` gains optional Wilson bounds and `roc_auc`, read with
  `.get`, so a record written before them still loads.
- **`reliability_table(with_edges=False)`**: the default output is
  unchanged (the test record and Streamlit use it).
  **`VALIDATION_SEGMENTS`** moved from `models/train.py` to `data/schema.py`;
  train and Streamlit import it.
- **Tests** cross-check independent paths, not a function against itself:
  - the book's validation rows reproduce training's `validation_metrics`
    at 0.50;
  - the counts at the cut equal the sweep point labelled with the served
    threshold, `tn = n − positives − fp`;
  - `churn_rate` equals the sweep's threshold-0 precision;
  - the live ECE is within ε of training's;
  - bins add to n; segments add to n and to the flagged count per
    dimension;
  - a group of exactly 20 keeps its figures and 19 loses them, for segments
    and bins;
  - a one-class or withheld book gives `null`, not a 500;
  - the segment rules name only fields both tables have;
  - an unknown leader key and an old-format record still load.

## Phase 3.6 — Model performance (2026-11-01)

The page answers how well the served model ranks churners, which errors a
cut makes, whether its probabilities hold up, where one cut fails a segment,
and what each figure rests on. It renders `/api/performance` and
`/api/model-info` and works out no metric.

**The evidence there is.** `reports/final_evaluation.json` does not exist,
so `test` is null with its reason; the archival `final_evaluation.md` is
linked, never quoted (its numbers would be typed copy). Validation is the
only measured evidence, and the page says it is somewhat optimistic:
model choice and tuning used cross-validation on train, validation set the
cut and decided whether to keep calibration. A recorded test evaluation, when
`make evaluate CONFIRM=yes` writes one, appears as its own column and tab,
labelled a re-used holdout, "a re-checked estimate, not an independent one".

**Layout.** Provenance strip (train · CV, validation, test, artifact and
whether the data and split match) → scorecard → errors at a cut beside
precision against recall → segments → calibration beside model selection →
footer ("This page computes nothing from the test partition…").

**Decisions.**
- **Scorecard, not tiles.** One table, row groups Ranking (PR-AUC primary,
  with its no-skill tick at the churn rate; ROC-AUC), At the cut (recall,
  precision, F1, each with its counts), Probabilities (Brier, ECE), and
  Context (accuracy, quiet, beside "flagging nobody scores …"). A test
  column exists only beside a record, at the record's own cut; validation
  is never shown under a test label.
- **Undefined is not zero.** The API writes 0.0 for an empty denominator;
  the page writes "—" from the counts (precision iff nothing flagged,
  recall iff nobody churned, F1 iff either), in the scorecard, the strip,
  the tables, the tooltip and the chart's accessible name. A genuine 0.0
  (three flagged, none churned) stays 0.000.
- **The cut** is an index slider over the served points (the keyboard
  control; `aria-valuetext` "threshold 0.36", the summary is the live
  region), found by label, kept in `?cut=` (replace; a chart click pushes),
  empty at the served cut, and a cut the curve lacks falls back without
  rewriting the address. Picking on the chart from the test tab goes back
  to validation. `threshold_source` is quoted with "one cut under one set of
  assumptions, not a universal optimum" and a link to the simulator.
- **Precision against recall**, both axes 0–1, every cut that flags someone
  (the sweep stays exempt from the small-group rule, as decided in Phase 1);
  no-skill at the threshold-0 precision as served; F1 contours are geometry.
  No ROC curve: at a 26 % churn rate the PR curve is the informative one, and
  ROC-AUC stays a number.
- **Calibration** is a validation reliability diagram from served bins, with
  the bins too small to draw listed under it, the served ECE, the 0.50
  uncalibrated-vs-served table ("Brier is the comparable figure"), and a
  sentence for each of kept / tried and not kept / not calibrated.
- **Model selection**: CV PR-AUC ± SE for the candidates inside one SE, axis
  from the whiskers and cutoff, leader and selected as served. The cutoff is
  written "= leader − SE" only when the comparison report agrees with the
  artifact's record to the digits shown. Candidates are labelled
  uncalibrated.
- **Model mismatch:** if the two responses name different artifacts, no
  figure is shown until both are asked again ("Reload figures", focus to
  `#main`); a response naming none is not a mismatch.
- **Segments** use a performance-only name map (gender is never added to the
  patterns page's dimensions); "No internet", not "None". The scroll region
  takes focus (axe scrollable-region-focusable): the repository's one
  `eslint-disable`, with its reason.

**Deviations from the artboard** (`Performance.dc.html`):
- Scorecard table instead of test tiles; the artboard's figures are test
  figures this artifact has no record of.
- Validation reliability diagram instead of the band strip: the band rates
  mix train rows, which are in-sample for the calibrator (`bands` is still
  served, unused here).
- Validation-first segments (the artboard's are test).
- PR curve: every cut that flags someone, not "15+ flagged"; y from 0, not
  0.2.
- Dropped unsourced copy: "roughly 10:1", "hundreds of dollars", "two model
  versions", "2.4× the no-skill line", "sampling noise", and the summed "34
  churners" (segments overlap).
- The 1-SE axis comes from the data; the artboard's RF whisker was off.

**Checks.** Backend 899 passed (the wording scan of the built bundle included) and
`make lint`; front end 854 vitest twice with no act warning, lint, typecheck,
prettier, build (Performance chunk 11.01 kB gzip JS), `npm audit --omit=dev`
0. Chromium on the built app (:8001, under the CSP) against the real API:
every figure equals `curl /api/performance` and `/api/model-info` (at 0.36:
260 reached, 188 offers to stayers, 114 missed, 847 left alone, as the sweep;
PR-AUC 0.644, ROC-AUC 0.838, precision 0.580, recall 0.695, F1 0.633, Brier
0.138, ECE 0.027; cutoff 0.6646 = 0.6714 − 0.0068); "Test evaluation not
recorded"; Two year and No internet NOT SCANNED; 0.9–1.0 not drawn (14
customers); the slider's arrows write `?cut=0.38` and keep the focus; a chart
click loads 0.46; `?cut=1.00` reads precision "—" in the strip and the
chart's name; 0 console messages; no element past the right edge at
375/768/1024/1440. `artifacts/` and `reports/` are byte-identical before and
after (sha256 of all 23 files).

### Performance review (code-reviewer and qa-expert, 2026-11-01)

- **code-reviewer:** 0 critical/high, 2 medium, 7 low, nits. All fixed
  test-first except where noted:
  - M1 `unscanned` revealed whether anyone churned in a group under 20
    (nulled; scan key added; the scan found it in the fixture);
  - M2 the chart's accessible name read the API's 0.0 as a precision;
  - L1 focus after "Reload figures"; L2 a chart pick from the test tab;
    L3 wording assumed calibration was kept; L4 `scope="rowgroup"`;
    L5 hidden bins and the y-axis title shown; L6 the PR-AUC meaning;
    L7 the cutoff sum only when the figures agree;
  - nits: "within one standard error", the empty-segment guard, a test
    pinning the segment fields, "is/are", the double announcement, distinct
    "Partition" names.
- **qa-expert** ran 69 frontend and 15 backend mutants against the first
  version: 60 frontend survived, mostly because the fixture's rates equalled
  their counts' quotients at three digits and the record shared the served
  cut. The fixture now has rates that are not their quotients, a record at
  0.40, a stayer count no formula gives, a cut that flags three and reaches
  none, and segment rows that tell the rules apart; the tests were rewritten
  around them (every test-column figure, the test tab, the PR table by
  column, history and address behaviour, model-info loading and failure,
  null artifacts, unavailable and withheld everywhere, null calibration
  blocks, leader cases). Backend gaps (recorded small segments, the bin and
  segment boundary at 20, an unknown leader) got tests.
- **Mutants after the fixes:** front end 49 plus an identity control, on a copy with a
cache-free Vitest config: 47 caught, the control survived, and 2 are
equivalent under the server's contract (precision "—" by null alone: the
server nulls it exactly when nothing is flagged; a null churn rate read as
0 %: the server nulls it only with the churners). The first pass's four other
survivors got tests: the null-artifact case was vacuous (in one
`server.use`, the first handler answers, and the default came first),
model-info still pending once the evaluation is in, a click on the first cut,
and a record whose cut reached nobody. Backend 23 plus a control: all caught,
the control survived (a first run with every mutant "caught" was the copy
missing `churnsense.data`: the control said so).

### Follow-ups and limitations found in this phase

- **Live validation ROC-AUC and PR-AUC differ from training's record by
  about 1e-6** (0.8375378 vs 0.8375365): the probabilities agree (Brier to
  1e-17), but a ULP-level difference breaks one exact tie
  (0.5 / (374 × 1,035) = 1.29e-6). Shown to three digits they are equal.
- **No test evaluation is recorded** for the served artifact; the test
  column, tab and segments are built and tested on fixtures only. Running
  `make evaluate CONFIRM=yes` is the maintainer's decision (one more read of
  a re-used holdout).
- **`model_comparison.csv` is not tied to the artifact's sha**; the page
  guards the cutoff sum, not the whole table.
- Pointer-only chart reading (hover); the keyboard path is the slider and the
  tables. Playwright/axe and a frontend CI job are still Phase 4.

### API additions for the explainability page (2026-11-02)

All additive; the cache file's format is unchanged (`make explain` already
wrote the matrix, `x` and the additivity gap).

- **`GlobalExplanation.dependence`**: one summary per feature, in `features`
  order, a union discriminated by `kind`, computed by
  `shap_explain.dependence(features, values, x)` from the cache's
  **unrounded** values (the served `values` are rounded to 6 dp for the dots;
  the means are rounded only after averaging):
  - `numeric` (`schema.NUMERIC_COLUMNS`): 12 equal-width groups over the
    observed range, right-closed, the first holding the minimum (the
    reliability table's `np.digitize(..., right=True)` idiom over the inner
    edges); empty groups are left out; a constant column is one group
    `low == high`;
  - `categorical`: every level of `schema.ALLOWED_CATEGORIES[f]` in contract
    order, absent ones with `customers 0`, so any customer's category has a
    place;
  - `mean` is null below **`dependence_min_customers` (5)**, also served. It is
    a stability rule for a mean of model outputs, not the `MIN_CELL` outcome
    rule: no outcome is in these groups, and the field is never called a rate.
- **`ExplainResponse.model_sha256`**, so the page can tell a customer
  explained by another artifact than the cached figures (precedent:
  `PerformanceResponse.model_sha256`).
- **A damaged cache reads as "not cached" (503 `explanations_not_cached`),
  never a 500 or a half-drawn page.** `load_cached_explanations` checks:
  features known, unique and text; `values` and `x` the same non-zero length;
  every `x` row exactly the features, categories from the contract, numbers
  finite (JSON integers past a float's range included); every contribution
  finite and within **[−1, 1]** (a permutation SHAP value of a probability is
  a mean of differences of two probabilities; a larger one would overflow a
  group mean); and the description the page states (partition text,
  `n_explained == len(x)`, counts, seed, base value in [0, 1], an importance
  list, a recorded gap). A malformed importance row is refused in the route.
- **Attributions that do not add up are never served:** `read_cache` refuses
  a recorded `max_additivity_gap` that is not a number in
  [0, `ADDITIVITY_TOLERANCE`] (1e-9), so the overview's ranking goes too;
  `ADDITIVITY_TOLERANCE` moved from `dashboard.py` to `shap_explain`.
- **Tests:** dependence groups account for every customer; category and bin
  means are the means of unrounded contributions (a case where rounding first
  gives 0.0 instead of 1e-6, and the API's means equal `dependence()` on the
  raw cache file exactly); bins right-closed, on one grid, the minimum in the
  first; every level listed; 5 customers carry a mean and 4 do not; a constant
  column; no explained customers; 24 kinds of damaged cache; a contribution of
  exactly ±1 is served; the dependence recomputed independently from the
  served values; one customer and the cached figures share a baseline (≤ 1e-9)
  and a background; both responses name the served sha; seven kinds of
  recorded gap refused (1e-6, missing, text, negative, true, false, NaN).

## Phase 3.7 — Explainability (2026-11-02)

The page shows how the shipped model uses each attribute, from `make
explain`'s cached attributions for a validation sample, and lets a reader
place one book customer on those figures from the customer's own
explanation. It renders `/api/explain/global`, `/api/model-info`,
`/api/customers/examples` and `/api/explain`; it averages, sums or ranks
nothing itself.

**What the figures are (verified on the shipped artifact).** shap 0.54.1's
`PermutationExplainer` on `ProbabilityClamp.predict_proba(...)[:, 1]`
(preprocessing, isotonic calibration and the clamp inside; column 1 is churn),
over the 18 raw attributes, on the probability scale (×100 = percentage
points). The base value is the mean served probability over 100 training
background rows (seed 42, 10 orderings). The API's background and the cache's
are the same rows. The cache explains its sample in one call and
`/api/explain` explains each customer alone, so a sampled customer's ring can
sit about 0.3 pp per attribute from their own dot: the copy never calls the
ring one of the dots.

**Layout.** Method strip (partition and seed, sample, background, orderings,
additivity gap) → one customer → swarm beside dependence → two rankings →
footer (masking caveat and the server's caveat).

**Decisions.**
- **State ladder:** loading → not cached (an empty state naming `make
  explain` as the maintainer's step; "Check again" asks once more, focus to
  `#main`) → failed (model-info or the figures; retry, focus `#main`) → the
  two responses name different artifacts (every figure hidden, "Reload
  figures" refetches model-info and invalidates every `['explain']` query,
  focus `#main`) → the page. A model-info without a sha still shows the
  figures, as on Performance.
- **The studied attribute** is `?f=` (replace; the top of the served ranking
  is the clean address); an attribute the cache does not explain falls back.
  Swarm rows and ranking chips pick it.
- **One customer** is `?id=` (a push): example links show ID and served
  probability, never an outcome, `aria-current` on the placed one; the ID
  field is submit-only and drops what no ID holds. A customer is asked for
  only once the figures are in, the model's description agrees with them, the
  training data is verified, and the server has not said it can explain no
  one (a latch for the session, across later addresses). The query has no
  placeholder and the answer is picked by `customer_id`, so another
  customer's explanation is never shown under a new one. 404 is a status
  line; 429/busy is asked again once (`retryExplanation`), then "Ask for the
  attributions again" (focus the card heading).
- **Ringed only when comparable** (`comparability`): same artifact, base value
  within 1e-9, same background size; seed and orderings are deliberately not
  compared (they change how credit is split, not the baseline). Another
  artifact hides the customer's table behind "Reload figures"; another
  baseline or background shows the table without rings, with the reason.
- **The customer's summary** gives the base value, the served probability,
  the three strongest raises and lowers by the sign a reader sees
  (`shownSign`: a contribution that rounds to 0.0 neither raises nor
  lowers), and all 18 contributions; never a summed contribution (rounding)
  and never a reconstructed probability. The waterfall stays on the single
  customer page (linked).
- **Swarm:** one SVG, one path per colour class per row; numeric dots
  coloured by the third of the observed range (three classes and tokens, not
  a gradient), categories grey; dots within one bucket stack alternately and a
  dense stack saturates at the row's edges (the table carries the numbers).
  Row buttons are in the chart frame's overlay, never inside `role="img"`.
  Rings are HTML spans so they stay round however the SVG stretches.
- **Dependence:** dots by shown sign; the server's group means as a line that
  breaks at a group without a mean and between groups that are not
  neighbours, or a bar per category with its n; a slider steps through the
  groups (its value text is what the tooltip says; a group without a mean is
  pointed at from the top, never at a mean of zero). Questions are worded as
  what SHAP measures ("How does the monthly charge's contribution vary with
  its value?"), not conditional effects. TotalCharges carries the served
  collinearity note.
- **Two rankings:** association rank on training outcomes against rank
  inside the model, a line only for attributes ranked both ways; no
  Cramér's V figure (the association ranking mixes measures).
- **Shared pieces:** `lib/labels` `valueText` and `FEATURES` (moved from the
  customer page), `lib/format` `shownSign` (the waterfall uses it too),
  `--value-low/mid/high` tokens, `queries` `retryGlobal` (never
  `explanations_not_cached`), `test/server` `refuse(..., {problems,
  headers})`, and `test/wording.ts` CAUSAL, which every copy test imports and
  which equals `tests/test_wording.py`'s (now also bare "drive", "prevent"
  and "leads to"; bare "cause" stays out because the pages say "nothing here
  identifies a cause").

**Deviations from the artboard** (`Explain.dc.html`):
- The ring is the customer explained on their own, not one of the dots.
- Three colour classes instead of a gradient; rings as HTML.
- The mean line has gaps instead of bridging small or empty groups.
- A group slider instead of per-dot hover; no reconstructed "customer
  probability".
- No outcome on the example picks; no Cramér's V figure.
- Unsourced notes dropped ("front-loaded", "not a discount working",
  "unique contribution is smaller"); conditional questions reworded.

**Checks.** Backend 952 passed (the wording scan of the built bundle included) and
`make lint`; front end 936 vitest twice with no act warning, lint, typecheck,
prettier, build (Explainability chunk 9.86 kB gzip JS, vendor unchanged at
85.61 kB), `npm audit --omit=dev` 0. Chromium on the built app (:8001, under
the CSP) against the real cache (300 validation customers, base 25.76 %, gap
1.9e-15; every contribution inside [−1, 1]): 18 rows in the served order;
Total charges' 12 groups each with a mean, stepped by ArrowRight with the
mark on each, and its collinearity note; a sampled validation customer ringed 19 times. The
review fixes, with responses altered in the browser only (`page.route`): a
group without a mean has no mark; a model-info naming another artifact hides
every figure and asks for no customer, and "Reload figures" brings them back
with the focus on `#main`; "Check again" after a 503 keeps the focus on
`#main`; a customer explained by another artifact gets the card alert, no
table and no rings, and its "Reload figures" restores all 19. No page
scroll at 375/768/1024/1440 (at 375 the swarm scrolls inside its own region,
as designed); 0 console messages in normal use. `artifacts/` and `reports/`
are byte-identical before and after (sha256 of all 23 files).

### Explainability review (code-reviewer and qa-expert, 2026-11-02)

- **code-reviewer:** 0 critical/high, 4 medium, 5 low, 5 nits; all fixed
  test-first:
  - M1 a damaged cache could still give a 500 (an unhashable feature, an
    integer past a float's range, unguarded scalars): kinds are checked before
    the set, `_finite` catches the overflow, the description is checked, a
    malformed importance or an empty matrix is a 503;
  - M2 two chart questions claimed conditional effects SHAP does not measure:
    reworded;
  - M3 the A→B test did not hold A's answer back: two page tests added (they
    passed at once; the mutants below show they guard the placeholder and the
    pick by ID);
  - M4 "Check again" dropped the focus: it goes to `#main`;
  - low: a group without a mean was marked at y(0); a null sha read as
    comparable; a customer from another artifact now gets "Reload figures"
    and no table; the overview refuses a cache past tolerance; the mismatch
    test asserts no customer request;
  - nits: notes without counts, "exactly" dropped, component strings moved to
    `copy.ts`, an exact-edge comment, the gap cases −1e-12, true and NaN.
- **qa-expert** (on the tree after those fixes): front end 53/81 and backend
  27/34 mutants killed. One real defect, **D1**: a contribution of 1e308 was
  accepted, its group mean overflowed to infinity, was served as null, and the
  page said "too few for a mean" for a group of five or more. Fixed test-first
  by the [−1, 1] bound. Two equivalent branches were removed (the `np.clip`,
  since `digitize` over the inner edges never leaves 0..11, and the
  `low == high` branch, since equal edges put every value in the first group),
  and the unreachable-from-the-route empty guard is kept and pinned by a direct
  test, since `dependence()` is public. Its test gaps were closed: the
  unrounded means, the row count, a gap of `false`, model-info failing, the
  latch across addresses, the `retryGlobal` wiring, stacking at row 3, colour
  by shown sign, ring and row placement, a categorical ring, both axes
  widened for the customer, the TotalCharges note, the served floor, the
  slider's max and a shrinking group list, the tooltip anchor, a lone mean
  drawn without a line, runs that start or end at a null group, the third's
  edge, the bucket, a 32-character ID, the strongest three, value text in the
  table, `aria-current`, a junk-only ID, the customer's own base value, and
  the highlighted slope drawn once.
- **Mutants after the fixes** (a copy with a cache-free Vitest config; the
  qa-expert's first list was not kept, so it was rebuilt from the code):
  front end 37 plus an identity control, all caught, the control survived;
  backend 10 plus a control, all caught (including three on the [−1, 1]
  bound, the right-closed bins, the five-customer floor, rounding before
  the mean and rounded values passed to `dependence()`), the control
  survived. The first pass had three survivors, each a weak test: a junk ID
  typed with no customer placed changes nothing either way (the test now
  starts with one placed); the colour test used a category, so the numeric
  branch's colouring was untested (it now runs on both, with rounding traps);
  and the slope-line filter had no test (`importance` in the cache is not
  checked to name every feature, so an attribute can have an association rank
  and no model rank).

### Follow-ups and limitations found in this phase

- Every figure on the page is a model attribution: how the shipped model's
  output moves with an attribute's value for these customers, not what
  changing that value would do to anyone. The server's caveat ("These are
  model attributions, not causal effects.") closes the page, and the CAUSAL
  scan covers the copy, the API text and the built bundle.
- A dense swarm stack saturates at its row's edges; the table carries the
  numbers.
- Equal-width groups put few customers in the tails (Total charges' top groups
  hold 6–9 of the 300); on the shipped cache every group still has a mean,
  but a smaller sample would break the line there.
- The association ranking mixes Cramér's V and |point-biserial r| and has
  near-ties; the page shows ranks, not values.
- The global payload (about 150–200 kB) is cached for the session.
- `FeatureContribution.direction` (Streamlit only) still reads an exact 0 as
  "lowers".
- jsdom cannot measure layout: geometry is tested in `explain.ts` and the
  component tests read the drawn coordinates; Chromium covers the rest.
  Playwright/axe and a frontend CI job are still Phase 4.

### API additions for the patterns page (2026-11-03)

All additive; `/api/segments` keeps its shape and its 14 dimensions.

- **Bound-safe suppression** (`churnsense.suppression`, used by
  `insights.breakdown`, so the overview's tabs and lifecycle follow it too):
  no group of fewer than 20 customers, alone or summed with other hidden
  groups, has a churn count that the published counts and `0 ≤ churned ≤ n`
  fix. Before, a hidden cell could be recovered from its margin (real data:
  tenure 4–6 × $95+, n = 15; < $35 × Streaming TV Yes, n = 4), and the bounds
  gave away Contract × tenure's small cells: their 0–3 column held no
  churner outside Month-to-month.
  - **Model.** A table is a flow on a small graph (cells, margins and the
    partition are edges; totals are nodes). A per-edge pair of linear programs
    finds the counts every completion agrees on; a group's sum is fixed exactly
    when no fundamental cycle of the still-moving hidden edges changes it, so
    "is any small group fixed?" is one small integer program (`scipy.optimize`
    `linprog`/`milp`, already a dependency), never an enumeration of groups.
  - **Repair.** The cheapest cycle of hidden edges through a pinned edge (fewest
    groups, then fewest customers), or, when the bounds pin it, a hideable edge
    on a line through it with a cycle of its own. Margins are never hidden in a
    two-way table: each is its own one-way breakdown's, so responses sharing
    one agree; grids are protected in `DIMENSIONS` order and transposed, so
    A×B and B×A hide the same cells.
  - **What it cannot protect** is set aside first and logged once: a total that
    holds no churner (or only churners) gives each of its groups away, and no
    hiding changes that.
  - **A solver failure** hides every count the table may hide (logged), so a
    numerical problem never takes `/api/overview` down with it.
  - **Real data cost** (91 pairs, 2.5 s, memoised per dashboard): Contract ×
    tenure also hides Month-to-month × 0–3 and × 4–6, One year × 25–48 and Two
    year × 25–48 (Two year's low-tenure cells all have 0 churners, so only a
    cell with churners protects them); tenure × monthly hides 3 more cells and
    monthly × Streaming TV 3 more. The margins still show every level. No
    one-way level is hidden.
  - **Scope: one response at a time.** Two two-way tables sharing a margin
    that its one-way breakdown hid could together narrow it; on the pinned
    dataset no margin is hidden, so responses add nothing to each other.
- **`ReportedCell.rate_low` / `rate_high`**: the rate's 95% Wilson interval
  (`evaluation.metrics.wilson_interval`). Hidden cells have none.
- **`SuppressedCell.reason`**: `too_few` (under 20) or `complementary`
  (enough customers, held back so a smaller group cannot be worked out).
- **`SegmentsResponse.book_customers`** (every customer, for "4,225 of 7,043")
  and **`min_customers`** (20, so the page types no threshold).
- **Tests:** an independent oracle (`tests/disclosure.py`: the table as
  equations, an LP per group) checks hand-built tables, 40 seeded random
  tables (800 more in a one-off run, none failing) and, from the JSON alone,
  every one of the 196 views of the synthetic book (all groups while a view
  has at most 8 small ones, otherwise singles and pairs); A×B equals B×A
  transposed; a two-way response's margins equal its one-way responses and
  the overview's tabs; every customer lands in exactly one cell; a reported
  group's n and churn equal the explorer's training summary for the same
  filters; Wilson bounds; 20 reported and 19 not; a lift against a
  partition with no churner is null; a solver failure; `lift`, `rate_low` and
  `rate_high` joined the leak scan's outcome keys.

## Phase 3.8 — Churn patterns (2026-11-03)

Observed churn of the training partition by one attribute or two crossed.
Validation and test outcomes are not read; no model output appears on the
page, and the lede says so.

- **Population strip**: "Training partition · 4,225 of 7,043 customers ·
  1,121 churned · 26.5% observed churn", from the response.
- **Controls**: rows and columns (a dimension is never offered against
  itself; an address that crosses one with itself reads as one dimension, so
  the 422 is never asked for), swap (which keeps the pin, turned), the
  measure (churn rate, customers, lift; replace, not push), and the four
  suggested views. Views push a history entry; pins and the measure replace.
- **Table**: a real `<table>` with row and column headers carrying their
  margins and an "All" corner with the overall rate. Each cell is a button
  whose name is the focus line's sentence; pointing or focusing describes it,
  pressing pins it (`?pr=&pc=`), Escape lets go (from the ranking too). A
  hidden cell shows "—" and `n<20`, `held back` or `none`, hatched; on the
  customers measure it shows its size like any other.
- **Shades**: rate on a single cyan ramp from 0 to the highest reported rate
  rounded up to a tenth; lift diverging on a log scale centred on 1× (slate
  below, cyan above, at least 1.5× either way so near-equal groups do not look
  far apart; a group where nobody churned sits at the bottom); customers on a
  neutral ramp. New tokens `--observed`, `--observed-below`, `--count`,
  `--hatch`: never a risk or attribution hue.
- **Focus line**: the pointed group, else the pinned one, else the partition;
  the pinned group's link into the explorer, or why there is none.
- **Explorer link**: only for a reported group whose dimensions the explorer
  filters exactly (contract, internet, payment, tenure bucket → `tenure_min`/
  `tenure_max` read off the label), always `part=train`, written by the
  explorer's own `EXPLORER_PARAMS` (`app/params.hrefTo`); a value the explorer
  would not read back gives no link rather than a dropped filter. The
  explorer's summary then repeats the group's n, churn and rate (tested at the
  API).
- **Ranking**: the highest reported rates with their 95% intervals and sizes
  on an axis from 0, a line at the overall rate; the subtitle says they are
  chosen for being highest (so more likely overstated) and that each interval
  covers one group alone.
- **Associations**: `make eda`'s ranking as served, the measure named per
  row; an attribute with a breakdown opens it alone, the others are listed
  only; gender is never named.
- **States**: skeleton; error with a retry that keeps focus on `#main`; 503
  `outcomes_withheld` → its own message and no further request
  (`retryOutcomes`, renamed from `retrySweep`, which the sweep shares); while
  the next view loads the last one stays, labelled as it is, `inert` and
  `aria-busy`, and one live region outside the busy one says "Showing … while
  … loads", "Pinned: …" and "Showing …" again on release. The pointed group
  belongs to one response, without remounting, so focus stays where the reader
  left it (an association button keeps it as its view arrives).
- **Overview**: a hidden level's text now says why (`lib/cells.hiddenText`).

### Deliberate deviations from the artboard

- Observed rates are not painted with the predicted-risk palette, and lift is
  centred on 1× instead of sharing the rate ramp.
- A `<table>` of buttons instead of `role="grid"` without arrow keys.
- Hidden cells say why; the ranking shows intervals and an axis from 0.
- The association list is served (no typed values) and drops gender and the
  artboard's "0.000" note.
- A link into the explorer for exactly filterable groups; none in the
  artboard.
- The population strip replaces the hard-coded "TRAIN PARTITION · 4,225 ·
  CHURN 26.5%" pill.

**Checks (2026-11-03):** backend 1,493 passed and `make lint` (after `npm
run build`, so the wording scan read the bundle); frontend 1,017 vitest twice
with no act warnings, lint, typecheck and Prettier clean, build with a
Patterns chunk of 6.29 kB gzip JS and vendor unchanged at 85.61 kB, `npm audit
--omit=dev` 0. Mutants in a scratchpad copy (`cache: false`, `fsModuleCache:
false`, `PYTHONPATH` at the copy): backend 28/28 caught, frontend 39/39, each
no-op control survived. `artifacts/` and `reports/` unchanged (23 files, sha256
before and after).

**Chromium** (built app on :8001, the real local API): every cell of Contract
× tenure equals `/api/segments` (18 of 18; the hidden ones show no rate in the
cell, its name, the focus line or the ranking); the pinned Month-to-month ×
7–12 opens the explorer at `contract=Month-to-month&tenure_min=7&tenure_max=12
&part=train`, whose summary reads 345 customers, 145 churned, 42.0%, as the
cell does; Back restores the pin; lift shades with `color-mix`; swap keeps
the pin, turned; Escape from a cell lets go and keeps focus; an association
keeps focus as its view arrives; responses held and failed in the browser
(`page.route`): the last view stays, `inert` and `aria-busy`, and the status
says so; a retry lands on `#main`; `outcomes_withheld` asks once; no page
scrolls sideways at 375, 768, 1024 or 1440 (the table scrolls in its box);
no console message but the 503 the test forced. `.playwright-mcp/` deleted.

### Patterns review (code-reviewer and qa-expert, 2026-11-03)

- **code-reviewer** (0 critical, 0 high, 3 medium, 4 low, nits), all
  verified and fixed test-first unless noted:
  - a group its published totals fix by themselves (a total with no churner)
    made the repair hide the whole table and list every fixed subset: such
    counts are now set aside first, logged once, and cost no cell;
  - combining responses can narrow a hidden margin: the guarantee is stated as
    per response, with why the pinned data has no hidden margin;
  - choosing an association remounted the page body and dropped focus: the
    pointed group is now kept per response without a remount;
  - the busy region could swallow the loading announcement, and a release was
    never said: one live region outside it;
  - the integer program's tie-break could prefer more customers; a solver
    failure would have taken the overview down (now: hide the table, log);
    duplicate repair options; the threshold literal; Escape from the ranking;
    a swap now keeps the pin.
  - Not taken: the rate scale's `Math.ceil(r * 10)` overshoot. No churned/n rate
    that is exactly a tenth overshoots (checked for n < 400), so a test of it
    would pass without the code.
- **qa-expert** (69 mutants on an earlier snapshot, 43 survived): a group where
  nobody churned was painted as average on the lift scale (now the bottom of
  it); the hidden-margin path and the grid's orientation had no test; the
  JSON oracle checked only single groups in wide views (now pairs too, and
  column margins count). Every surviving mutant that was not equivalent got a
  test; the four the final pass still let through (minimality of the repair:
  bound options, checking an option frees its group, setting aside what the
  totals fix; the empty-rate floor; the per-response pointer; the
  association's pressed state while loading) now each fail a test.

### Follow-ups and limitations found in this phase

- Suppression holds per response (see the API section); the explorer lists
  training outcomes one by one by design, so the rule is about what the
  breakdowns present, not disclosure control for the whole app.
- The validation and recorded test segment tables (`small_group`) still
  null small rows without complementary suppression.
- The explorer filters 4 of the 14 dimensions; other groups get no link.
- `docs/frontend/PLAN.md` named a real customer in the Phase 3.7 browser
  notes (`a94b220`); the working tree now says "a sampled validation
  customer", and the governance test that caught it runs only with the
  dataset present. The local, unpushed commit still holds the old line.
- Intervals are per group and unadjusted for the many comparisons a reader
  can make; the copy says so.

## Phase 4 — integration, e2e, accessibility, CI (2026-11-04/05)

Pages were not redesigned. This phase ran the built app in a real browser
against the real API, fixed what only shows there, and keeps it checked.

### What was built

- **Unknown page addresses** (`api/spa.py` `not_found_page`, called from
  `main._http_error_handler`): a browser navigation (GET or HEAD whose
  `Accept` holds `text/html`, any case) to a path outside every route's first
  segment gets `index.html` with status 404 and the app's own Not found page.
  The router's own 404 only: one that names its `error` (`frontend_not_built`)
  keeps it; 405 stays JSON; nothing without a build; the path never reads a
  file. The reserved segments are read from the app (`reserved_segments`).
- **Explorer → Single customer**: the open customer's panel links to
  `/customer?id=` ("Open {id} on the Single customer page").
- **Makefile**: `frontend-install`, `frontend`, `frontend-test`, `e2e`.
- **`scripts/e2e_server.py`**: a synthetic book (`E2E-00000`…, one 32-character
  ID), a temporary `CHURNSENSE_CONFIG` whose every path is checked inside the
  folder before anything is written (`within`, then `confine` on the loaded
  configuration), one grid point per family, `n_jobs` 1, raised rate limits,
  no final evaluation (the test partition stays withheld), SIGTERM turned into
  `SystemExit` so the folder is removed. Startup about 11 s.
- **Playwright** (`frontend/playwright.config.ts`, `frontend/e2e/`, Chromium,
  2 workers, dark, UTC, `reducedMotion: 'reduce'`): 13 specs and opt-in review
  screenshots (`E2E_SCREENSHOTS=1`, `test-results/review/`). A global setup
  refuses a server whose customers are not `E2E-`. An automatic guard fails
  any test on a page error; a console error the test did not allow (by text
  *and* the address it is about); a CSP violation; a request to another
  origin; one document sending a request again while it is in flight; or a
  response holding a held-out (test or unverified) customer's outcome, a
  suppressed group's figures, a final-evaluation figure or an IBM-format ID.
  Expected model figures always come from captured responses.
- **CI**: jobs `frontend` (Node only: typecheck, lint, format, Vitest, build,
  `npm audit --omit=dev` at any level, dev at `high`) and `e2e` (the hashed
  Python lock, OpenAPI type freshness, build, the bundle wording scan with a
  build required, Chromium, Playwright, the report kept on failure). ruff now
  covers `scripts/`. `actions/setup-node` is pinned to v7.1.0's commit.
  Dependabot watches `/frontend` (npm).

### Defects found in the browser and fixed

1. **Patterns**: the pinned group's link moved about 20 px under the pointer
   at 1280 px, because the focus line changed when the pointer left the pinned
   cell. It now keeps "Pinned ·" while the pinned group is pointed at.
2. **Single customer**: the picker card cut the 32-character ID with an
   ellipsis at 320 px (WCAG 1.4.10). It now wraps (`overflow-wrap: anywhere`).
3. **Accessibility** (axe): visible text is now each link's or button's name
   with the context in `aria-describedby` (StatTile, the Swarm rows, the
   CrossTab cells; WCAG 2.5.3); links in running text are underlined; the
   Patterns count no longer drops below contrast (`.count { color: inherit }`).

### Reviews (code-reviewer and qa-expert, 2026-11-05)

code-reviewer: 0 critical, 0 high. qa-expert: 3 high, 7 medium. Both found
the first two below. All fixed test-first:

- the two "a late explanation never lands" tests asserted before the late
  answer could arrive: they now prove the superseded request was aborted;
- the duplicate-request guard was reset by every `pushState`: requests are now
  counted per document (a main-frame navigation request, then its commit);
- the CSV formula test read a demo file with nothing formula-shaped: a file of
  formula-shaped IDs now has to come back defused, cell for cell; with
  `exports._defused` disabled the old test passed and the new one fails;
- `not_found_page` ignored a coded 404; `e2e_server` wrote its dataset before
  the paths were checked (a `..` base path wrote outside the folder);
- the console allowlist matched text only, so "status of 404" hid any missing
  asset; the CSP was checked by substring (now directive sets equal to a
  written-out policy, for the app and the docs, in pytest and Playwright);
  suppressed cells now must hold nothing but their place, size and reason;
  the `unknown` partition is held out like `test`; the long-ID check measures
  the text itself (it found defect 2); a fixed 600 ms wait became the
  address the settled draft writes; the explorer range line is computed from
  the response (the old matcher had been reading the summary's one-row
  request); `tests/test_wording.py` fails instead of skipping when CI says a
  build exists.
- Controls: each new guard rule failed a planted violation and passed a
  reload. Application mutants (explain without an abort signal, exports
  without defusing) were caught; sources restored and checked by sha.

**Checks (2026-11-05):** `make lint`; pytest 1,541 passed; Vitest 1,020 with no
act warnings, typecheck, lint, Prettier; build; Playwright 70 passed (2
opt-in skipped) in 1.0 min, and `--repeat-each=3` 210 of 210 in 2.5 min;
`artifacts/`, `reports/`, `data/` (27 files) and `audit-reports/` unchanged by
sha256.

### Decisions

- Visual regression is structural (overflow at 320–1440 px, the longest ID,
  a visible focus ring); screenshots are for review only and never committed.
- Batch → Explorer is not offered: the API cannot list a batch's rows.

### Limitations

- Chromium only; WebKit and Firefox are not run.
- The model is cached with `staleTime: Infinity`; only Performance and
  Explainability compare the served model's sha, so a model swapped under an
  open tab shows on the other pages after a reload.
- Performance → Simulator does not carry the chosen cut (`t`).
- The explorer lists training and validation outcomes row by row by design;
  suppression applies to the breakdowns (page level).
- `workers: 2`, because the server allows three explanations at once.
- The new jobs are not required checks until the ruleset lists them;
  `actionlint` was not run, and CI has not run on GitHub (not pushed). CodeQL's
  default setup may need JavaScript added (repository settings, untouched).
- The temporary folder is left behind only if the server is killed with
  SIGKILL. `make e2e` builds first, since the server serves `frontend/dist`.

## Remediation after the final audit (2026-11-06)

A limited pass over the audit's findings, on top of Phase 4. Nothing was committed
in it, `make evaluate` was not run, and `reports/` was not touched.

### What changed

- **Held-out figures** (`evaluation/disclosure.py`, used by the API on every record
  it reads and by the writer): a recorded figure over the listed test customers is
  published only if each side of its two-by-two table is empty, or holds at least
  20 customers with both outcomes. A reader of one dimension holds a table of flagged
  and unflagged by level, tied to the partition's totals and bounded by each published
  churn count, so `_fixed_counts` works out what still follows and siblings are
  withheld until nothing does.
- **Reliability bins are all or nothing.** The calibration error and any count at a
  score are further equations over the bins, so one bin that fails withholds every
  rate, the error and the counts at 0.50. The writer also reads each bin on both sides
  of the threshold and of 0.50 (`score_parts`). A real `make evaluate` may therefore
  publish no test calibration; validation's stays on the Performance page.
- **A partition whose totals fail** publishes no figure: a short report, a record with
  `metrics: null` that the API reads as withheld, and no test figures.
- **Retention queue:** `at_risk` is null for recorded churners, `exclude_churned` is a
  server filter, and `BandSummary.flagged_not_churned` gives each chip the number its
  list shows. Test customers count, since nothing here reads their outcomes.
- **Copy:** Overview's causal lines, Patterns ("display rule, not privacy protection"),
  the histogram's validation-only points, and flagged/stayed wording in place of
  offer/reached on measured counts.
- **Elsewhere:** repeated axis labels (`lib/format.tickLabels`), the router and Reload
  link, `Campaign.peak_gain` and `Selection.leader_gap` in place of front-end sums.
- **Documents:** `docs/MODEL_CARD.md` and `docs/INTERVIEW_PREPARATION.md` follow the same
  rule on their test segment tables.

### Limitations

- Each dimension is protected on its own: two dimensions read together are not solved.
- The scores over the whole partition (Brier, ROC-AUC, PR-AUC, simulated value) stay when
  a bin is withheld, on purpose. Each is one more equation, but over individual customers
  rather than a few bins, so it pins nobody while scores differ. In an invented table with
  nearly all scores tied (1,000 at 0.10, 5 at 0.95), Brier, ROC-AUC and PR-AUC did give the
  small group's count back. Withholding them would remove what a held-out evaluation exists
  to report whenever one bin is small.
- The API cannot read a bin on both sides of a cut, having no scores with outcomes: an
  older record with passing bins and failing parts still serves its calibration error.
- The test precision-recall figure is no longer drawn: its steps are outcomes in score
  order. It was a local file, gitignored, and no document used it.
- When one level holds every flagged customer, its flagged churners are the partition's
  own published total. What stays withheld is its churn count, so its unflagged side
  stays open.
- `reports/final_evaluation.md`, `reports/eda_report.md` and git history keep their older
  figures or text; `make evaluate` adds to the test history. (The artboards were cleaned
  later: see "Release governance", below.)
- Streamlit reads `meta.test_metrics` directly. A new evaluation writes it only when the
  totals may be published, but its segment-free figures are outside the table rule.
- `frontend/dist` was not rebuilt in this pass: run `make frontend`.
- The dependency audit stays red on tornado until its own maintenance PR.

## Disclosure remediation, second pass (2026-11-06)

An independent review found the per-dimension rule of the first pass recoverable: a
union of hidden levels by subtraction (flagged 480/1/1 said "1 churner among 2 named
customers"), two dimensions read together, a reader who replays the rule, and tied
scores through full-precision score metrics. Where this section and an earlier one
disagree about the test partition, this one holds.

**Why not a joint integer program.** Every outcome-dependent decision is itself an
output, so a complementary suppression that is checked jointly still tells a reader
who knows it which case applied. The listing also lets a reader cross any attributes,
and the API holds no scores to check anything with. So what could only be protected
that way is not published at all (`evaluation/disclosure.py`).

| Held-out figure | Decision | Depends on |
|---|---|---|
| Segments, reliability bins, ECE, reliability and PR figures | never | nothing |
| Metrics at 0.5, tuning table, test-optimal threshold | never | nothing |
| Simulated money on the partition | never | nothing |
| n, positives, tn/fp/fn/tp, precision, recall, F1, accuracy | all or none | each of the four cells >= 20 |
| Brier, ROC-AUC, PR-AUC | all or none, beside the counts | score groups hold >= 200 bits |

| Scalar | What its value says | With known scores | Ties | Precision | Combined | Null means |
|---|---|---|---|---|---|---|
| tn/fp/fn/tp | one table with known sides | one count per side | none | whole | nothing past the sides | some cell under 20 |
| Brier | sum over labels weighted by known scores | one equation over score groups | groups become counts | 4 decimals | three figures can fix a few groups, hence the gate | scores tie, public |
| ROC-AUC | rank sum of churners | one equation over ranks | the same | 4 decimals | the same | scores tie, public |
| PR-AUC | a nonlinear function of the order | a further equation | the same | 4 decimals | the same | scores tie, public |

**Decisions.** The floor counts every cell rather than purity, so withholding states a
range and cannot be read as "everyone in that side churned"; it also rules out an AUC
of exactly 0 or 1 (at least 400 pairs each way), so no separate AUC band is kept: a band
would make a null beside released counts mean "extreme AUC". `FinalEvaluation` holds
only the released figures, so no writer can reach a raw one. `released_metrics` is the
one way to read them back, in the API and Streamlit: mark, whole counts, finite shares,
an allow-list, and the rates recomputed from the counts.

**Limitations, stated.** Test positives follow from a stratified split and the served
train and validation counts, to within one. The dataset is public and the split
reproducible, so this limits what the application says, not what can be found out by
fetching the data. A withheld block is a weak signal that can't be removed. The gate's
result on the real scores is unknown until an evaluation runs, and it is an argument
about the average case, not a proof. Two artifacts with different flags, each evaluated,
can be compared. The API trusts the mark on a record, since it has no scores.

**History.** Old values stay in `reports/final_evaluation.md` and in Git history, and
nothing here removes them. Regenerating the report means `make evaluate CONFIRM=yes`,
which adds to the partition's history: a decision for the project's owner.

**Retention queue.** `Segment.in_queue*` count flagged customers not recorded as churned,
so a key figure equals the queue it opens by construction; `flagged.customers` stays
for "See all", since the explorer lists churners too. `CustomerPage.band_counts` is
`select()` without the band filter, so a chip says what choosing it would list under any
partition or search. `current_monthly_charges` is what "recurring" now shows. A withheld
outcome counts as current: nothing reads it. `/api/overview`'s band `flagged_not_churned`
stays book-level for the default bands and the empty states.

## Disclosure governance, third pass (2026-11-06)

An independent review of the second pass returned FAIL. This section supersedes the
parts of "Disclosure remediation, second pass" that describe `tie_bits`,
`reports/final_evaluation.json`, the sidecar's `test_metrics` and `remove_test_figures`:
none of them exists any more. Nothing here read the real held-out partition, and no
final evaluation was run.

**Decisions (the maintainer's).**

- Held-out Brier, ROC-AUC and PR-AUC are withheld unconditionally. `tie_bits` measured
  how many group-count vectors the scores allow, not how many label assignments fit
  the figures; its 200 had no derivation, and jitter at a rounding edge could pass it.
- No code reads or writes `reports/final_evaluation.md`, and the README no longer links
  to it. It stays in the repository and in Git history with figures this policy does not
  publish: an open governance issue, not a resolved one.
- One frozen release per partition, enforced rather than documented
  (`evaluation/release.py`, `SECURITY.md`, "The held-out release").
- Customers have three states: `active`, `churned`, `unknown`.

**Design choices.**

- The ledger is the release: state, fingerprints and released figures in one file,
  replaced in one step. Separate record and report files would have needed digests to
  stay in step, and a digest of a small table can be brute-forced while a release is
  unfinished. `reports/held_out_release.md` is rendered from the ledger, has no
  authority, and comes back without scoring if lost.
- A single ledger, not one per split: a changed seed overlaps the same customers.
- Its path is a constant under the project root. `CHURNSENSE_CONFIG` can move every
  configured path and `make clean` empties `reports/` and `artifacts/`, so neither could
  hold it. The command also loads the default configuration by name.
- It ships `open`, and a missing file refuses: deleting it cannot start a new release.
- A reader told of a missing or damaged ledger reports "withheld", not "nothing recorded".
- The evaluation fingerprint hashes plain data built from the AST, of `disclosure.py` and
  of the definitions in `metrics.py` a figure comes from: `ast.dump` differs between
  Python 3.12 and 3.13 (checked on both), and an unrelated function must not strand a
  reservation.
- `model_meta.json` is not rewritten by an evaluation any more, which removes the one
  partial-write failure that could take the model down.
- Unknown customers stay in the queue, named apart, because the alternative treats them
  as churned; they count in neither recurring revenue nor revenue at risk.

**Still open.**

- A second clone does not see an uncommitted ledger; commit it right after a release.
- Whether earlier tables in Git history differ from the eventual release is unverified
  without running it.
- `make clean` still deletes the tracked `reports/final_evaluation.md`, as it always has.
- Existing `reports/figures/reliability.png` and `precision_recall.png` are local,
  git-ignored and served nowhere; they are left alone rather than deleted.
- A `ValueError` while scoring (a partition of one outcome, scores that are not
  probabilities) publishes a withheld release, which is final. Fixing a broken model
  would change the artifact and be refused anyway, so a retry could not help.
- Streamlit's target list on the simulator page lists everyone flagged, churners included:
  it reads scores only and claims no status. Streamlit has no retention queue.
- Named by the review and outside this pass: `eda.py`'s prescriptive sentence, the
  `TypeError` a text value raises in `reports.py`, and the SHAP cache reader's
  `RecursionError`.

## Release governance (2026-11-07)

An independent audit failed the previous pass. This one replaces two of its choices: a `ValueError`
while scoring no longer publishes a withheld release, and the evaluation fingerprint no longer
hashes syntax trees. Where this differs from the sections above, this section stands. The
procedure and the rules are in `SECURITY.md`, "The held-out release".

- **Artboards.** All eight `docs/design/*.dc.html` carried held-out figures, not only
  `Performance.dc.html`: each sidebar quoted test ROC-AUC and test ECE. All are clean, in the tree
  and in this branch's unpublished history, whose sixteen commits were rewritten and re-signed
  before it was first pushed. Figures already on `main` stay where they are.
- **Scan.** `documentation_surface` is every Markdown, HTML and text file outside the protected
  and generated folders. `html_violations` reads a page's visible text, where a label and its
  value are separate elements. A failure names the file and a count, never the line.
- **Scope of the rule.** The ledger and `flock` reach one working tree. One machine and one
  operator make a release the project's, and `held-out-release-governance` is a best-effort
  consistency check on pull requests. A calculation elsewhere on a public dataset is not
  preventable and is not claimed to be.
- **`open` to `published` is allowed by the check.** One run moves through `reserved` without
  committing it, so this is what an ordinary release looks like in a pull request.
- **Identity.** Source files are hashed by their bytes, for the evaluation, the data path and
  the artifact's classes, with the runtime and the lock's digest. Editing one refuses even a run
  that would only read a release back; readers are not affected.
- **Tests needing tracked reports** are integration checks (`tests.conftest.tracked_report`):
  failed in GitHub Actions when the file is absent, skipped elsewhere with a reason that tells a
  generic CI from a local copy. They may ask only for the four validation and EDA reports it
  lists, so none can come to need final evaluation data.

**Governance hardening (after the focused verification).** That review reproduced five blockers.
The maintainer's decisions: no `pull_request_target`, and the check is accepted as best-effort.

- **The check is what it is.** `pull_request` lets a change edit its own workflow, and a merged
  change can replace the script. It is not in the ruleset, and requiring it would not make it
  tamper-resistant. The documents now say so, a test keeps them saying so, and
  `test_known_limit_a_merged_change_can_weaken_the_check_for_the_next_one` holds the limit in code.
- **Strict schema in both readers.** A closed set of fields per state, a table whose cells, totals
  and rates are checked, the threshold of its identity, exact format and policy (only the
  pull-request check lets an `open` ledger stand under a newer one), and only the bytes the writer
  lays out. `evaluated_at` had been free text, and so a way round the allowlist. The two readers
  share no code, since the script must run alone: `tests/ledger_payloads.py` goes through both, and
  a seeded comparison holds them together. A valid shape is still not a true value: neither reader
  can tell a real fingerprint or count from an invented one.
- **A failure is not recorded as an answer.** Only a table under the floor is the rule's final
  answer. A scorer's malformed output now raises and leaves the ledger `reserved`, where it used to
  be recorded as a withheld release, and the writer checks its own output before replacing the file.
  A failure that repeats stays reserved: see the next pass.
- **No recovery.** `reserved` to `open` was guarded by a record nothing could verify, under a ruleset
  requiring no approval. It is removed rather than disabled: a same-identity retry remains, a
  different identity is refused, and a record is refused in any change. A reservation that can no
  longer run stays reserved. `tests/test_release.py` no longer fails on a committed reservation,
  which is how a failed run survives a local `git restore`.

**Evaluation safety (after the next review).** That review reproduced a deterministic failure
permanently consuming the release, and found the documents saying otherwise.

- **A rehearsal before the reservation.** `report.rehearse` runs the scoring and the metrics on
  validation rows and drops the results, then the description, summary, report, ledger and metadata on
  one withheld evaluation and one invented table, with output in a temporary directory. A failure
  stops the run with the ledger `open` and the held-out path not started. It reduces the risk of a
  repeating failure and does not remove it.
- **The boundary is the maintainer's decision.** Touching no held-out row or label at all cannot hold,
  since `make_splits` loads the whole file and stratifies on every label. The decision: the rehearsal
  gets `ValidationRows`, an object with nowhere to hold a test row, and a spy and an AST test show none
  reaches the model or a metric before the reservation. The test rows are still in the process's
  memory, as they are in `make dev`. The rehearsal has never run on the real artifact.
- **After publication.** Everything between the publication and the printed summary is handled:
  only the class of a failure is logged, the exit status is non-zero, the ledger is not touched and
  nothing is scored again. The message says what the ledger holds, read and not assumed.
- **Next policy.** An `open` ledger may stand one version ahead, only with `POLICY` moved in the script
  and in `disclosure.py` in the same change. It used to take any larger integer.
- **Retry tests.** A different scorer under an unchanged identity is not a source fix. Those tests now
  separate a transient failure, a failure that repeats, and an edit to a real fingerprinted file,
  which is refused.

**Still open.**

- `held-out-release-governance` has to run for the first time on GitHub, and may then be added to
  the `Protect main` ruleset by hand. Neither has happened.
- The rehearsal and preflight have not met the real artifact: `make evaluate` is what runs them.
- A reservation that cannot finish has no recovery path until there is an approval that can be
  verified (`SECURITY.md`, "A reservation has no way back").
- The generated text of `eda.py` and `report.py` changed, so tracked `reports/eda_report.md` and
  `reports/model_comparison.md` keep the old sentences until the next `make eda` and `make train`.
- Local `src/churnsense.egg-info/PKG-INFO` holds an old README with held-out figures. It is
  git-ignored and outside the scan.

## Completion pass (2026-11-08)

An independent review of the pass above passed it with conditions. This pass closes them; where it
differs from the sections above, it stands.

- **No Git change mid-release.** "After a reservation" sent a failed run's operator to commit the
  reservation on a new branch and open a pull request, while the same section forbade branch changes;
  from a checkout carrying other work that pull request would also have carried it. Now nothing in Git
  moves in that checkout until the ledger reads `published`: a transient failure is retried by the same
  command, anything else stops for the maintainer, and the ledger is committed only after publication,
  on a branch made from `main`. The uncommitted reservation can still be lost to a local rollback; that
  is stated as the accepted price. `make all` is named among the forbidden commands. This supersedes
  the "committed reservation" note above.
- **The loader and the hash helpers are fingerprinted.** `models/train.py` joins the model's
  sources: its `load_artifact` checks the artifact's bytes against the hash the identity records, and
  an edit to it could have scored something else under the same identity. `data/download.py` joins the
  data path's, since its `sha256_of` does that check and `verify_dataset` gives the dataset's hash
  (found by the review of this pass). `config.py` stays out, since what it parses is fingerprinted by
  value. A test checks that the scoring path's steps and both helpers sit in fingerprinted files; the
  list is kept by hand, not derived from the calls.
- **Wording.** A report rendered again after publication is drawn "without scoring the test partition
  again": validation is scored again for the report's sweep.
- **Two readers no longer crash on a damaged file**, closing the two named under "Still open" of the
  disclosure pass: `reports.py` refuses text in a number column as `DataError` instead of raising
  `TypeError` (association ranking) or passing it (model comparison); the SHAP cache reader reads
  nesting past the recursion limit and an integer past the digit limit as "not cached".
- **Tornado 6.6** in `requirements-dev.txt` (via the Jupyter stack only; the runtime lock has none),
  through `make lock LOCK_ARGS="--upgrade-package tornado"`. No other pin moved. Both locks audit
  clean, `pip check` passes, and a notebook kernel runs, on Python 3.12 and 3.13.

