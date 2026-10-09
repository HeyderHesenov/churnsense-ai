# Project walkthrough — the reasoning behind the decisions

This document explains *why*, including the choices I reversed. A decision I
can only defend by its outcome is not a decision I understood when I made it,
so the reasoning is recorded alongside the evidence that prompted it.

---

## 1. Environment: a migration I did not plan

The project started on the machine's Python 3.10.0. It ended on 3.12.13, and
the reason is worth recording because it is the kind of thing that eats a day.

Every pydantic model raised at import:

```
TypeError: ForwardRef.__init__() got an unexpected keyword argument 'is_class'
```

pydantic 2.14 calls `ForwardRef(..., is_class=True)`. That parameter was added
to `typing.ForwardRef` in Python **3.10.1**. The machine had **3.10.0** — the
first 3.10 release, from October 2021, never patched. FastAPI could not start
at all.

**Options:** pin an older pydantic, or move the interpreter.

I moved. Pinning would have worked around a symptom of running a four-year-old
unpatched interpreter, and it would have left the next incompatibility waiting.
3.12 was already installed. The migration also paid for itself: the `shap` pin
at 0.49.1 existed *only* to keep 3.10 working, so it lifted to 0.52.0, and
scikit-learn went 1.7.2 → 1.9.1, numpy 2.2.6 → 2.3.5.

**The reassuring part:** the whole pipeline reproduced bit-identical results
across that jump — validation PR-AUC 0.6444, calibration ECE 0.1441 → 0.0288,
threshold 0.36, test ROC-AUC 0.8425. That is a stronger reproducibility signal
than any assertion I could have written.

**Lesson recorded:** the floor went to `>=3.11`, because a floor of 3.10 would
be a claim about an interpreter I know is broken for this stack. The audit in
§10 later raised it to `>=3.12`: the lock pins shap 0.52, scipy 1.18 and
contourpy 1.4, none of which installs on 3.11, so the old floor promised an
environment `make setup` could not build.

---

## 2. Why the preprocessing lives inside the Pipeline

The leakage requirement could have been met with a comment ("remember to fit
only on train"). It is met structurally instead: the `ColumnTransformer` is a
`Pipeline` step, so `cross_val_score` refits it on every fold automatically and
there is no code path that fits it on anything else. (One path did, until the
audit in §10: calibration reused the preprocessor fitted on all of train. It
now calibrates the whole pipeline, and a test checks each fold's scaler.)

The test does not read the code to confirm this. It fits a scaler on a
deliberately shifted training split — `MonthlyCharges` set to a constant 100 —
and asserts that held-out rows are transformed by *those* statistics. If the
scaler had seen the held-out rows it would have real spread and the assertion
would fail. Behaviour, not a promise.

**Same principle, applied again:** `train_all()` takes
`(X_train, y_train, X_validation, y_validation)`. The test partition is not a
parameter, so no amount of future carelessness inside that function can reach
it. A test asserts the signature contains no parameter with "test" in its name.

---

## 3. One-hot encoding for the gradient booster too

`HistGradientBoostingClassifier` supports native categorical splits and would
probably gain a little AUC from them. I did not use them.

Native categoricals mean a second column contract and a second serialization
path. The project's central claim is that the dashboard, the API and batch
scoring produce *identical* numbers; that is provable with one uniform
contract and merely hopeful with two. A fraction of a point of AUC is not worth
weakening the guarantee the whole architecture rests on.

Scaling, by contrast, is applied **only** to the linear model. Trees are
invariant to monotone rescaling, so fitting a scaler for them would add
parameters to the artifact that change no prediction — noise, and one more
thing to explain.

---

## 4. Model selection: the one-standard-error rule

The measured field:

| Model | CV PR-AUC | CV std | Val PR-AUC |
|---|---|---|---|
| Logistic Regression | 0.6663 | 0.0244 | 0.6444 |
| Hist Gradient Boosting | 0.6705 | 0.0148 | 0.6416 |
| Random Forest | 0.6714 | 0.0152 | 0.6365 |

My first implementation took the argmax of validation PR-AUC. It selected
logistic regression — the right answer, for a bad reason.

The top three are within **0.008** against a standard error of **0.0109**. The
"winner" is sampling noise. In an interview, "it beat the booster by 0.003" is
an answer that invites the follow-up I would deserve.

So selection became the **one-standard-error rule** (Breiman; Hastie et al.):
treat everything within one standard error of the leader as tied, break the tie
on simplicity. Complexity order is explicit in
`registry.COMPLEXITY_ORDER` — it encodes a judgement (fewer fitted parameters,
a readable decision function, cheaper inference), so it belongs in code where
it can be read and argued with.

The baseline gets no exemption. It is the simplest candidate, but it reaches
the tie set only if nothing else is meaningfully better — which would mean the
pipeline had failed and should not ship quietly. A test covers that.

**The same discipline reappears in threshold selection** (§6). That was not
planned; I noticed the identical mistake and fixed it the same way.

---

## 5. Calibration, and a bug it exposed

Calibration is fitted, **measured, and kept only because it helped**: ECE on
validation 0.1441 → 0.0274. The code keeps the uncalibrated model otherwise.

It is fitted with `cv=5` on the *training* data rather than `cv="prefit"` on
validation. Prefit calibration would consume the validation set, which is still
needed to judge the result and to choose the threshold.

**The bug.** `model_meta.json` reported the *candidate's* pre-calibration
metrics while `model.joblib` held the *calibrated* pipeline — one set of
numbers describing two different models. I found it reading the artifact, not
from a failing test.

It was not cosmetic:

| At threshold 0.50 | Candidate | Shipped |
|---|---|---|
| Precision | 0.5095 | **0.6723** |
| Recall | 0.7888 | **0.5321** |

`class_weight="balanced"` inflates raw scores, so an uncalibrated 0.5 flags far
more customers than a calibrated 0.5. Publishing the candidate's numbers would
have overstated recall by 48 % relative. The shipped pipeline is now scored on
its own, both sets are published side by side, and a test asserts the reported
metrics match the persisted model.

### The probability clamp, and where it belongs

**First version, and why it was wrong.**

Isotonic calibration is a step function. Its terminal bins assigned **exactly
0.000 to 210 customers and exactly 1.000 to five** (four after the §10
retrain), and the dashboard was displaying "100.0%" churn probability for a
real customer.

That is a claim no model fitted on 4,225 rows can support. A probability of
exactly 1 also has infinite log-odds, which
degenerates any expected-value or log-loss arithmetic downstream.

Probabilities are clamped to `[ε, 1−ε]` with `ε = 1/(2·n_train) = 1.2e-4`: the
finest rate the calibration sample can express, derived rather than chosen.
Clipping is monotone so ranking is untouched, and ε sits three orders of
magnitude below the operating threshold so no decision changes. The UI shows
the extremes as `>99.9%` rather than `100.0%`.

I first put the clamp in `Predictor`, the serving layer. That was the wrong
altitude and a later review caught it: the serving layer applied the clamp and
nothing else did, so the evaluation report published a Brier score and an ECE
for a distribution the system never emits, and the explainability page showed
one customer as **`0.9999` in its dropdown and `100.0%` in the KPI tile beside
it** — the certainty bug resurfacing through the one path that bypassed the fix.

The clamp is a fact about the *fitted model* (isotonic's terminal bins) with a
bound derived from the *training size*. Both belong to the artifact, so it now
wraps the whole persisted model. Every consumer gets the same function by
construction, and SHAP additivity reconciles against the **served**
probability instead of one nobody sees.

The general lesson, and the one I would give in an interview: a fix applied at
the layer where you *noticed* the problem will be bypassed by every other
layer. The clamp was noticed in the dashboard and therefore implemented in the
dashboard's dependency, which is exactly how it ended up contradicting itself
two pages later.

**The alternative I rejected** was leaving the raw value and "reporting what
the model says". But 1.0 is not what the model knows; it is an artefact of the
fitting method, and presenting an artefact as a finding is the thing this
project is trying not to do.

---

## 6. The threshold is a business parameter

0.5 is an arbitrary place to cut a probability. The right cut depends on what
an offer costs, how often it works, and what a customer is worth.

```
value of a retained customer = monthly charges × horizon × gross margin
net benefit = Σ(TP value × success rate) − (flagged × offer cost)
```

Value uses **each customer's own charges**, not an average. Averaging would
misprice a skewed book, and a test pins that.

### Why 0.36 rather than the peak at 0.30

| Threshold | Flagged | Precision | Net benefit |
|---|---|---|---|
| 0.30 | 511 | 0.548 | $26,579 |
| 0.36 | 448 | 0.580 | $26,547 |

A **$32** difference on a $26.5k figure built from an *assumed* success rate.
Taking the argmax commits the campaign to 63 extra customers at lower precision
for a difference the simulation cannot resolve.

So the indifference band is **one retention offer's cost**. That tolerance is
the business's own unit of account, not an invented epsilon: a simulation
cannot resolve a difference smaller than a single intervention, and the band
widens automatically when the offer gets more expensive. Choosing 0.36 moved
test precision 0.5296 → **0.5525**.

The dashboard shades the band, because a chosen point that is visibly not the
peak would otherwise read as a bug.

### Reporting the test-optimal threshold

The final evaluation reports what the optimal threshold *would have been* on
test, and then discards it. Using it would be tuning on held-out data.
Publishing it is a fair measure of how much threshold choice moves between
samples, and staying silent about it would be the more flattering choice.

---

## 7. Explaining the shipped model, not a convenient inner one

The selected model is wrapped in `CalibratedClassifierCV`. `shap.TreeExplainer`
on the inner estimator would be fast and would attribute a probability **nobody
is served**.

So the explainer runs model-agnostically against the artifact's own
`predict_proba` — preprocessing, calibration and clamp included — and a test
asserts that base value plus contributions reproduce the served probability.

Three details that matter:

- **Raw features.** The 18 raw columns are explained directly: categories go in
  as vocabulary codes and are decoded inside the model call, so masking swaps a
  whole value. The first version explained the 44 one-hot columns and summed
  them back per feature; that scored impossible "two contracts at once" rows
  and needed a column-ownership map to re-aggregate.
- **Enough permutations.** That first version also used a single sampled
  ordering per customer. The audit in §10 measured what that meant: changing
  nothing but the seed moved the top three drivers for over a third of
  customers. Ten orderings cut the mean seed-to-seed spread from about 1 to
  about 0.3 percentage points; doubling again bought little and doubled the
  cost (≈0.45 s per customer on the dashboard).
- **Seeded.** Permutation SHAP samples feature orderings; unseeded, a
  customer's explanation changes on every dashboard refresh while the
  prediction stays put. Seeding makes an explanation reproducible — it does
  not make it accurate, which is what the permutation budget is for.

### The disagreement I published instead of resolving

| Feature | Univariate rank (EDA) | SHAP rank |
|---|---|---|
| Contract type | **1** (Cramér's V 0.410) | 4 (8.7 %) |
| Tenure | 2 | **1** (18.4 %) |

Contract is the strongest single signal but overlaps heavily with tenure, so
its *unique* multivariate contribution is smaller. Both rankings are in the
repository. Picking whichever one told a neater story would have been easy and
wrong.

---

## 8. Charts: what looking at them found

Six defects were found by rendering figures and *looking at them*. None would
have failed a test:

1. **Outside bar labels were silently clipped.** `$227,264` rendered as
   `$227` — a hundredfold error presented as a fact. Three churn-rate charts
   lost their `%` the same way. Fixed with a shared helper that disables
   clipping *and* gives the value axis headroom; disabling clipping alone makes
   the text overlap the panel border.
2. **A two-group histogram on a bimodal distribution** was unreadable mud.
   Replaced with churn rate per price band, which answers the question directly
   and reuses the same bar form as the other charts.
3. **A dumbbell legend covered the bottom row's marks**; moving the labels to
   the top row made them collide with each other. They now sit above the
   widest-gap row.
4. **Suppressed heatmap cells were painted dark** — on a light-to-dark ramp
   that reads as *maximum* risk. Now off-ramp neutral grey.
5. **Evaluation figures were silently rendering on matplotlib's default light
   background** because the theme was only applied in the EDA module.
6. **A threshold annotation was anchored to the peak** while its marker sat at
   the chosen point. Harmless while they coincided; wrong the moment the
   indifference band separated them.

**Colour was computed, not chosen.** The categorical palette was validated
against the dark card surface: worst adjacent CVD ΔE 8.9, normal-vision ΔE
19.7, all slots ≥ 3:1 contrast. Risk bands use the reserved status palette with
the band name always printed, because warning and serious measure ΔE 13.6 apart
and colour alone cannot carry them.

---

## 9. Trade-offs I accepted

**The artifact is fitted on 60 % of the data.** Refitting on train+validation
would use a third more, but the calibration map and the threshold were tuned on
validation; an estimator that has seen the data its own operating point was
chosen on cannot honestly be called held out. With 4,225 rows for 44 encoded
features, that trade is not worth an unexplainable threshold. A more elaborate
scheme — nested CV with out-of-fold threshold selection — would recover the
data and is the right answer at larger scale. It is not worth the machinery
here.

**`gender` is excluded.** Measured Cramér's V is 0.0000, and putting it back
changes cross-validated PR-AUC by −0.0010 against a fold standard deviation of
0.0244. The fairness argument and the accuracy argument point the same way,
which is lucky; the model card says what I would have done if they had not.

**`TotalCharges` is kept** despite r = 0.9996 with `tenure × MonthlyCharges`.
It is collinearity, not leakage — all three are known at prediction time.
Dropping it costs −0.0039 CV PR-AUC, inside the fold noise. The consequence of
keeping it is unstable coefficients in the linear model, which is documented
rather than hidden by a silent drop.

**Tracebacks are visible in the dashboard.** Right for a localhost analyst
tool, wrong for anything exposed. The README says exactly what to change first.

---

## 10. An independent audit, and what it changed

A full review of the finished project, run against the real data, found
defects that every existing test had passed. Each was reproduced before it was
fixed, and each fix has a regression test that fails on the old code.

| Finding | Evidence | Fix |
|---|---|---|
| Batch scoring dropped exact-duplicate rows and renumbered the rest | a 6-row upload with one duplicate returned 5 rows; the API's `row 1` was file row 2 | scoring never deduplicates; `row` is the position in the file |
| Calibration reused the preprocessor fitted on all of train inside its CV folds | each fold's held-out rows had shaped its scaler | the whole pipeline is calibrated; a test checks every fold's scaler |
| Per-customer SHAP was one sampled ordering over one-hot columns | changing only the seed moved the top-3 drivers for 37.5 % of customers | raw features, 10 orderings (see §7) |
| Docs cited a gender / `TotalCharges` ablation that no code produced | no ablation existed anywhere | `make train` now runs it and publishes the table |
| Unknown target labels became "did not churn" | the label was encoded before it was validated | unknown labels are rejected |
| The library path skipped input validation the API enforced | a direct `predict_frame` scored a typo'd category; a plain `read_csv` frame crashed in the encoder | `Predictor` validates and coerces every input |
| A test asserted argmax selection, which the code does not implement | it passed only because the two rules agreed on the fixture | replaced with a test of the one-SE rule |
| The per-segment table was typed by hand | correct, but no command produced it | `make evaluate` generates it |
| The lock needed Python 3.12 while the project claimed 3.11 | shap 0.52, scipy 1.18, contourpy 1.4 | floor raised; `make setup` installs the lock |

The calibration and SHAP fixes changed the model, so the pipeline was re-run
and the test partition scored a second time. Both fixes were decided on
principle, before that scoring, and nothing was selected on it. The measured
effect on test was in the third decimal (recall 0.7005 → 0.7032, ECE
0.0264 → 0.0275); validation ECE improved from 0.0288 to 0.0274. Model, threshold
and every qualitative conclusion stayed the same.

---

## 11. What I would do next

In rough order of value:

1. **Nested cross-validation** for threshold selection, so the production model
   can be fitted on all non-test data without compromising the operating point.
2. **Drift monitoring** — PSI on the input distribution, rolling calibration
   error on outcomes. The model will decay as pricing and product mix move, and
   nothing here would notice.
3. **An uplift model.** This model finds customers likely to leave; the
   business actually wants customers whose behaviour an offer would *change*.
   Those are different sets, and the gap is the single largest conceptual
   limitation of the whole project. It needs experimental data this dataset
   does not contain.
4. **Survival analysis** for *when*, not just *whether* — which needs event
   dates the snapshot does not have.
5. **Per-segment thresholds.** A single global cut-off maximises net benefit
   across the book and, as a side effect, flags *zero* two-year-contract
   customers — the optimiser spends nothing where the base rate is low. That
   may be the right commercial answer, but it should be a stated decision
   rather than an emergent one.
6. **Fairness auditing** across the attributes retained, not just the one
   dropped.
