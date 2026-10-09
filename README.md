# ChurnSense AI — Customer Retention Intelligence Platform

[![CI](https://github.com/HeyderHesenov/churnsense-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/HeyderHesenov/churnsense-ai/actions/workflows/ci.yml)

A decision-support system for telecom customer retention: it estimates churn
probability, explains what moved each estimate, segments customers into
operational risk bands, and lets an analyst explore the economics of a
retention campaign under assumptions they control.

**Every number in this README was produced by running the code.** Nothing is
copied from a previous run, a tutorial, or the literature. The commands that
produce each table are named beside it, and the artifacts they write
(`reports/*.md`, `reports/*.csv`) regenerate from the SHA-256-pinned raw file.

> **What this is not.** It is not a causal model and it cannot say why a
> customer leaves. It does not prove any retention action works. Every
> monetary figure anywhere in this project is a **simulation** under stated
> assumptions, because the dataset contains no record of any retention
> campaign.

---

## Contents

- [Business problem](#business-problem)
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Dataset](#dataset)
- [What the data says](#what-the-data-says)
- [Modelling](#modelling)
- [Evaluation](#evaluation)
- [Threshold as a business decision](#threshold-as-a-business-decision)
- [Explainability](#explainability)
- [Dashboard](#dashboard)
- [API](#api)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Security](#security)
- [Limitations](#limitations)
- [Further reading](#further-reading)

---

## Business problem

A telecom operator loses customers it could have kept. Retention offers cost
money, so contacting everyone is wasteful and contacting no one is worse. The
operator needs to know **which customers to contact**, **why they look at
risk**, and **where the cut-off should sit** given what an offer costs and how
often it works.

That last question is the one most churn projects skip. A model that ranks
customers well is useless until someone decides where to draw the line, and
that line is a commercial decision, not a modelling default. This project
treats it as such and makes the trade-off explicit and adjustable.

## What it does

| Capability | Where |
|---|---|
| Reproducible EDA with written interpretation | `make eda` → [`reports/eda_report.md`](reports/eda_report.md) |
| Four-model comparison with a principled selection rule | `make train` → [`reports/model_comparison.md`](reports/model_comparison.md) |
| Single, final test-set evaluation | `make evaluate` → [`reports/final_evaluation.md`](reports/final_evaluation.md) |
| Threshold economics under adjustable assumptions | dashboard → *Retention simulator* |
| SHAP explanations, global and per customer | `make explain`, dashboard → *Explainability* |
| Narrated EDA notebook (executed, outputs committed) | [`notebooks/01_eda_walkthrough.ipynb`](notebooks/01_eda_walkthrough.ipynb) |
| Eight-section BI dashboard | `make app` |
| REST API with validated schemas | `make api` |
| Batch CSV scoring with validation | dashboard and `POST /predict/batch` |

## Architecture

One trained artifact, one prediction path, three consumers. This is the
structural decision the whole project rests on: because `models/predict.py` is
the only code that turns features into a probability, the dashboard, the API
and batch scoring **cannot** disagree.

```
  configs/config.yaml ── seed, split sizes, model grids, business assumptions
          │
          ▼
  data/raw/*.csv ──► data.loader ──► data.split ──► features.preprocess
  (immutable,          cleaning      stratified      ColumnTransformer,
   SHA-256 pinned)     reported      60/20/20        fitted inside the Pipeline
                                          │
                                          ▼
                                   models.train
                            4 candidates → 1-SE selection → calibration
                                          │
            ┌─────────────────────────────┼───────────────────────────┐
            ▼                             ▼                           ▼
    evaluation.metrics          artifacts/model.joblib        explainability
    evaluation.threshold        artifacts/model_meta.json     (SHAP, seeded)
    (validation only)           (threshold, metrics, schema)
                                          │
                                          ▼
                               models.predict  ◄── the only inference path
                                          │
                     ┌────────────────────┼────────────────────┐
                     ▼                    ▼                    ▼
              app/ Streamlit        api/ FastAPI          batch CSV
```

**Guarantees built into the structure, not into a convention:**

- `train_all()` takes training and validation data only — the test partition is
  not a parameter, so no tuning path can reach it. A test asserts the signature.
- Preprocessing is a `Pipeline` step, so it is refitted inside every CV fold —
  in the hyper-parameter search *and* in the calibration folds — and cannot be
  fitted on data outside it. A test checks each calibration fold's scaler.
- Inputs are validated on that same single path, so an unknown category or an
  impossible number is rejected by the library, the dashboard and the API
  alike, never silently encoded into a confident score.
- The decision threshold lives in `model_meta.json`, so all three consumers
  read the same operating point.

## Quick start

Requires **Python 3.12+** (developed on 3.12.13; the fast test suite also passes
on 3.13.5). The floor comes from the lock: shap 0.52, scipy 1.18 and
contourpy 1.4 no longer support 3.11. If your default `python3` is older, pass
a newer one — `make setup` checks before it creates anything and tells you
which interpreters it can find:

```bash
make setup PYTHON=python3.12
```

```bash
make setup      # .venv from the lock in requirements.txt
make data       # download the dataset and verify its SHA-256
make all        # data → eda → train → explain → evaluate
make app        # dashboard  → http://localhost:8501
make api        # API        → http://localhost:8000/docs
```

`make help` lists every target. Individual steps:

```bash
make eda        # reports/eda_report.md + reports/figures/*
make train      # trains and compares 4 models, writes artifacts/
make evaluate   # the single test-set scoring + threshold economics
make explain    # caches global SHAP importance
make test       # pytest
make lint       # ruff check + format --check
make audit      # pip-audit
make clean      # remove generated output (raw data is kept)
```

### If the download fails

`make data` fetches from GitHub over HTTPS with an explicit `certifi` CA
bundle. If it still fails, it prints manual instructions: download
[the CSV](https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv)
and save it to `data/raw/Telco-Customer-Churn.csv`, then re-run to verify.

> A python.org CPython build on macOS ships without CA roots until its bundled
> `Install Certificates.command` is run, which makes `urllib` raise
> `CERTIFICATE_VERIFY_FAILED` on a perfectly healthy network. That is why the
> downloader sources its trust store from `certifi` instead of the interpreter
> default.

## Dataset

**IBM Telco Customer Churn**, from
[IBM/telco-customer-churn-on-icp4d](https://github.com/IBM/telco-customer-churn-on-icp4d)
(Apache-2.0). 7,043 customers, 21 columns, one row per customer.

- **Target:** `Churn` (Yes/No) — 1,869 Yes, **26.54 %**.
- **Pinned:** SHA-256 `16320c9c1ec72448db59aa0a26a0b95401046bef5d02fd3aeb906448e3055e91`,
  verified on every run. An upstream change halts the pipeline rather than
  quietly altering documented results.
- **Not committed.** `make data` fetches it; `data/raw/` is gitignored.

### Data quality, measured

| Check | Result |
|---|---|
| Missing values | 0 |
| Duplicate rows | 0 |
| Duplicate `customerID` | 0 |
| `TotalCharges` non-numeric | **11 rows** (a single space) |

Those 11 rows all have `tenure == 0`: customers who have not been billed yet.
They are filled with **0.00**, not the median — median imputation would invent
about $1,400 of spend for a brand-new customer, precisely in the tenure region
the model is most sensitive to. Every cleaning action is reported by
`CleaningReport` and appears in the EDA report.

## What the data says

Full write-up with figures: [`reports/eda_report.md`](reports/eda_report.md).

**Contract type is the strongest single signal.**

| Contract | Customers | Churn rate |
|---|---|---|
| Month-to-month | 3,875 | **42.7 %** |
| One year | 1,473 | 11.3 % |
| Two year | 1,695 | 2.8 % |

A 15× spread — but customers who expect to stay are also the ones willing to
sign a long contract, so part of that gap is self-selection rather than an
effect of the contract. The snapshot cannot separate the two. What it does
support is *targeting*.

**Risk is front-loaded.** Churn runs at 56.2 % in the first three months and
falls to 9.5 % past four years. Median tenure is 10 months for churned
customers against 38 for retained ones.

**Price is not monotonic.** Churn peaks at **36.4 %** in the $75–95 band and
*falls* to 32.3 % above $95. A plain "higher price drives churn" story does not
fit that shape — the top band is dominated by long-tenure customers holding
several services, so price and commitment pull against each other there. It is
also a concrete argument for a model that can express non-linearity.

**`gender` carries no signal: bias-corrected Cramér's V = 0.0000.** It is
excluded from the model as a protected attribute, and the exclusion costs
nothing measurable: putting it back changes cross-validated PR-AUC by
−0.0010 against a fold-to-fold standard deviation of 0.0244 (the ablation in
[`reports/model_comparison.md`](reports/model_comparison.md)). See
[`docs/MODEL_CARD.md`](docs/MODEL_CARD.md).

**`TotalCharges` is near-redundant**, correlating r = 0.9996 with
`tenure × MonthlyCharges`. This is collinearity, **not leakage** — all three are
known at prediction time. It is kept: dropping it costs −0.0039 CV PR-AUC, inside
the fold noise, so this is a judgement call — made explicitly, with the
consequence (unstable coefficients in the linear model) documented rather than
hidden.

## Modelling

Four candidates, all as full `Pipeline`s, all one-hot encoded for a single
uniform column contract:

| Model | Role |
|---|---|
| `DummyClassifier(strategy="prior")` | the floor every other number is read against |
| `LogisticRegression(class_weight="balanced")` | interpretable linear baseline |
| `RandomForestClassifier(class_weight="balanced_subsample")` | bagged trees |
| `HistGradientBoostingClassifier(class_weight="balanced")` | gradient boosting |

**Protocol.** Stratified 60/20/20 (4,225 / 1,409 / 1,409), seed 42, positive
rate preserved at 0.2653 / 0.2654 / 0.2654. Hyper-parameters are searched with
5-fold stratified CV on **train**; the model and the threshold are chosen on
**validation**; **test** is scored exactly once, at the end.

**Imbalance** is handled by `class_weight`, not resampling. At roughly 1 : 2.8
the imbalance is moderate, weighting cannot leak synthetic rows across a fold
boundary, and it leaves the probability scale interpretable — which matters
because the business layer multiplies by it.

### Measured comparison (`make train`, validation partition)

| Model | CV PR-AUC | CV std | Val PR-AUC | Val ROC-AUC | Selected |
|---|---|---|---|---|---|
| Logistic Regression | 0.6663 | 0.0244 | **0.6444** | 0.8373 | **yes** |
| Hist Gradient Boosting | 0.6705 | 0.0148 | 0.6416 | 0.8369 | |
| Random Forest | 0.6714 | 0.0152 | 0.6365 | 0.8380 | |
| Baseline (prior) | 0.2653 | 0.0005 | 0.2654 | 0.5000 | |

### Why logistic regression, and not "it scored highest"

The top three sit within **0.008** of each other against a cross-validation
standard error of **0.0109**. The nominal winner is decided by sampling noise;
change the seed and the order reshuffles. "It beat the booster by 0.003" is not
an answer that should survive a review.

Selection therefore uses the **one-standard-error rule**: every model within one
standard error of the leader is treated as tied, and the tie breaks on
simplicity. All three real models are tied here, so the simplest wins. That
choice survives a re-run, is cheaper to serve, and is far easier to explain to a
retention team.

### Calibration

Isotonic calibration was fitted, **measured, and kept because it helped**:
expected calibration error on validation fell from **0.1441 → 0.0274**.
It is fitted with 5-fold cross-validation on the training partition, and each
fold refits the *whole* pipeline — preprocessing included — on its own rows.

Calibration is a headline concern here, not a tidiness exercise — the retention
simulator multiplies a predicted probability by a customer's value, so the
number has to mean what it says.

It also changed the operating point sharply. `class_weight="balanced"` inflates
raw scores, so the calibrated model behaves very differently at the same cut:

| At threshold 0.50 | Candidate (uncalibrated) | Shipped (calibrated) |
|---|---|---|
| Precision | 0.5095 | **0.6723** |
| Recall | 0.7888 | **0.5321** |
| Brier | 0.1678 | **0.1379** |

`model_meta.json` reports the **shipped** model's own metrics. Publishing the
candidate's numbers for a calibrated artifact would describe a model that never
ships; a test enforces the distinction.

> **Probabilities are clamped to `[ε, 1−ε]` with `ε = 1/(2·n_train) = 1.2e-4`.**
> Isotonic calibration is a step function, so its terminal bins assigned exactly
> 0.000 to 210 customers and exactly 1.000 to four. No model fitted on 4,225
> rows can claim certainty, and a probability of exactly 1 has infinite log-odds.
> ε is the finest rate the calibration sample can express, not a round number,
> and sits three orders of magnitude below the operating threshold, so no
> decision changes. Clipping is monotone, so ranking is untouched.
>
> The clamp lives **inside the persisted artifact**, not in one consumer, so
> the reports, SHAP and the API all describe the same function. It did not,
> once: the explainability page showed the same customer as `0.9999` in one
> place and `100.0%` in another. See
> [`docs/PROJECT_WALKTHROUGH.md`](docs/PROJECT_WALKTHROUGH.md) §5.

## Evaluation

Full report: [`reports/final_evaluation.md`](reports/final_evaluation.md).
Produced by `make evaluate`, which is the only place the test partition is read.

**Test partition: 1,409 customers, 374 churned (26.5 %). Threshold 0.36, fixed
on validation before this partition was touched.**

> **Disclosure.** The audit that moved calibration inside the cross-validation
> folds and rebuilt the explainer also re-ran `make evaluate`, so this partition
> has now been scored twice in the project's history. Both fixes were chosen on
> principle, before the second scoring, and no decision used either result.
> The earlier figures differed by at most 0.003 (recall 0.7005 → 0.7032).

| Metric | Test |
|---|---|
| Precision | **0.5525** |
| Recall | **0.7032** |
| F1 | 0.6188 |
| ROC-AUC | **0.8422** |
| PR-AUC (average precision) | **0.6251** |
| Brier score | 0.1385 |
| Expected calibration error | **0.0275** |
| Accuracy | 0.7700 |

|  | Predicted stay | Predicted churn |
|---|---|---|
| **Actually stayed** | 822 | 213 |
| **Actually churned** | 111 | **263** |

Accuracy is listed last deliberately. Predicting "nobody churns" scores
**73.5 %** on this partition and is worth nothing — which is the whole reason
this project headlines PR-AUC, precision and recall instead.

PR-AUC of 0.6251 against a no-skill baseline of 0.265 is a **2.4× lift**.

### The false-positive / false-negative trade

They are not symmetric, and that asymmetry is the whole argument for tuning the
threshold:

- A **false negative** is a customer who leaves without ever being offered
  anything. The cost is their remaining margin — hundreds of dollars.
- A **false positive** is an offer to someone who was going to stay. The cost is
  one offer — $50 under the default assumptions. (Any discount such a customer
  then keeps is real but not modelled; see the model card.)

At roughly 10:1, recall is worth buying with precision, and the optimum sits
well below 0.5.

## Threshold as a business decision

```
value of a retained customer = monthly charges × horizon × gross margin
retained value  = Σ over true positives of (value × offer success rate)
campaign cost   = every flagged customer × offer cost
net benefit     = retained value − campaign cost
```

Default assumptions (`configs/config.yaml`, adjustable live in the dashboard):
offer cost **$50**, success rate **30 %**, horizon **12 months**, gross margin
**65 %**.

**Measured sweep on validation:**

| Threshold | Flagged | Precision | Recall | Simulated net benefit |
|---|---|---|---|---|
| 0.30 | 511 | 0.548 | 0.749 | $26,579 |
| **0.36** | **448** | **0.580** | **0.695** | **$26,547** |
| 0.50 | 296 | 0.672 | 0.532 | $23,924 |

### Why 0.36 and not the peak at 0.30

The two differ by **$32** on a $26.5k figure built from an *assumed* success
rate. Taking the argmax would commit the campaign to 63 extra customers at lower
precision for a difference the simulation cannot resolve.

So operating points within **one retention offer's cost** of the maximum are
treated as indistinguishable, and the tie breaks toward the highest threshold —
the one that spends least and disturbs fewest customers. The tolerance is the
business's own unit of account rather than an invented epsilon, and it scales
automatically with the offer. Same discipline as the model selection, same
reason.

Choosing 0.36 over 0.30 moved test precision from 0.5296 to **0.5525**.

> For reference only, and then discarded: the threshold that would have been
> optimal *on the test partition* is reported in the final evaluation. Using it
> would be tuning on held-out data. The gap is published as an honest measure of
> how much threshold choice moves between samples.

## Explainability

`make explain` caches global SHAP importance; the dashboard reads the cache
rather than recomputing on every interaction.

**The shipped model is explained, never a convenient inner estimator.** The
explainer calls the artifact's own `predict_proba` — preprocessing, calibration
and clamp included — so contributions plus the base value reproduce the served
probability exactly; a test checks that sum. One model-agnostic Permutation
explainer covers every model family.

**Raw features, enough permutations, a fixed seed.** The 18 raw columns are
explained directly (categories passed as codes and decoded inside the model
call), so a masked feature swaps a whole value instead of half of a one-hot
pair. Each customer gets 10 sampled feature orderings. An earlier version used
one ordering over the one-hot matrix, and changing nothing but the seed moved
the top three drivers for over a third of customers; the mean seed-to-seed
spread is now about 0.3 percentage points against 1 point with one ordering.
The seed is fixed so a refresh never changes an explanation, and all pages
share one 100-customer training background, so "an average customer" means the
same thing everywhere.

**Measured global importance** (300 validation customers, 100-customer
training background):

| Feature | Mean \|SHAP\| | Share |
|---|---|---|
| Tenure (months) | 0.1488 | 18.4 % |
| Monthly charges | 0.1366 | 16.8 % |
| Internet service | 0.1185 | 14.6 % |
| Contract type | 0.0707 | 8.7 % |
| Total charges to date | 0.0675 | 8.3 % |

**This ranking disagrees with the EDA's univariate ranking, and both are
published.** Contract type is the strongest single signal on its own
(Cramér's V 0.410, rank 1) but only fourth here, because it overlaps heavily
with tenure and its *unique* multivariate contribution is smaller. Reconciling
the two into one convenient story would have meant hiding one of them.

Per-customer explanations are rendered as a signed waterfall plus plain
sentences. Wording is a correctness requirement, not a style choice: the test
suite asserts that causal phrasing ("causes", "will prevent", "guarantees")
never appears.

## Dashboard

```bash
make app     # http://localhost:8501
```

Eight sections: executive overview, customer explorer, drivers and segments,
model performance, explainability, single-customer scoring, retention
simulator, batch scoring and export.

Every chart carries the business question it answers as a visible caption. A
chart that could not be given one was deleted rather than kept as decoration.

**To capture screenshots:** run `make app`, open each section from the sidebar
and capture the viewport at 1600×1000. No screenshots are committed, so nothing
in this repository can drift out of date relative to the code.

## API

```bash
make api     # http://localhost:8000/docs
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness, and whether a model is actually loaded |
| `GET /model-info` | what is served: metrics, threshold, features, limitations |
| `POST /predict` | score one customer |
| `POST /predict/batch` | score a CSV upload |

```bash
curl -s -X POST http://localhost:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"SeniorCitizen":"0","Partner":"No","Dependents":"No","tenure":3,
       "PhoneService":"Yes","MultipleLines":"No","InternetService":"Fiber optic",
       "OnlineSecurity":"No","OnlineBackup":"No","DeviceProtection":"No",
       "TechSupport":"No","StreamingTV":"Yes","StreamingMovies":"Yes",
       "Contract":"Month-to-month","PaperlessBilling":"Yes",
       "PaymentMethod":"Electronic check","MonthlyCharges":95.0,"TotalCharges":285.0}'
```

```json
{
  "churn_probability": 0.802979373568,
  "risk_band": "Critical",
  "flagged": true,
  "threshold": 0.36,
  "model_key": "logistic_regression",
  "caveat": "A churn probability is an estimate of similarity to past churners, not a prediction of an individual's intent, and not a causal statement."
}
```

```bash
curl -s -X POST http://localhost:8000/predict/batch \
  -F "file=@tests/fixtures/demo_customers.csv"
```

Every uploaded row is scored — duplicates included — and each prediction
carries `row`, its 0-based position in the file, so results can be joined back
even when the file has no `customerID`.

**Verified consistency.** That same customer scores `0.802979373568` through
`POST /predict` and through the library; the dashboard form calls the same
`Predictor.predict_one`. On all 120 rows of the demo file the batch endpoint
matches the library to `0.00e+00`, and `POST /predict` row by row to `4e-16`
(the JSON float round trip).

**Behaviour under failure.** With no artifact the API returns **503** and says
so; it never produces a placeholder score. A request body over the upload limit
gets **413** before it is buffered. Unhandled exceptions return a generic
500 with the traceback in the log. Validation failures are returned in full —
they describe the caller's input, not ours. Every error body has the shape
`{"error", "detail", "problems"}`. No response body contains a filesystem path,
and a test asserts it.

## Testing

```bash
make test     # pytest
make lint     # ruff
```

**275 tests, all passing on Python 3.12.13.** Measured, not claimed:

```
$ make test
275 passed in 78.93s

$ pytest -m "not slow"          # needs no dataset
253 passed, 22 deselected
```

On a fresh checkout with no dataset and no trained artifact — the state
GitHub Actions runs in — the full suite reports **256 passed, 19 skipped**:
the slow tests that need a trained model skip cleanly.

Before the security hardening added its 12 tests, the then 241 fast tests also
passed on Python 3.13.5 (scikit-learn 1.9.1, shap 0.53.0) in a separate
environment resolved from the `pyproject.toml` constraints; CI runs 3.12 and
3.13 on every push.

The 22 `slow` tests drive the dashboard end to end and check that the
numbers in this README still match `artifacts/model_meta.json`; both need a
trained artifact and **skip cleanly** without one, verified by hiding
`artifacts/` and `data/raw/` and re-running. Everything else needs **no
dataset and no network**. `tests/conftest.py` generates
seeded synthetic data that reproduces the real file's *structure* — the same
columns and vocabularies, the "No internet service" dependency, the
blank-`TotalCharges`-at-zero-tenure quirk — and none of its statistics. Every
identifier is prefixed `DEMO-` so a fixture row cannot be mistaken for a real
observation, and **no test asserts a performance number** on it: the fixture's
statistics are invented, so a threshold on them would be theatre.

What the tests actually defend:

| Area | Example |
|---|---|
| Leakage | fits a scaler on a deliberately shifted training split and asserts held-out rows are transformed by the *training* statistics; checks every calibration fold fits its own scaler — behaviour, not a code-reading promise |
| Protocol | `train_all`'s signature cannot accept test data |
| Consistency | API, library and batch agree to 1e-12; a batch with duplicate rows returns every row at its file position |
| Input contract | unknown categories, impossible numbers and unknown target labels are rejected on every path, library included |
| Honesty | served probabilities never reach exactly 0 or 1; SHAP contributions sum to the served probability and do not hinge on the seed; narratives never use causal phrasing |
| Contract drift | the API's category vocabularies are compared against the training schema |
| Arithmetic | threshold economics checked against hand-computed values on tiny inputs |
| Dashboard | all eight sections driven through `streamlit.testing.v1.AppTest`; a section that raises on load is the cheapest bug to catch and the most embarrassing to miss |
| Documentation drift | the headline metrics in this README are parsed back out and compared to `model_meta.json`, so prose cannot outlive a retrain |

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs ruff,
pytest with coverage, an end-to-end training smoke test, an API smoke test, and
`pip-audit`, on Python 3.12 and 3.13.

CI needs no dataset and no network beyond the package index; the slow tests
skip there because no trained artifact exists in a fresh checkout.

`make audit` currently reports **no known vulnerabilities**.

### Verified in a clean checkout

The whole flow was run from a copy of exactly the 87 tracked files — no `.venv`,
no data, no artifacts, no caches — on 2026-10-09, before the security
hardening of the same day (whose test counts are the ones under
[Testing](#testing)):

```
make setup PYTHON=python3.12   ok   81 s, installed from the lock
make data                      ok   sha256 16320c9c... (matches the pin)
make all                       ok   12 figures, 6 reports
make test                      ok   263 passed
make lint                      ok   62 files already formatted
make audit                     ok   no known vulnerabilities
POST /predict (example above)  ok   0.802979373568
```

Every report and `model_meta.json` came out identical to the ones in this
repository except for the training timestamp and wall-clock fit times — same
split, same selection, same threshold 0.36, same test metrics.

## Project layout

```
configs/config.yaml          single source of truth for every tunable value
src/churnsense/
  config.py                  typed, validated config loader
  viz.py                     one palette for the report and the dashboard
  analysis.py                descriptive statistics shared by report and app
  eda.py                     generates reports/eda_report.md + figures
  data/      schema, download (certifi-aware), loader, split
  features/  preprocess      ColumnTransformer, built from the schema
  models/    registry, train, calibration_clamp, predict
                             ← predict.py is the only inference path, and it validates
  evaluation/ metrics, threshold, report, figures
  explainability/ shap_explain
  api/       main, schemas
app/                         Streamlit dashboard (theme, components, 8 sections)
notebooks/                   narrated EDA walkthrough; imports the package, holds no logic
tests/                       275 tests + seeded synthetic fixtures
docs/                        walkthrough, interview prep, model card
reports/                     generated: EDA, model comparison, final evaluation
artifacts/                   generated: model.joblib, model_meta.json (gitignored)
```

## Security

- **No secrets.** `.env.example` documents the two environment variables the
  code reads; `.env` is gitignored. The project uses no paid APIs and no
  external LLM services, so there is deliberately no API-key setting to leak.
- **Request bodies are bounded before anything buffers them.** Starlette
  spools a multipart upload to disk, and reads a JSON body into memory, before
  an endpoint runs, so a size check inside the endpoint would come too late.
  A small ASGI middleware refuses any body over 5 MB (plus multipart framing)
  with **413**: by its declared `Content-Length` without reading it, or, when
  chunked, the moment the running count crosses the limit.
- **Uploads are validated before anything is predicted**, cheapest check
  first: size, then row count, required columns, numeric ranges, category
  values and target labels. An unknown category is **rejected**, not bucketed
  — a typo must not become a confident prediction — and the same check runs
  inside `Predictor`, so no consumer can bypass it. Error messages quote at
  most five offending values, each cut to 40 characters, and the dashboard
  shows them as literal text, never as Markdown.
- **Exports cannot carry spreadsheet formulas.** The scored batch file keeps
  only `customerID`, `Churn`, the model's inputs and its outputs; any other
  uploaded column is dropped rather than echoed back. Every dashboard export
  then prefixes `'` to text — cells and column names — that starts with `=`,
  `+`, `-`, `@`, tab or carriage return, and to a trigger that follows `;` or
  a tab mid-cell, where Excel in `;`-separator locales would split the cell.
  Opened in Excel or Sheets they read as text instead of running.
- **No arbitrary deserialization.** The artifact path comes from config and is
  never caller-supplied; unpickling is equivalent to executing a file.
- **No SQL, no `eval`,** no execution of uploaded content.
- **Errors do not leak internals.** Generic messages to users, detail to logs,
  no filesystem paths in any API response. Every API error, the framework's
  404/405 included, has the documented `ErrorResponse` shape, and every
  response carries `X-Content-Type-Options: nosniff` and
  `Cache-Control: no-store`.
- **Never a fabricated prediction.** A missing or unreadable model surfaces as
  503 / a dead-end dashboard state, never as a default score.
- **Dependencies** are constrained in `pyproject.toml` and locked in
  `requirements.txt`, which `make setup` installs; `make audit` runs `pip-audit`.
  CI audits both the resolved environment and the lock itself, with a
  read-only `GITHUB_TOKEN`.

> **Local by design.** There is no authentication and no rate limiting, so
> `make app` and `make api` both bind to `127.0.0.1` (Streamlit alone would
> listen on every interface). The dashboard also shows Streamlit's default
> tracebacks, which is right for a local analyst tool. Before exposing either
> beyond localhost: put authentication, TLS and rate limiting in front of it,
> set `client.showErrorDetails = "none"` in `.streamlit/config.toml`, and
> consider disabling the API's `/docs` (it loads Swagger UI from a CDN). None
> of that is in scope here.

## Limitations

- **One split of one public dataset.** The intervals around every number above
  are wider than the decimal places suggest; a different seed moves them.
- **Association, not causation.** The model finds customers who resemble past
  churners. It cannot say why they leave, and it cannot say what an offer would
  do to any individual.
- **The economics are assumptions.** No campaign outcomes exist in this data.
  Change `offer_success_rate` and every monetary figure changes with it.
- **No time dimension.** Churn is labelled at a single cut, with no event dates.
  Nothing here is a survival or time-to-event analysis.
- **One global threshold leaves segments unscanned.** At 0.36 the model flags
  **no two-year-contract customer and no customer without internet** in the
  test partition. The headline recall of 0.70 is almost entirely
  month-to-month recall. `make evaluate` publishes the per-segment table in
  [`reports/final_evaluation.md`](reports/final_evaluation.md); per-segment
  thresholds would address it — see [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md).
- **No drift monitoring, no retraining schedule, no A/B framework.** A deployed
  version would need all three. None is in scope.
- **The artifact is fitted on 60 % of the data.** Refitting on train+validation
  would use more, but the calibration map and threshold were tuned on
  validation; see [`docs/PROJECT_WALKTHROUGH.md`](docs/PROJECT_WALKTHROUGH.md).

## Further reading

- [`docs/PROJECT_WALKTHROUGH.md`](docs/PROJECT_WALKTHROUGH.md) — why each major
  technical decision was made, including the ones I reversed.
- [`docs/INTERVIEW_PREPARATION.md`](docs/INTERVIEW_PREPARATION.md) — likely
  questions with answers grounded in this implementation.
- [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) — intended use, evaluation,
  fairness, risks.

## License

Code: [MIT](LICENSE). Dataset: Apache-2.0, © IBM, not redistributed here.
