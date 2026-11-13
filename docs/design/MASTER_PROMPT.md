# Master prompt — implement the ChurnSense AI dashboard design in code

> Paste everything below the line into Claude Code, opened at the root of the
> `churnsense-ai` repository, with this bundle's `design/` folder copied to
> `docs/design/` first. Work through it phase by phase; do not skip the gates.

---

You are implementing a production front end for **ChurnSense AI**, a telecom
customer-retention decision-support system. The repository already contains a
reproducible ML pipeline (`src/churnsense`), a FastAPI service
(`src/churnsense/api`), a Streamlit dashboard (`app/`) and 634 passing tests.
A finished visual design lives in `docs/design/` (eight `.dc.html` artboards
plus `canvas.json`; open `docs/design/preview/*.html` through a local HTTP
server to click through it). Your job is to turn that design into real,
maintainable, secure code **without weakening any guarantee the repository
already makes**.

Read `README.md`, `docs/MODEL_CARD.md`, `docs/PROJECT_WALKTHROUGH.md`,
`SECURITY.md`, `configs/config.yaml`, `src/churnsense/api/main.py`,
`src/churnsense/api/schemas.py`, `src/churnsense/models/predict.py`,
`src/churnsense/hosts.py`, `app/data.py` and every artboard before writing
code. Summarise what you learned and the plan in `docs/frontend/PLAN.md`, then
stop and wait for approval.

## 0. Non-negotiable principles (from the repository — keep every one)

1. **One trained artifact, one prediction path.** Every probability shown
   anywhere comes from `models/predict.py` through the API. The design
   prototype ran an exported copy of the model in the browser; **do not ship
   that**. The front end never computes a probability, a risk band, a flag,
   a SHAP value or a recommended threshold itself. It renders what the API
   returns.
2. **The test partition's outcomes are never sent to a client.** Membership is
   trusted only when the dataset SHA-256 and split digest match the model's
   record (`app/data.py`); otherwise every outcome is withheld. Enforce this
   server-side and assert it in tests. A test row's outcome is the literal
   string `"withheld"`, never `null` that a client could misread.
3. **No causal language.** Attributions are "raises/lowers the estimate",
   never "causes", "drives", "will prevent", "guarantees". Extend the
   existing wording test to every user-visible string in the front end.
4. **Every money figure is labelled SIMULATED** and shows its assumptions
   (offer cost, success rate, horizon, margin from `configs/config.yaml`).
5. **Numbers are produced, never typed.** No metric, count or rate may be
   hard-coded in the front end. Everything comes from an endpoint backed by
   the artifact, `reports/*.csv|md` or the cached SHAP file.
6. **Calibration and clamp stay inside the artifact.** Display clamped values
   honestly: `0.9999` must not render as `1.000` (use 4 decimals above 0.999).

## 1. Stack and structure

- `frontend/`: React 18 + TypeScript (strict) + Vite. No UI kit; implement the
  design's own components. Charts are hand-built SVG with `d3-scale` /
  `d3-shape` only. Fonts: Geist and Geist Mono, **self-hosted** (woff2 in the
  bundle, no Google Fonts request at runtime).
- State: React Query (TanStack) for server data; URL search params for every
  filter, sort, page and selected item, so views are shareable and the back
  button works. No global store unless a real need appears.
- Types: generate TypeScript types from the FastAPI OpenAPI schema
  (`openapi-typescript`) in CI; the build fails if the generated file is stale.
- Serving: FastAPI serves the built static files under `/` with the API under
  `/api`, so one origin, one host allowlist and no CORS in production.
- Keep the Streamlit app working; do not delete it in this change.

## 2. API work (backend first, read-only unless stated)

Add endpoints in `src/churnsense/api/` with Pydantic response models, reusing
library functions; never duplicate analysis logic in a route.

| Endpoint | Returns |
|---|---|
| `GET /api/model-info` | exists; extend with artifact SHA-256, data SHA-256, split-digest status |
| `GET /api/overview` | KPIs, band counts and monthly charges, 20-bin probability histogram with observed churn per bin (train+validation only), flagged counts per band |
| `GET /api/customers` | paginated, filtered, sorted book: `q, bands[], contract, internet, payment, tenure_min, tenure_max, flagged, partition, sort, dir, page, page_size (≤100)`; outcome `"churned"|"stayed"|"withheld"` |
| `GET /api/customers/{id}` | profile, probability, band, flag, partition, outcome rule as above |
| `POST /api/explain` | SHAP contributions for one record via `explain_customer` with the shared training background and fixed seed; cache by record hash; base value + sum must equal the probability (assert) |
| `GET /api/segments` | train-partition cross-tab for two whitelisted dimensions; cells with n < 20 returned as `{"suppressed": true, "n": n}` with no rate |
| `GET /api/explain/global` | cached `make explain` output: importance, 300×18 matrix, feature values |
| `GET /api/threshold/sweep` | validation sweep (flagged, TP, retained-value base) plus, for query assumptions, the recommended threshold computed by the repository's own rule (`evaluation/threshold.py`) |
| `GET /api/performance` | recorded test metrics from `model_meta.json`, CV comparison, calibration figures, per-segment table |
| `POST /api/predict`, `POST /api/predict/batch` | exist; keep behaviour and limits exactly |

Rules: validate every query parameter (enums for dimensions/sort keys, ranges
for numbers), cap page size, return the repository's error shape
`{"error","detail","problems"}`, never echo filesystem paths or stack traces,
and keep `predict.py` the only inference path.

## 3. Security requirements (each needs a test)

- **Host allowlist / DNS rebinding:** keep `hosts.py` enforcement on every
  route including static files; default bind `127.0.0.1`.
- **Authentication:** the dashboard currently has none. If the app can be
  reached from anything but loopback, require auth (OIDC or at minimum a
  signed session cookie: `HttpOnly`, `Secure`, `SameSite=Strict`) and make
  `make app`/`make api` refuse a non-loopback bind without it.
- **CSRF:** state-changing requests (`POST`) require a same-origin check plus
  a CSRF token when cookie auth is enabled.
- **Headers:** strict CSP (`default-src 'self'; script-src 'self';
  style-src 'self'; img-src 'self' data:; font-src 'self';
  connect-src 'self'; frame-ancestors 'none'; base-uri 'none';
  form-action 'self'`), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` denying camera, mic,
  geolocation, and HSTS when served over HTTPS. No inline scripts or styles
  that would force `unsafe-inline` (move design styles into CSS modules).
- **Uploads:** server-side limits stay authoritative: 5 MiB body (reject
  before buffering, 413), 10,000 rows, 1,000 columns, field-size cap, UTF-8
  only. Client-side checks are only a convenience. Never write uploads to
  disk; never `eval`/`pickle` user input.
- **CSV injection:** every exported CSV (scored batch, explorer export) must
  neutralise cells starting with `=`, `+`, `-`, `@`, tab or CR by prefixing
  `'`; customer IDs and categories included.
- **XSS:** no `dangerouslySetInnerHTML`; user-supplied strings (customer IDs,
  CSV values, file names) are rendered as text only; file names truncated.
- **Rate limiting:** per-client limits on `/api/predict/batch` and
  `/api/explain` (SHAP is expensive) with 429 responses.
- **Data minimisation:** no customer data in URLs beyond the customer ID,
  nothing in `localStorage` except UI preferences, no third-party requests,
  no analytics.
- **Supply chain:** `package-lock.json` committed, `npm ci` only, exact
  versions, `npm audit --audit-level=high` and the existing `pip-audit` in CI,
  Dependabot for npm, no CDN scripts.
- **Errors:** generic 500 body, details only in logs; front end shows a
  retry state, never raw error text from the server.

## 4. Design implementation rules

Source of truth: `docs/design/*.dc.html`. Reproduce layout, spacing, type
scale and every interaction listed there.

- Tokens (put in `frontend/src/styles/tokens.css`): ground `#0a0d12`,
  surface `#10151c`, sidebar `#0c1016`, border `#1f2730`, ink `#e6edf3`,
  ink-2 `#adb7c2`, muted `#8b949e`, accent `#1897ae` (text `#5cc8dc`),
  simulated amber `#f0b54a` (text `#f6c873`). Risk ramp (sequential,
  lightness rises with risk): Low `#56667b`, Moderate `#6b62b0`, High
  `#a35ec9`, Critical `#e8578f`. Attribution diverging pair: raises
  `#ec835a`, lowers `#5b8def`. Radii 9/12/14/16 px; base text 14 px;
  numerals tabular; Geist Mono for figures and tags.
- Colour never carries meaning alone: every band mark has a text label.
  Contrast ≥ 4.5:1 for text, ≥ 3:1 for chart marks.
- Pages: Executive overview, Customer explorer, Churn patterns,
  Explainability, Single customer (with live "new customer" scoring through
  `POST /api/predict` + `POST /api/explain`), Retention simulator, Model
  performance, Batch scoring. Navigation is a collapsible sidebar on desktop
  and a left slide-in drawer on screens ≤ 860 px (hamburger, backdrop, focus
  trap, `Esc` closes, focus returns to the trigger).
- Charts: SVG with accessible names (`role="img"` + `aria-label` summary)
  and a visually hidden data table alternative for each chart. Tooltips are
  HTML, flip at the right edge, follow pointer and keyboard focus.
- Interaction states: loading skeletons, empty states with a reason and a
  reset action, error states with retry, disabled states while requests run.
  Debounce text filters (250 ms); cancel stale requests (AbortController).
- Responsive: no horizontal page scroll at 375, 768, 1024, 1440 px; wide
  tables scroll inside their own box; `overscroll-behavior-x: none`.
- Motion: 150–300 ms transitions, all disabled under
  `prefers-reduced-motion`.
- Accessibility: real `<button>`, `<a href>`, `<label>`; visible focus ring;
  logical tab order; `aria-pressed` on toggles, `aria-sort` on sortable
  headers, `aria-live` on changing summaries; target size ≥ 40 px.

## 5. Correctness requirements (each needs a test)

- **Parity:** for a fixed set of customers, the probability rendered in the
  UI equals `POST /api/predict` equals `Predictor.predict_one` (≤ 1e-12).
- **Bands and flag** come from the API; the UI never re-derives them.
- **Threshold recommendation** uses the repository's rule (ties within one
  offer's cost break toward the highest threshold); with default assumptions
  it must reproduce 0.36, and on the full 0.01 validation grid (`make train`'s local
  report) the sensitivity table must reproduce 0.88 / 0.50 / 0.36 / 0.23 / 0.22 for
  10–50 % success. *(Added 2026-11-13: a public demo applies the rule among its published
  cuts only, so its sensitivity moves in coarser steps.)*
- **SHAP additivity:** base + Σ contributions = probability (≤ 1e-9) for
  every explanation the API returns.
- **Withheld outcomes:** no response from any endpoint contains a test-row
  outcome (scan responses in a test).
- **Suppression:** no segment response contains a rate for n < 20.
- **Formatting:** one tested formatting module (percent, money, signed pp,
  probability with clamp-aware precision, integers with separators).
- **Wording:** the causal-phrase test runs over the built front-end bundle.

## 6. Testing and quality gates

- Backend: pytest for every new endpoint (happy path, validation errors,
  limits, withheld outcomes, error shape, no paths in bodies), keeping the
  existing 634 tests green.
- Front end: Vitest + Testing Library for components and formatters;
  MSW-mocked API contract tests built from the generated types; Playwright
  end-to-end on all eight pages at 375 and 1440 px, including drawer,
  filters, sorting, pagination, tooltips, batch upload (demo fixture and a
  deliberately broken file), and keyboard-only navigation; `axe-core` with
  zero serious violations; a check that `document.scrollingElement.scrollWidth
  <= clientWidth` on every page and width.
- Static: `tsc --noEmit` strict, ESLint (react-hooks, jsx-a11y, security
  plugin), Prettier, ruff; zero warnings in CI.
- CI (`.github/workflows/ci.yml`): add a front-end job (`npm ci`, lint,
  typecheck, unit, build, e2e against `make api` with the synthetic model the
  tests already use), OpenAPI type freshness check, `npm audit`.

## 7. Working method

1. Phase 1 — plan (`docs/frontend/PLAN.md`), wait for approval.
2. Phase 2 — backend endpoints + tests. Run the full suite.
3. Phase 3 — front-end shell (tokens, layout, sidebar/drawer, routing,
   API client, error/loading patterns) + tests.
4. Phase 4 — pages one by one, each with its tests, in this order: overview,
   explorer, single customer, batch, simulator, performance, explainability,
   patterns.
5. Phase 5 — security headers, auth gate, rate limits, CSV-injection guard,
   audits; update `SECURITY.md` and `README.md` (with measured numbers only).

After every phase: run all tests and linters, show the output, and list
anything not done. Never claim a check passed without running it. Never
modify training, the artifact, `configs/config.yaml` business values or the
dataset. Never commit secrets. If a requirement conflicts with what the
repository does, stop and ask instead of choosing silently.
