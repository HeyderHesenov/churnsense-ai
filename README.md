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
| Four-model comparison, operating point and per-segment view | `make train` → [`reports/model_comparison.md`](reports/model_comparison.md) |
| Final evaluation on the held-out test partition (explicit opt-in) | `make evaluate CONFIRM=yes` → [`reports/final_evaluation.md`](reports/final_evaluation.md) |
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
          4 candidates → 1-SE rule on CV → calibration → threshold (validation)
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
- Only the final evaluation may name the test partition: a test fails if any
  other module in `src/` or `app/` reads `X_test` or `y_test`. `make dev` never
  reaches it, and `make evaluate` refuses to run without `CONFIRM=yes`.
- Preprocessing is a `Pipeline` step, so it is refitted inside every CV fold —
  in the hyper-parameter search *and* in the calibration folds — and cannot be
  fitted on data outside it. A test checks each calibration fold's scaler.
- Inputs are validated on that same single path, so an unknown category, an
  impossible number or an impossible combination (add-ons without internet) is
  rejected by the library, the dashboard and the API alike, never silently
  encoded into a confident score.
- The decision threshold is chosen on validation by `make train` and stored in
  `model_meta.json` beside the model's own SHA-256, so all three consumers read
  the same operating point for the same bytes.

## Quick start

Requires **Python 3.12+** (developed on 3.12.13; the fast test suite also passes
on 3.13.5) on **Linux or macOS on Apple silicon**, the platforms the lock is
resolved for. The floor comes from the lock: shap 0.52, scipy 1.18 and
contourpy 1.4 no longer support 3.11. If your default `python3` is older, pass
a newer one — `make setup` checks before it creates anything and tells you
which interpreters it can find:

```bash
make setup PYTHON=python3.12
```

```bash
make setup      # .venv from the hashed dev lock, requirements-dev.txt
make dev        # data → eda → train → explain; never reads the test partition
make app        # dashboard  → http://localhost:8501
make api        # API        → http://localhost:8000/docs
```

If another server already holds a port, pick a different one:
`make api API_PORT=8001`, `make app APP_PORT=8502`.

`make help` lists every target. Individual steps:

```bash
make data       # download the dataset and verify its SHA-256
make eda        # EDA on the training partition: reports/eda_report.md + figures
make train      # select (CV), calibrate, set the threshold (validation); artifacts/ + reports
make explain    # cache global SHAP importance and its figure
make test       # pytest
make lint       # ruff check + format --check
make audit      # pip-audit
make clean      # remove generated output (raw data is kept)

make evaluate CONFIRM=yes   # FINAL evaluation: scores the held-out test partition
```

**Only `make evaluate CONFIRM=yes` reads the test partition** (and `make all
CONFIRM=yes`, which is `make dev` followed by it). Without `CONFIRM=yes` it
refuses, and so does `python -m churnsense.evaluation.report` without
`--confirm-test-read`. `make dev` never scores it, and nothing else computes a
figure from it: the dashboard's performance page works on validation, and the
book views score every customer, test rows included, but show no test-row
outcome (see [Dashboard](#dashboard)). Each final evaluation adds to the partition's
history ([model card](docs/MODEL_CARD.md#evaluation-history)), so it is a
deliberate step, not part of the development loop.

The dependencies come in two hashed locks, both compiled by `make lock` from
the constraints in `pyproject.toml` (repeated in `requirements*.in`):

```bash
make setup-runtime   # .venv-runtime from requirements.txt: what the app and API need, nothing else
make sbom            # sbom.cdx.json: a CycloneDX SBOM of that runtime environment
make lock            # recompile both locks; LOCK_ARGS="--upgrade-package X" moves one pin
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
  verified every time the pipeline reads the file, not only at download, and
  recorded in `model_meta.json` from the file actually read. A changed file
  halts the pipeline rather than quietly altering documented results.
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
Everything in this section is computed on the **training partition** (4,225
customers): modelling decisions are read off it, so it does not look at the
validation or test labels any more than the models do.

**Contract type is the strongest single signal.**

| Contract | Customers | Churn rate |
|---|---|---|
| Month-to-month | 2,326 | **43.1 %** |
| One year | 865 | 10.8 % |
| Two year | 1,034 | 2.5 % |

A 17× spread — but customers who expect to stay are also the ones willing to
sign a long contract, so part of that gap is self-selection rather than an
effect of the contract. The snapshot cannot separate the two. What it does
support is *targeting*.

**Risk is front-loaded.** Churn runs at 55.7 % in the first three months and
falls to 9.6 % past four years. Median tenure is 10 months for churned
customers against 37 for retained ones.

**Price is not monotonic.** Churn peaks at **37.2 %** in the $75–95 band and
*falls* to 32.4 % above $95. A plain "higher price drives churn" story does not
fit that shape — the top band is dominated by long-tenure customers holding
several services, so price and commitment pull against each other there. It is
also a concrete argument for a model that can express non-linearity.

**`gender` carries no signal: bias-corrected Cramér's V = 0.0000.** It is
excluded from the model as a protected attribute, and the exclusion costs
nothing measurable: putting it back changes cross-validated PR-AUC by
−0.0010 against a fold-to-fold standard deviation of 0.0244 (the ablation in
[`reports/model_comparison.md`](reports/model_comparison.md)). Excluding a
column does not make the model treat the groups alike — other columns can
correlate with it — so recall and flag rate per gender are reported too. See
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
rate preserved at 0.2653 / 0.2654 / 0.2654. Hyper-parameters are searched, and
the model is selected, with 5-fold stratified CV on **train**; whether
calibration is kept and where the threshold sits are decided on
**validation**; **test** is read only by the final evaluation — and it has
been read more than once (see [Evaluation](#evaluation)).

**Imbalance** is handled by `class_weight`, not resampling. At roughly 1 : 2.8
the imbalance is moderate, weighting cannot leak synthetic rows across a fold
boundary, and it leaves the probability scale interpretable — which matters
because the business layer multiplies by it.

### Measured comparison (`make train`)

Cross-validated on train (5 folds); validation scores beside them as evidence.

| Model | CV PR-AUC | CV std | CV SE | Within 1 SE | Val PR-AUC | Val ROC-AUC | Selected |
|---|---|---|---|---|---|---|---|
| Random Forest | **0.6714** | 0.0152 | 0.0068 | yes | 0.6365 | 0.8380 | |
| Hist Gradient Boosting | 0.6705 | 0.0148 | 0.0066 | yes | 0.6416 | 0.8369 | |
| Logistic Regression | 0.6663 | 0.0244 | 0.0109 | yes | 0.6444 | 0.8373 | **yes** |
| Baseline (prior) | 0.2653 | 0.0005 | 0.0002 | | 0.2654 | 0.5000 | |

### Why logistic regression, and not "it scored highest"

The three real models sit within **0.0051** of each other in cross-validation.
The nominal winner is decided by sampling noise; "it beat logistic regression
by 0.005" is not an answer that should survive a review.

Selection therefore uses the **one-standard-error rule**, with every number
taken from the same five folds: the leader is Random Forest at **0.6714**, one
standard error of its score is 0.0152 / √5 = **0.0068**, so the cutoff is
**0.6646**. All three real models clear it — logistic regression by 0.0017 —
and the tie breaks on simplicity. That choice is cheaper to serve and far
easier to explain to a retention team. The baseline is far below the cutoff;
had it ever been selected, training would have stopped with an error rather
than ship it.

An earlier version took the leader on *validation* PR-AUC and subtracted a
standard error computed from the *cross-validation* folds — two numbers from
different samples. It was corrected on 2026-10-11; both versions select the
same model on this data, and the retrained artifact is byte-identical.

### Calibration

Isotonic calibration was fitted, **measured, and kept because it helped**:
expected calibration error on validation fell from **0.1441 → 0.0274**.
It is fitted with 5-fold cross-validation on the training partition, and each
fold refits the *whole* pipeline — preprocessing included — on its own rows.

Calibration is a headline concern here, not a tidiness exercise: the
probability itself is what people read — the API returns it, the dashboard
shows it, and the risk bands are fixed probability ranges — so the number has
to mean what it says. Calibration is accepted on validation ECE, which makes
that validation figure optimistic.

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
> ε is half of one observation in the training sample — a convention tied to the
> data rather than a round number — and sits three orders of magnitude below the
> operating threshold, so no decision depends on its exact value. Clipping is
> monotone, so ranking is untouched.
>
> The clamp lives **inside the persisted artifact**, not in one consumer, so
> the reports, SHAP and the API all describe the same function. It did not,
> once: the explainability page showed the same customer as `0.9999` in one
> place and `100.0%` in another. See
> [`docs/PROJECT_WALKTHROUGH.md`](docs/PROJECT_WALKTHROUGH.md) §5.

## Evaluation

Full report: [`reports/final_evaluation.md`](reports/final_evaluation.md),
written by `make evaluate` on 2026-10-09 for the artifact whose `model.joblib`
has SHA-256 `02b2466d…47a9` — the file the current `make train` reproduces
byte for byte, so these are the shipped model's figures.

**Test partition: 1,409 customers, 374 churned (26.5 %). Threshold 0.36, fixed
on validation before this partition was read.**

> **The test partition is a re-used holdout, not an untouched one.** It was
> scored for two versions of the model (before and after the 2026-10-09 audit,
> recall 0.7005 → 0.7032) and re-read by later full-pipeline runs that checked
> reproducibility; until 2026-10-11 the dashboard's performance page also
> re-scored it on every visit, and so did every local `make test`. Every re-run
> reproduced the same numbers, and no model, threshold or feature choice was
> made on them — but the code cannot prove a history, so it is written down:
> [model card, "Evaluation history"](docs/MODEL_CARD.md#evaluation-history).
> Read these figures as a re-checked estimate. A genuinely independent one
> needs data this project has never seen, such as a later customer snapshot.

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

The threshold is chosen by `make train`, on validation, with the model that
ships, and stored with it; the final evaluation receives it and cannot move it.
How the choice moves with the one assumption nobody can measure — the offer
success rate — is tabulated in
[`reports/model_comparison.md`](reports/model_comparison.md): from 0.88 at an
assumed 10 % to 0.22 at 50 %. A high churn risk does not mean an offer would
keep that customer; the model estimates risk, not the effect of an offer.

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
the top three contributors for over a third of customers; the mean seed-to-seed
spread is now about 0.3 percentage points against 1 point with one ordering.
The seed is fixed so a refresh never changes an explanation, and all pages
share one 100-customer training background, so "an average customer" means the
same thing everywhere.

**Measured global importance** (300 validation customers, 100-customer
training background), in percentage points of churn probability — the unit of
the per-customer waterfall:

| Feature | Mean \|SHAP\| (pp) | Share |
|---|---|---|
| Tenure (months) | 14.9 | 18.4 % |
| Monthly charges | 13.7 | 16.8 % |
| Internet service | 11.9 | 14.6 % |
| Contract type | 7.1 | 8.7 % |
| Total charges to date | 6.7 | 8.3 % |

**This ranking disagrees with the EDA's univariate ranking, and both are
published.** Contract type is the strongest single signal on its own
(Cramér's V 0.419 on the training partition, rank 1) but only fourth here, because it overlaps heavily
with tenure and its *unique* multivariate contribution is smaller. Reconciling
the two into one convenient story would have meant hiding one of them.

Per-customer explanations are rendered as a signed waterfall plus plain
sentences. Wording is a correctness requirement, not a style choice: the test
suite asserts that causal phrasing ("causes", "will prevent", "guarantees")
never appears. SHAP describes this model's output, not why customers leave,
and the permutation explainer masks features independently, so it also
evaluates combinations no real customer has (add-ons without internet); read
contributions as attributions under that approximation.

## Dashboard

```bash
make app     # http://localhost:8501
```

Eight sections: executive overview, customer explorer, churn patterns, model
performance, explainability, single-customer scoring, retention simulator,
batch scoring and export.

*Model performance* computes everything live on the **validation** partition —
the comparison chart shows the CV scores, one standard error and the cutoff the
selection rule used, and a per-segment table with 95 % intervals names any
segment the threshold leaves unscanned. The test result appears only as the
figure `make evaluate` recorded with the artifact; no page computes a
performance figure from the test partition, and none shows its outcomes. The
book views score every customer, test rows included, but the frame the pages
receive from the data layer (`app/data.py`) has no outcome column at all. The
outcomes it may show - training and validation customers only - are kept
apart and joined only where a page computes a churn rate, so a test row in
the explorer shows its score with its outcome "Withheld". Partition membership
is trusted only when the dataset's SHA-256 and the split digest both match the
ones the model recorded; otherwise every outcome is withheld and the page
says why.

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

**634 tests, all passing on Python 3.12.13** (2026-10-17). Measured, not
claimed:

```
$ make test
634 passed
```

The same 634 pass on a fresh copy of the tracked files with no dataset and no
trained artifact — the state GitHub Actions runs in. No test reads the real
dataset: the dashboard tests run the real app script on a synthetic model and
book, and fail if anything tries to load the real file.

Before the security hardening added its 143 tests, the then 241 fast tests also
passed on Python 3.13.5 (scikit-learn 1.9.1, shap 0.53.0) in a separate
environment resolved from the `pyproject.toml` constraints; CI runs 3.12 and
3.13 on every push.

The dashboard tests drive every section end to end on that synthetic book,
including the state in which no outcome may be shown. The documentation
checks run everywhere, because they compare this README with the tracked
reports rather than with a local artifact. Everything else needs **no
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
| Protocol | `train_all`'s signature cannot accept test data; no module but the final evaluation names `X_test`; `make evaluate` refuses without `CONFIRM=yes`; a changed split is refused |
| Selection | the one-SE rule on hand-built cases: clear winner, ties, the cutoff boundary, simplicity tie-break, order independence, invalid results, a baseline win stops training |
| Consistency | API, library and batch agree to 1e-12; a batch with duplicate rows returns every row at its file position |
| Input contract | unknown categories, impossible numbers, impossible combinations and unknown target labels are rejected on every path, library included |
| Honesty | served probabilities never reach exactly 0 or 1; SHAP contributions sum to the served probability and do not hinge on the seed; narratives never use causal phrasing |
| Contract drift | the API's category vocabularies are compared against the training schema |
| Arithmetic | threshold economics checked against hand-computed values on tiny inputs |
| Dashboard | all eight sections driven through `streamlit.testing.v1.AppTest`; a section that raises on load is the cheapest bug to catch and the most embarrassing to miss |
| Documentation drift | the headline metrics, the selection cutoff and the threshold in this README are parsed back out and compared to the tracked reports that produced them; no document may claim the test partition was read only once |

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs ruff,
pytest with coverage, an end-to-end training smoke test and an API smoke test
on Python 3.12 and 3.13, and a blocking `pip-audit` over the resolved
environment and both locks. A third job installs the dev lock through
`make setup`, checking every file against its hash, and runs `pip check` and the
suite against those exact pins. It then installs the runtime lock on its own,
imports every module, runs the dashboard script and starts the API on it, and
keeps that environment's CycloneDX SBOM as a build artifact. Pull requests into
`main` also run a dependency review, and only merge once every check passes.

CI needs no dataset and no network beyond the package index.

`make audit` and the lock audit reported **no known vulnerabilities** on
2026-10-11. A finding fails the required `dependency audit` check; the
exception procedure is in [SECURITY.md](SECURITY.md#dependency-audits).

### Verified in a clean checkout

The whole flow was run from a copy of exactly the 87 tracked files — no `.venv`,
no data, no artifacts, no caches — on 2026-10-09, before the security
hardening of the same day (whose test counts are the ones under
[Testing](#testing)). That run predates `make dev`: its `make all` ended with
`make evaluate`, so it is one of the test-partition re-reads recorded in the
model card's evaluation history.

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
split, same selection, same threshold 0.36, same test metrics. Fit times have
since left the tracked reports; `make train` still logs them.

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
tests/                       634 tests + seeded synthetic fixtures
docs/                        walkthrough, interview prep, model card
reports/                     generated: EDA, model comparison, final evaluation
artifacts/                   generated: model.joblib, model_meta.json (gitignored)
```

## Security

Found a vulnerability? Please report it privately, as described in
[SECURITY.md](SECURITY.md), not in a public issue.

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
  first: size; then, while the file is read once with the linear `csv`
  module, the column count (`api.max_upload_columns`), row widths and row count
  (reading stops one row past the limit); then required columns, numeric
  ranges, category values, combinations no real customer has (add-ons
  without internet, multiple lines without a phone line, charges without
  tenure) and target labels. pandas never parses an
  uploaded header: its header handling is quadratic in the column count, and
  a 4.7 MB header of 560,000 empty columns held a CPU for more than five
  minutes (now refused in well under a second). A row wider than the header
  is refused wherever it is — pandas would have shifted every value in the
  file one column along without a word when that row came first — and
  columns outside the data contract are never built. An unknown category is
  **rejected**, not bucketed — a typo must not become a confident prediction
  — and so is a missing or non-numeric number or a fractional `tenure`: the
  pipeline's median imputer would otherwise have filled the gap and scored
  the guess (`tenure = "abc"` came back as a 7% churn risk). The one blank
  accepted is `TotalCharges` at `tenure == 0`, which is 0.00, not an
  estimate. The same checks run inside `Predictor`, so no consumer can
  bypass them.
  Error messages quote at most five offending values, each cut to 40
  characters, and the dashboard shows them as literal text, never as Markdown.
- **Exports cannot carry spreadsheet formulas.** The scored batch file keeps
  only `customerID`, `Churn`, the model's inputs and its outputs; any other
  uploaded column is dropped rather than echoed back. Every dashboard export
  then prefixes `'` to text — cells and column names — that starts with `=`,
  `+`, `-`, `@`, tab or carriage return, and to a trigger that follows `;` or
  a tab mid-cell, where Excel in `;`-separator locales would split the cell.
  Opened in Excel or Sheets they read as text instead of running.
- **No arbitrary deserialization.** The artifact path comes from config and is
  never caller-supplied; unpickling is equivalent to executing a file. Nothing
  is unpickled until the metadata is complete and servable and `model.joblib`
  matches the `model_sha256` it records — a check that the two files belong
  together, not of where they came from.
- **No SQL, no `eval`,** no execution of uploaded content.
- **Errors do not leak internals.** Generic messages to users, detail to logs,
  no filesystem paths in any API response. Every API error, the framework's
  404/405 included, has the documented `ErrorResponse` shape, and every
  response carries `X-Content-Type-Options: nosniff` and
  `Cache-Control: no-store`.
- **Never a fabricated prediction.** A missing or unreadable model surfaces as
  503 / a dead-end dashboard state, never as a default score.
- **Dependencies** are constrained in `pyproject.toml` and locked, version
  and sha256, in `requirements.txt` (runtime) and `requirements-dev.txt`
  (runtime plus dev and notebook tools). `make setup` installs with
  `--require-hashes`, so a package file that differs from the one locked is
  refused; the build backend is locked too, and the editable install is built
  from it without fetching anything. The runtime lock carries only what the
  app and API import (`tests/test_lock.py` keeps dev tools out of it), and CI
  publishes its CycloneDX SBOM on every run. `make audit` runs `pip-audit`;
  CI audits both the resolved environment and the locks, with a read-only
  `GITHUB_TOKEN` and actions pinned to commit SHAs. Dependabot raises alerts
  and fix pull requests for known advisories and proposes weekly version
  updates; CodeQL scans the Python code and the workflows.
- **`main` takes only checked, signed commits.** A ruleset blocks direct
  pushes, force-pushes and deletion, requires the CI checks to pass, blocks
  merging new high-severity CodeQL alerts, and requires verified signatures.
  Commits are signed with an SSH key. A pull request lands by fast-forwarding
  `main` to its checked head, because GitHub's rebase-and-merge rewrites
  commits and drops their signatures.

> **Local by design.** There is no authentication and no rate limiting, so
> both servers bind to `127.0.0.1`: the API through `make api`, the dashboard
> through `.streamlit/config.toml`, so even a bare `streamlit run` does not
> listen on the network (Streamlit's own default is every interface). Binding
> keeps other machines out but not DNS rebinding, where a web page re-resolves
> its own name to 127.0.0.1 and the browser then talks to the local server as
> that page; so both servers also answer only Host headers listed in
> `api.allowed_hosts` (`localhost`, `127.0.0.1`). The API refuses others with a
> 400, and refuses a browser request whose `Origin` is another site with a 403 —
> the Host of such a request is a genuine `localhost`, and a multipart upload
> needs no CORS preflight, so without it any page the user visits could make
> the API score files in a loop. The dashboard refuses a rebound WebSocket
> handshake through Streamlit's own `server.allowedHosts` (kept equal to
> `api.allowed_hosts` by a test) and re-checks inside every page; Streamlit's
> media and upload routes are outside that option but are keyed to a session,
> which a rebound page can no longer open. The dashboard also shows
> Streamlit's default tracebacks, which is right for a local analyst tool.
> Before exposing either beyond localhost: add the public host name to
> `api.allowed_hosts` and `server.allowedHosts`, put authentication, TLS and
> rate limiting in front of it, set `client.showErrorDetails = "none"` in
> `.streamlit/config.toml`, and consider disabling the API's `/docs` (it loads
> Swagger UI from a CDN). None of that is in scope here.

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
  **no two-year-contract customer and no customer without internet** — on
  validation (35 churners between them) and in the recorded test result alike.
  Recall is 0.80 for month-to-month customers and 0.05 for one-year ones
  (95 % interval 0.01–0.18). Within those segments the model still ranks
  customers (ROC-AUC 0.75–0.79 on validation): the global cut simply sits above
  their scores. `make train` publishes the per-segment table with intervals in
  [`reports/model_comparison.md`](reports/model_comparison.md), and the
  dashboard shows it. No flags means *not scanned*, not *no risk*. Per-segment
  thresholds are one policy option, not a fix: they would decide who receives
  an offer partly by segment membership, a differential-treatment question with
  governance and legal sides, and each would be tuned on 13 or 22 validation
  churners. They are not implemented; the trade-offs are in the
  [model card](docs/MODEL_CARD.md).
- **Group differences are measured, not certified away.** On validation the
  model catches 0.735 of churners among women and 0.658 among men (95 %
  intervals 0.67–0.79 and 0.59–0.72, overlapping), and flags senior citizens at
  twice the rate of others, in line with their churn rate (41.9 % vs 23.6 %).
  This is a descriptive check of one sample, not a fairness audit.
- **The test partition is re-used.** Its history is in the
  [model card](docs/MODEL_CARD.md#evaluation-history); an independent estimate
  needs new data.
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
