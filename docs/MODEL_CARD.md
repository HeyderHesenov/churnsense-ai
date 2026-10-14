# Model card — ChurnSense AI churn classifier

Version 0.1.0 · artifact `model.joblib` SHA-256 `02b2466d…47a9` · first
trained 2026-10-09, reproduced byte for byte by `make train` on 2026-10-11 ·
scikit-learn 1.9.1 · Python 3.12.13

Written to the spirit of [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993).
Every figure here was measured on this repository's pinned data: training,
cross-validation and validation figures by `make train` (2026-10-11), test
figures by `make evaluate` (2026-10-09, same artifact). How often the test
partition has been read is set out under [Evaluation history](#evaluation-history).

---

## Model details

| | |
|---|---|
| **Type** | scikit-learn `Pipeline` (preprocessing → logistic regression), isotonic-calibrated, behind a probability clamp |
| **Selected from** | DummyClassifier, LogisticRegression, RandomForest, HistGradientBoosting |
| **Selection rule** | One-standard-error rule on 5-fold cross-validated PR-AUC (training partition), ties broken on simplicity; validation scores reported as evidence only |
| **Hyper-parameters** | `C = 10.0`, `class_weight = "balanced"`, `max_iter = 2000` |
| **Calibration** | Isotonic, `cv = 5` on the training partition; each fold refits the whole pipeline, preprocessing included |
| **Features** | 18 input columns → 44 one-hot encoded |
| **Output** | Churn probability in `[1.2e-4, 1 − 1.2e-4]`, plus a risk band and a flag |
| **Decision threshold** | 0.36: the highest threshold within one offer's cost of the best simulated net benefit on validation, chosen by `make train` under stated business assumptions |
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

**IBM Telco Customer Churn**, Apache-2.0, SHA-256 `16320c9c1ec7…` verified
every time the pipeline reads it and recorded in the model metadata. 7,043 customers, 21 columns, one row per customer, no time
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

Recorded by `make evaluate` on 2026-10-09 for the artifact this card describes,
at the threshold fixed beforehand on validation. The partition is a re-used
holdout, not an untouched one: see [Evaluation history](#evaluation-history).

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
metric because the probability itself is what people read: the API returns it,
the dashboard shows it, and the risk bands are fixed probability ranges. A
model that ranks well but is systematically overconfident would put the wrong
customers in "Critical". (The threshold economics use observed outcomes on
validation, so they depend on where the cut falls, not on the probability
scale.)

Isotonic calibration saturates at its terminal bins, so probabilities are
clamped to `[ε, 1−ε]` with `ε = 1/(2 × 4,225) = 1.2e-4`. Without it the model
reported exact certainty for 214 customers. The clamp is part of the fitted
artifact rather than of any one consumer, so these metrics describe the same
function the API serves.

### Performance by segment (test partition, threshold 0.36 — recorded 2026-10-09)

Reported because an aggregate metric hides a segment the model serves badly —
and here it hides a serious one. Generated by `make evaluate`
(`reports/final_evaluation.md`), not computed by hand. The current analysis,
with uncertainty intervals and more groups, is on validation (next table).

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

### Performance by segment (validation partition, threshold 0.36)

From `make train` (`reports/model_comparison.md`), with 95 % Wilson intervals.
`gender` is not a model input; it is reported because excluding a column does
not show the groups are treated alike.

| Segment | n | Churn rate | Flag rate | Recall (95 % CI) | Precision (95 % CI) | ROC-AUC within |
|---|---|---|---|---|---|---|
| Month-to-month | 776 | 41.8 % | 56.7 % | 0.796 (0.75–0.84) | 0.586 (0.54–0.63) | 0.765 |
| One year | 308 | 12.0 % | 2.6 % | 0.054 (0.01–0.18) | 0.250 (0.07–0.59) | 0.670 |
| Two year | 325 | 4.0 % | **0.0 %** | 0.000 (0.00–0.23) | — | 0.785 |
| Fibre optic | 640 | 41.4 % | 57.2 % | 0.834 (0.78–0.87) | 0.604 (0.55–0.65) | 0.797 |
| DSL | 463 | 18.8 % | 17.7 % | 0.448 (0.35–0.55) | 0.476 (0.37–0.58) | 0.793 |
| No internet | 306 | 7.2 % | **0.0 %** | 0.000 (0.00–0.15) | — | 0.753 |
| Not senior | 1,180 | 23.6 % | 26.8 % | 0.655 (0.60–0.71) | 0.576 (0.52–0.63) | 0.840 |
| Senior citizen | 229 | 41.9 % | 57.6 % | 0.812 (0.72–0.88) | 0.591 (0.51–0.67) | 0.788 |
| Female | 715 | 25.3 % | 32.4 % | 0.735 (0.67–0.79) | 0.573 (0.51–0.64) | 0.856 |
| Male | 694 | 27.8 % | 31.1 % | 0.658 (0.59–0.72) | 0.588 (0.52–0.65) | 0.819 |

The pattern repeats on validation, where 35 churners sit in the two
unflagged segments. Within those segments the model still ranks customers
(ROC-AUC 0.785 and 0.753): what leaves them unscanned is the operating point,
not an inability to rank them. The threshold is the highest one within one
offer's cost of the best simulated net benefit across the whole book, and
because low-base-rate segments yield few true positives per offer, that rule
spends nothing on them. It follows the simulated economics, and it gives those
segments no coverage at all; whether that is acceptable is a policy question,
not a statistical one. With 13 and 22 churners the intervals are wide, and a
segment's ROC-AUC on that few positives is a rough indication, not a
measurement to act on alone.

**A segment with no flags is *not scanned*, not *no risk*.** Its churners are
still there; the global cut sits above their scores.

**Per-segment thresholds are one policy option, not a fix.** They would decide
who receives a commercial offer partly by segment membership, which is
differential treatment and raises governance, business and legal questions;
and each would be tuned on its own segment's churners — 13 and 22 here — so it
would be estimated far less precisely than the global threshold. Adopting them,
or accepting the current coverage explicitly, is a decision for the business
owner, and it needs enough subgroup data, an uncertainty analysis and an
assessment of who would gain or lose an offer. Descriptive metrics on one
validation sample cannot establish fairness or legal compliance. The
application does not implement per-segment thresholds.

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

Excluding the column removes the one direct route for gender into the score,
not every route: other inputs can correlate with it. So the model's behaviour
per gender is measured on validation (table above): flag rates are close
(32.4 % vs 31.1 %), recall is 0.735 for women and 0.658 for men, with 95 %
intervals that overlap (0.67–0.79 and 0.59–0.72). One sample of 1,409 cannot
show whether that gap is real; it is reported, not explained away.

**Attributes retained that warrant scrutiny.** `SeniorCitizen`, `Partner` and
`Dependents` are demographic and correlate with protected characteristics. A
descriptive check exists for `SeniorCitizen`: senior citizens are flagged at
57.6 % against 26.8 %, in line with their observed churn rate (41.9 % vs
23.6 %), and precision is the same in both groups (0.59 vs 0.58). A difference
in flag rate that tracks a difference in churn is not by itself evidence of
unfair treatment, but neither does it settle the question. **A disparate-impact
audit is not in this project.** Before any deployment, the questions to answer
are:

- Is a flag-rate difference that follows churn behaviour acceptable for this
  offer, given who receives it and what it is worth?
- Does the retention offer itself differ in value across groups?
- Does excluding `SeniorCitizen` cost measurable performance? If not, exclude
  it too, as was done with gender.

These are descriptive statistics from one validation sample. They are not a
fairness certification, and nothing here claims legal compliance.

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
   model flags no two-year-contract and no no-internet customer, which is not
   the same as those customers carrying no risk. Per-segment thresholds are one
   policy option, with the trade-offs set out under *Performance by segment*;
   they are not implemented.
6. **Fitted on 60 % of the data** so the threshold and calibration stay
   honestly held out. See `docs/PROJECT_WALKTHROUGH.md` §9.
7. **The test partition is a re-used holdout.** Its figures are re-checked, not
   pristine; see [Evaluation history](#evaluation-history).
8. **Recommended cadence if deployed:** retrain quarterly or on a calibration
   alert, re-audit fairness at each retrain, and re-derive the threshold
   whenever the offer cost or success rate changes.

---

## Evaluation history

**The intended protocol.** The training partition fits every model and, through
5-fold cross-validation, tunes and selects one. The validation partition decides
whether calibration is kept and where the threshold sits. The test partition is
read only after both are fixed, by the final evaluation.

**What actually happened.** The protocol held in one respect throughout: no
model, feature, hyper-parameter or threshold was ever chosen on a test number.
It did not hold in another: the partition was read many times, and it can no
longer be called untouched. Reconstructed from the commit messages and pull
requests, because no code can prove a history:

| When | What read the test partition | Effect |
|---|---|---|
| 2026-10-09, commit `0d75542` | First final evaluation (first model version) | First published test figures; the commit, and later the docs, also quoted test precision at threshold 0.30, which no committed command produces (removed) |
| 2026-10-09, `a413152`, `d064842` | Re-runs after the Python 3.12 migration and after moving the probability clamp into the artifact | Test metrics reported unchanged |
| 2026-10-09, `7515d62` | Re-scoring after the audit that moved calibration inside the CV folds and rebuilt SHAP — a changed model | Recall 0.7005 → 0.7032, ECE 0.0264 → 0.0275; **the figures published since** |
| 2026-10-09 to 2026-10-10 | Full-pipeline reproducibility runs including `make evaluate`: clean-clone checks (`920dca5`; the README's "verified in a clean checkout"), a control and a candidate run for each of the numpy and pandas upgrades (PRs #4, #5), and a run for the hashed-lock change (PR #6) | Identical reports every time |
| until 2026-10-11 | The dashboard's *Model performance* page scored the partition on every visit, and the dashboard tests ran that page in every local `make test` | Same model, same numbers; not recorded anywhere |
| until 2026-10-14 | The dashboard's book views showed each test row's outcome beside its score (explorer table and CSV) and counted test labels in the overview's and *Churn patterns*' churn rates | No metric was computed, but the labels were visible; no decision was taken from them |

Before `make evaluate` generated the per-segment table, it was also typed out
by hand from an ad hoc scoring.

**What it means.** Re-reading a holdout is harmful when what was read
influences what is reported; here the figures were only ever reproduced, never
improved by a re-run, and the model the dashboard showed was the one already
evaluated. Even so, a partition that has been looked at this often is a
re-checked estimate, not an independent one, and it is presented that way.

**What changed on 2026-10-11.**
- Only `make evaluate CONFIRM=yes` reads the partition; without the flag it
  refuses. `make dev` (data, EDA, training, SHAP) never reads it, and the
  threshold is now chosen by `make train`, so routine work has no reason to.
- A test fails if any module other than the final evaluation names `X_test` or
  `y_test` (the split that defines the partitions, and the dashboard's
  per-row partition labels, are the listed exceptions); the dashboard now
  computes its live figures on validation. Its book views still score every
  customer, test rows included; since 2026-10-14 they withhold the test
  partition's outcomes from every view, aggregate and export, and trust the
  partitions only for the dataset and split the model was trained on.
- The final report records the artifact's SHA-256, and evaluation refuses an
  artifact whose split or dataset no longer matches the one it was trained on.
- Reproducibility checks compare the model file's hash and the validation
  outputs instead of re-scoring test: the same bytes cannot score differently.
- The model-selection rule was corrected (see `docs/PROJECT_WALKTHROUGH.md`
  §4). It selects the same model; the retrained `model.joblib` is
  byte-identical, so the recorded test figures still describe what ships.
- The EDA now uses the training partition only; before, it read every row,
  test labels included (the decisions it informed — excluding `gender`,
  keeping `TotalCharges` — are also measured by the training-only ablation).

**An independent estimate** needs data this project has never touched — for
example a later snapshot of the same operator's customers, evaluated a single time by
`make evaluate CONFIRM=yes` with everything else frozen. The IBM file cannot
provide one any more.

## How to reproduce

```bash
make setup && make dev
```

`make dev` rebuilds everything except the final evaluation, which is a
separate, deliberate step: `make evaluate CONFIRM=yes`.

Deterministic given the pinned dataset hash, seed 42 and the locked
environment that `make setup` installs (Python 3.12, scikit-learn 1.9.1).
Other library versions may move the fourth decimal.
