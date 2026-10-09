# Model card — ChurnSense AI churn classifier

Version 0.1.0 · trained 2026-10-09 · scikit-learn 1.9.1 · Python 3.12.13

Written to the spirit of [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993).
Every figure here was measured by `make train` / `make evaluate` on this
repository's pinned data.

---

## Model details

| | |
|---|---|
| **Type** | scikit-learn `Pipeline` (preprocessing → logistic regression), isotonic-calibrated, behind a probability clamp |
| **Selected from** | DummyClassifier, LogisticRegression, RandomForest, HistGradientBoosting |
| **Selection rule** | One-standard-error rule on validation PR-AUC, ties broken on simplicity |
| **Hyper-parameters** | `C = 10.0`, `class_weight = "balanced"`, `max_iter = 2000` |
| **Calibration** | Isotonic, `cv = 5` on the training partition; each fold refits the whole pipeline, preprocessing included |
| **Features** | 18 input columns → 44 one-hot encoded |
| **Output** | Churn probability in `[1.2e-4, 1 − 1.2e-4]`, plus a risk band and a flag |
| **Decision threshold** | 0.36, chosen on validation under stated business assumptions |
| **Artifact** | `artifacts/model.joblib` (~9 KB) + `artifacts/model_meta.json` |
| **Licence** | MIT (code). Dataset Apache-2.0, © IBM, not redistributed |

---

## Intended use

**Intended.** Prioritising a retention team's outreach queue: ranking customers
by estimated churn risk, showing which attributes moved each estimate, and
sizing a campaign under assumptions a business owner sets.

**Intended users.** Retention analysts and their managers, with the dashboard
as the interface. Not end customers, and not an automated system acting
without review.

### Out of scope

- **Any automated adverse action.** Nothing here should deny service, change
  pricing for an individual, or alter a customer's terms without human review.
- **Causal inference.** The model identifies customers who *resemble* past
  churners. It cannot say why anyone leaves.
- **Uplift / treatment targeting.** It does not estimate whether an offer would
  change a given customer's behaviour, which is what a campaign actually needs.
  Those sets differ, and this is the model's largest conceptual limitation.
- **Individual certainty.** A probability is a population statement applied to
  an individual, not a prediction about that person's intent.
- **A different operator's book.** Trained on a published sample of one
  company. Pricing, product mix and class balance differ elsewhere; it would
  need retraining and recalibration.
- **Financial reporting.** Every monetary figure in this project is a
  simulation under assumptions, never a measured result.

---

## Training data

**IBM Telco Customer Churn**, Apache-2.0, SHA-256 `16320c9c1ec7…` verified on
every run. 7,043 customers, 21 columns, one row per customer, no time
dimension.

| Partition | Rows | Positive rate |
|---|---|---|
| Train | 4,225 | 0.2653 |
| Validation | 1,409 | 0.2654 |
| Test | 1,409 | 0.2654 |

Stratified 60/20/20, seed 42. Overall churn rate **26.54 %**.

**Preprocessing:** 11 blank `TotalCharges` values (all at `tenure == 0`) filled
with 0.00; `SeniorCitizen` treated as a category rather than a quantity;
one-hot encoding; inputs with an unknown category are rejected, not encoded;
standardisation applied only to the linear model.

**Excluded features.** `customerID` (identifier, quasi-PII) and `gender` (see
Fairness).

---

## Evaluation

The test partition was scored **once**, after model selection and threshold
tuning had both closed on validation.

### Test set (1,409 customers, 374 churned), threshold 0.36

| Metric | Value |
|---|---|
| Precision | 0.5525 |
| Recall | 0.7032 |
| F1 | 0.6188 |
| ROC-AUC | 0.8422 |
| PR-AUC | 0.6251 |
| Brier score | 0.1385 |
| Expected calibration error | 0.0275 |
| Accuracy | 0.7700 |

|  | Predicted stay | Predicted churn |
|---|---|---|
| **Actually stayed** | 822 | 213 |
| **Actually churned** | 111 | 263 |

### Baselines

| Baseline | Score |
|---|---|
| Predict "no churn" for everyone | 73.5 % accuracy, 0 recall, **worthless** |
| Predict the prior for everyone | PR-AUC 0.2654, ROC-AUC 0.500 |
| **This model** | **PR-AUC 0.6251 — a 2.4× lift** |

Accuracy is reported for completeness and is not a headline. The trivial rule
beats it on nothing that matters.

### Calibration

ECE **0.0275** on test; Brier **0.1385**. Calibration is treated as a primary
metric because the retention simulator multiplies predicted probability by
customer value — a model that ranks well but is systematically overconfident
would produce a plausible-looking budget built on inflated numbers.

Isotonic calibration saturates at its terminal bins, so probabilities are
clamped to `[ε, 1−ε]` with `ε = 1/(2 × 4,225) = 1.2e-4`. Without it the model
reported exact certainty for 214 customers. The clamp is part of the fitted
artifact rather than of any one consumer, so these metrics describe the same
function the API serves.

### Performance by segment (test partition, threshold 0.36)

Reported because an aggregate metric hides a segment the model serves badly —
and here it hides a serious one. Generated by `make evaluate`
(`reports/final_evaluation.md`), not computed by hand.

| Segment | n | Churn rate | Flagged | Precision | Recall |
|---|---|---|---|---|---|
| Month-to-month | 773 | 42.6 % | 467 | 0.555 | **0.787** |
| One year | 300 | 12.0 % | 9 | 0.444 | **0.111** |
| Two year | 336 | 2.7 % | **0** | — | **0.000** |
| Fibre optic | 613 | 41.1 % | 384 | 0.560 | 0.853 |
| DSL | 484 | 20.0 % | 92 | 0.522 | 0.495 |
| No internet | 312 | 8.0 % | **0** | — | **0.000** |

> **The headline recall of 0.70 is almost entirely month-to-month recall.**
> At this threshold the model flags **no two-year-contract customer and no
> customer without internet** — not a single one. Their churners are invisible
> to any campaign driven by this score: 9 two-year churners and 25 no-internet
> churners in the test partition would receive nothing.

This is a property of the operating point, not a defect in the ranking. The
threshold was chosen to maximise simulated net benefit across the whole book,
and because low-base-rate segments contribute few true positives per offer,
the optimiser spends nothing on them. That is arguably the right commercial
answer and an unacceptable answer if the goal is equitable coverage.

**What to do about it, if deployed.** Either accept it explicitly as a
targeting decision, or run **per-segment thresholds** so each segment gets an
operating point tuned to its own base rate. The second is the better answer
and is not implemented here. Either way the team must know that a "no flags"
segment means *not scanned*, not *no risk*.

---

## Fairness

**`gender` is excluded from the model.** Bias-corrected Cramér's V against the
target measures **0.0000** on this dataset — no detectable association — and it
is a protected attribute. Excluding it costs nothing measurable — putting it
back changes cross-validated PR-AUC by −0.0010 against a fold-to-fold standard
deviation of 0.0244 (ablation in `reports/model_comparison.md`) — and removes
a category of risk.

That the two arguments agreed here was lucky. **Had gender carried real signal,
the right action would still have been to exclude it** from a model that
decides who receives a commercial offer, and to report the accuracy cost
openly rather than quietly keeping it.

**Attributes retained that warrant scrutiny.** `SeniorCitizen`, `Partner` and
`Dependents` are demographic, correlate with protected characteristics, and
**have not been audited for disparate impact**. That audit is not in this
project. Before any deployment, the questions to answer are:

- Do flagged rates differ materially across senior-citizen status, and is any
  difference justified by a genuine difference in churn behaviour?
- Does the retention offer itself differ in value across groups?
- Does excluding `SeniorCitizen` cost measurable performance? If not, exclude
  it too, as was done with gender.

**Proxy risk.** `PaymentMethod` and `InternetService` may proxy for income or
geography. The model uses them because they carry real signal, but that is a
reason for monitoring, not a reason to assume neutrality.

---

## Ethical considerations and risks

| Risk | Mitigation in this project |
|---|---|
| A score read as a statement about a person | Every surface carries an explicit caveat; the API embeds one in the response body |
| Attribution read as causation | SHAP output is labelled as model attribution; a test asserts causal phrasing never appears in narratives |
| Simulated savings quoted as results | Every monetary figure is labelled SIMULATED and its assumptions shown beside it |
| Differential treatment of similar customers | Risk bands are fixed and published; the threshold is explicit and auditable |
| A silently stale or broken model | Missing artifact → HTTP 503 and a dead-end dashboard state, never a placeholder score; a scikit-learn version mismatch logs a warning |
| Over-contacting low-value customers | The economics weight by each customer's own charges, not an average |
| **Discount leakage** — offers to people who would have stayed | Not mitigated. At threshold 0.36, **213 of 476 flagged test customers were not going to churn**. Precision 0.55 is the honest cost of recall 0.70 |

---

## Caveats and recommendations

1. **One split of one public dataset.** Confidence intervals are wider than the
   decimals suggest; a different seed moves every number.
2. **No time dimension.** Churn is labelled at a single cut. Nothing here is a
   survival or time-to-event analysis, and no trend can be read from it.
3. **The economics are assumptions.** `offer_success_rate` cannot be estimated
   from this data — it contains no campaign outcomes. Change it and every
   monetary figure changes with it.
4. **No drift monitoring.** Performance will decay. A deployed version needs
   input-distribution monitoring and rolling calibration checks.
5. **The single operating point leaves whole segments unscanned.** At 0.36 the
   model flags no two-year-contract and no no-internet customer. Per-segment
   thresholds would fix it; they are not implemented.
6. **Fitted on 60 % of the data** so the threshold and calibration stay
   honestly held out. See `docs/PROJECT_WALKTHROUGH.md` §9.
7. **Recommended cadence if deployed:** retrain quarterly or on a calibration
   alert, re-audit fairness at each retrain, and re-derive the threshold
   whenever the offer cost or success rate changes.

---

## How to reproduce

```bash
make setup && make data && make all
```

Deterministic given the pinned dataset hash, seed 42 and the locked
environment that `make setup` installs (Python 3.12, scikit-learn 1.9.1).
Other library versions may move the fourth decimal.
