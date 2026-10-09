# Interview preparation

Questions a reviewer is likely to ask about **this** project, answered from
**this** implementation. Every number is measured; the commands that produce
them are named so you can re-run anything live.

The three questions worth rehearsing most are **Q4** (why the simpler model
won), **Q8** (the calibration bug) and **Q16** (the segment the model does not
serve). They are where the reasoning is, and where a weak answer shows.

---

## Business framing

### Q1. What problem does this solve, in one sentence?

It tells a retention team **which customers to contact and where to draw the
line**, given what an offer costs and how often it works — and it shows the
reasoning behind each score so a human can overrule it.

The second half matters. A ranked list alone is not a decision; the threshold
is, and it is a commercial choice, not a modelling default.

### Q2. Who uses it, and what do they do differently?

Retention analysts, through the dashboard. Without it, campaigns are either
blanket (wasteful) or driven by intuition ("call the month-to-month people").
With it, the team gets a ranked queue, a per-customer reason, and a simulator
that answers "what if the offer only works 20 % of the time".

### Q3. How do you know it is worth anything?

Measured on the held-out test partition: PR-AUC **0.6251** against a no-skill
baseline of **0.265** — a **2.4× lift**. At the chosen threshold it catches
**70 %** of churners while flagging a third of the book.

I will not claim a dollar figure. The dataset contains no campaign outcomes,
so the offer success rate cannot be estimated from it. Every monetary number
in the project is labelled SIMULATED and its assumptions are sliders.

---

## Modelling

### Q4. Logistic regression beat gradient boosting? Really?

It did not, in any meaningful sense — and that is the interesting part.

| Model | CV PR-AUC | CV std | Val PR-AUC |
|---|---|---|---|
| Logistic Regression | 0.6663 | 0.0244 | 0.6444 |
| Hist Gradient Boosting | 0.6705 | 0.0148 | 0.6416 |
| Random Forest | 0.6714 | 0.0152 | 0.6365 |

The top three sit within **0.008** against a standard error of **0.0109**. The
ordering is sampling noise; change the seed and it reshuffles.

My first version took the argmax. It picked logistic regression — the right
answer for a bad reason. I replaced it with the **one-standard-error rule**:
everything within one standard error of the leader is tied, and the tie breaks
on simplicity. All three real models tie, so the simplest wins.

That choice survives a re-run, is cheaper to serve, and is far easier to
explain to a retention team. On tabular data with strong categorical signal, a
regularised linear model being competitive is the expected outcome, not a
surprise.

**If pushed:** "would a bigger grid change it?" Possibly, by another 0.005 —
still inside the standard error. The honest answer is that this dataset cannot
distinguish these three models, and pretending otherwise is the error.

### Q5. Why PR-AUC and not accuracy or ROC-AUC?

Predicting "nobody churns" scores **73.5 %** accuracy on the test partition and
is worth nothing. That single fact disqualifies accuracy as a headline.

ROC-AUC is better but optimistic under imbalance: it rewards ranking true
negatives correctly, and with 73 % negatives that is easy. PR-AUC asks the
question the team actually cares about — *of the customers we flag, how many
really were going to leave* — and its no-skill baseline is the prevalence
(0.265), so the lift is immediately interpretable.

I report all of them. PR-AUC is what selection optimises.

### Q6. How did you handle class imbalance?

`class_weight="balanced"`, not resampling. At roughly **1 : 2.8** the imbalance
is moderate — this is not a fraud-detection 1:1000 problem. Weighting is
simpler, cannot leak synthetic rows across a cross-validation fold boundary,
and leaves the probability scale interpretable, which matters because the
business layer multiplies by it.

SMOTE would have added a hyper-parameter, a leakage risk, and a distorted
probability scale, in exchange for nothing this dataset needs.

### Q7. Walk me through your leakage prevention.

Three mechanisms, all structural rather than procedural:

1. **Preprocessing lives inside the `Pipeline`**, so `cross_val_score` refits
   it per fold automatically. There is no code path that fits it elsewhere.
2. **`train_all()` does not accept test data.** Its signature is
   `(X_train, y_train, X_validation, y_validation)`. A test asserts no
   parameter contains "test".
3. **Model selection, calibration and threshold all happen on validation.**
   Test is read in exactly one function, `run_final_evaluation`, and only
   after the threshold is already fixed.

The leakage test does not read the code. It fits a scaler on a training split
where `MonthlyCharges` is a constant 100, then asserts held-out rows are
transformed by *those* statistics. If the scaler had seen them it would have
real spread and the assertion would fail.

### Q8. Tell me about a bug you found.

Two worth telling.

**The metadata bug.** `model_meta.json` reported the *candidate's*
pre-calibration metrics while `model.joblib` held the *calibrated* pipeline —
one set of numbers describing two different models. Found by reading the
artifact, not from a failing test.

Not cosmetic: precision 0.5095 → **0.6723**, recall 0.7888 → **0.5321**.
`class_weight="balanced"` inflates raw scores, so an uncalibrated 0.5 flags far
more customers than a calibrated 0.5. Publishing the candidate's numbers would
have overstated recall by 48 % relative. Fixed by scoring the shipped pipeline
on its own, publishing both side by side, and adding a test that asserts the
reported metrics match the persisted model.

**The certainty bug.** The dashboard displayed **"100.0 %"** churn probability
for a real customer. Isotonic calibration is a step function; its terminal bins
assigned exactly 1.000 to five customers and exactly 0.000 to 210 (four and
210 after the audit's retrain). No model
fitted on 4,225 rows can claim certainty, and a probability of exactly 1 has
infinite log-odds, which breaks any downstream expected-value arithmetic.
Probabilities are now clamped to `[ε, 1−ε]` with `ε = 1/(2·n_train) = 1.2e-4` —
the finest rate the calibration sample can express, derived rather than chosen.

### Q9. Why does calibration matter here?

Because the business layer multiplies a predicted probability by a customer's
value. A model that *ranks* perfectly but reports 0.9 for customers who churn
60 % of the time produces a confident, wrong budget.

Isotonic calibration cut expected calibration error from **0.1441 to 0.0274**
on validation, and it holds on test at **0.0275**. I fitted it, measured it,
and kept it *because* it helped — the code keeps the uncalibrated model
otherwise.

It is fitted with `cv=5` on train, not `cv="prefit"` on validation, because
prefit would consume the validation set I still need for the threshold. Each
calibration fold refits the whole pipeline — the first version reused the
preprocessor fitted on all of train, a small leak an audit caught (effect on
validation ECE: 0.0288 → 0.0274).

---

## Evaluation and decisions

### Q10. How did you pick 0.36?

By maximising simulated net benefit on validation — with a correction.

| Threshold | Flagged | Precision | Net benefit |
|---|---|---|---|
| 0.30 | 511 | 0.548 | $26,579 |
| **0.36** | **448** | **0.580** | **$26,547** |

The peak is at 0.30. It beats 0.36 by **$32** on a $26.5k figure built from an
*assumed* success rate. Taking the argmax commits the campaign to 63 extra
customers at lower precision for a difference the simulation cannot resolve.

So operating points within **one retention offer's cost** of the maximum are
treated as tied, and the tie breaks toward the fewest customers contacted. The
tolerance is the business's own unit of account — it scales automatically when
the offer gets more expensive — rather than an epsilon I invented.

It is the same discipline as the model selection, for the same reason. I made
the noise-chasing mistake once, recognised it the second time.

### Q11. False positives versus false negatives — how do you reason about it?

- A **false negative** is a customer who leaves without ever being offered
  anything. Cost: their remaining margin, hundreds of dollars.
- A **false positive** is an offer to someone who was staying. Cost: one offer,
  $50 by default — plus any discount they keep, which the simulation does not
  model.

Roughly 10:1, so recall is worth buying with precision, and the optimum sits
well below 0.5. At 0.36 on test: **263 churners caught, 111 missed, 213
unnecessary offers**.

I would not hide that third number. 213 of 476 flagged customers were going to
stay anyway — that is the honest price of 70 % recall.

### Q12. Your test and validation numbers differ. Why?

Validation PR-AUC 0.6441, test 0.6251; validation ROC-AUC 0.8375, test 0.8422.
Different directions, both small — ordinary sampling variation across two
1,409-row partitions.

What matters is that nothing was tuned on test, so the gap is an estimate of
generalisation rather than evidence of overfitting. If test had been *much*
worse I would suspect the selection; here it is not.

### Q13. Would you trust these numbers in production?

Not without more. They come from one split of one public dataset — the
confidence intervals are wider than the decimals suggest. I would want repeated
splits or nested CV before quoting a single figure, and I would want drift
monitoring before trusting it past the first quarter.

What I *do* trust is the protocol: test was touched once, nothing was tuned on
it, and the pipeline reproduces identically across two Python versions and two
scikit-learn versions.

---

## Explainability and limitations

### Q14. Your SHAP ranking contradicts your EDA ranking. Which is right?

Both, measuring different things — and both are published.

| Feature | Univariate (Cramér's V) | SHAP |
|---|---|---|
| Contract type | **1st** (0.410) | 4th (8.7 %) |
| Tenure | 2nd (0.352) | **1st** (18.4 %) |

Contract is the strongest signal *on its own*. But it overlaps heavily with
tenure — long-tenure customers are the ones on long contracts — so inside a
multivariate model its *unique* contribution is smaller.

Univariate association answers "what travels with churn"; SHAP answers "what
moves this model". Picking whichever told a neater story would have been easy
and wrong.

### Q15. Can I tell a customer their contract type is causing them to leave?

No, and the project is built to make that hard to do by accident.

SHAP values describe how a feature moved **this model's output** relative to an
average customer. They are not causal effects. The clearest counter-example is
in the data: customers with tech support churn at 15.2 % against 41.6 % without
it — but customers who *buy* support are plausibly more invested to begin with.
Giving support to a disengaged customer may change nothing.

A test asserts that the words "causes", "will prevent" and "guarantees" never
appear in a generated narrative. Wording is a correctness requirement here, not
a style preference.

### Q16. What is the biggest weakness of this model?

Two, and I would volunteer both.

**Conceptually: it is not an uplift model.** It finds customers *likely to
leave*. The business wants customers whose behaviour an offer would *change*.
Those are different sets — some high-risk customers are lost regardless, and
some low-risk ones are the ones an offer would actually move. Closing that gap
needs experimental data this dataset does not contain.

**Operationally: one global threshold leaves whole segments unscanned.**
Measured on test at 0.36:

| Segment | n | Churn rate | Flagged | Recall |
|---|---|---|---|---|
| Month-to-month | 773 | 42.6 % | 467 | 0.787 |
| One year | 300 | 12.0 % | 9 | 0.111 |
| Two year | 336 | 2.7 % | **0** | **0.000** |
| No internet | 312 | 8.0 % | **0** | **0.000** |

The headline recall of 0.70 is almost entirely month-to-month recall. The model
flags **not one** two-year-contract customer; its 9 churners are invisible to
any campaign driven by this score. That is the optimiser behaving correctly —
low base rates mean few true positives per offer — but it is an emergent
targeting decision rather than a stated one. Per-segment thresholds would fix
it, and they are not implemented.

### Q17. What would you build next?

1. **Nested CV for threshold selection**, so the production model can use all
   non-test data without compromising the operating point.
2. **Per-segment thresholds**, for the reason in Q16.
3. **Drift monitoring** — PSI on inputs, rolling calibration error on outcomes.
4. **An uplift model**, once there is experimental data.
5. **Fairness auditing** on the demographic attributes I kept, not just the one
   I dropped.

---

## Engineering

### Q18. How do you know the dashboard and the API agree?

By construction, then verified. `models/predict.py` is the **only** code that
turns features into a probability; the dashboard, the API and batch scoring all
call it. `predict_one` is implemented *in terms of* `predict_frame` rather than
beside it, because a separate single-row path is exactly how a form and a batch
job start disagreeing in the third decimal.

Verified live: the same customer scores **0.802979373568** through the library
and `POST /predict`, and on all 120 rows of the demo file the batch endpoint
matches the library to **0.00e+00**. Validation lives on that same path, so a
value the API would reject cannot be scored through the dashboard or the
library either.

### Q19. What happens if the model file is missing?

The API returns **503** with an explanation. The dashboard shows a dead-end
state with the command to run. Neither ever produces a placeholder score.

A wrong prediction served confidently is worse than an outage, because nothing
downstream can tell the difference. The dashboard deliberately stops rather
than rendering empty charts next to live-looking filters — zeros get read as
data.

### Q20. How would you deploy this?

Honestly: I would not deploy *this*, as is. What exists is a reproducible
pipeline and a working service; what is missing is everything that makes a
model safe to leave running.

The gaps, in order: drift monitoring, a retraining trigger, authentication in
front of the dashboard, structured request logging for audit, and an
experiment framework to measure whether the campaign works at all. The README
says the first thing to change before exposing it beyond localhost
(`client.showErrorDetails`).

### Q21. Why this test suite shape?

**406 tests** (384 of them needing no dataset and no network). `conftest.py` generates seeded synthetic
data reproducing the real file's *structure* — the same vocabularies, the
"No internet service" dependency, the blank-`TotalCharges`-at-zero-tenure quirk
— and none of its statistics. Every id is prefixed `DEMO-`.

**No test asserts a performance number on it.** The fixture's statistics are
invented, so a threshold on them would be theatre. What the tests defend is
behaviour: leakage, protocol, cross-consumer consistency, honesty of the
output, arithmetic checked against hand-computed values on tiny inputs.

Writing tests first caught an arithmetic error in my own expected ECE value
before it reached the code — which is the whole argument for the order.

### Q22. Something you changed your mind about?

The chart work, most visibly. I rendered the figures and *looked* at them, and
found six defects no test would have caught: bar labels silently clipped so
`$227,264` displayed as `$227`; a two-group histogram that was unreadable mud
on a bimodal distribution; suppressed heatmap cells painted dark on a
light-to-dark ramp, where dark reads as *maximum* risk.

Also the Python version. The project started on 3.10.0, and every pydantic
model raised at import because `typing.ForwardRef` gained the `is_class`
parameter in 3.10.**1**. I could have pinned an older pydantic. I moved the
interpreter instead, because pinning works around a symptom of running an
unpatched 2021 build. The migration also lifted `shap` 0.49 → 0.52, and the
pipeline reproduced bit-identical results across it — which is better evidence
of reproducibility than anything I could have asserted.

### Q23. What did reviewing your own finished project turn up?

Defects every test had passed. The worst: batch scoring dropped duplicate rows
and renumbered the rest, so the API attributed probabilities to the wrong
customers; calibration reused a preprocessor fitted on all of train inside its
own CV folds; and per-customer SHAP rested on a single sampled ordering — with
only the seed changed, the top three drivers moved for 37.5 % of customers.
The documentation also cited a gender ablation that no code had ever run.

Each was reproduced first, fixed, and pinned with a test that fails on the old
code. The leak and the SHAP fix changed the model, so the test set was scored
a second time; I disclose that, and both fixes were decided before it. Full
table in `docs/PROJECT_WALKTHROUGH.md` §10.

---

## Quick reference

| Fact | Value |
|---|---|
| Dataset | IBM Telco, 7,043 × 21, 26.54 % churn, Apache-2.0 |
| Split | Stratified 60/20/20, seed 42 |
| Selected model | Logistic Regression (`C=10`), isotonic-calibrated |
| Selection rule | One-standard-error, tie broken on simplicity |
| Threshold | 0.36, validation-chosen, one-offer indifference band |
| Test | P 0.5525 · R 0.7032 · F1 0.6188 · ROC-AUC 0.8422 · PR-AUC 0.6251 · ECE 0.0275 |
| Baseline | PR-AUC 0.2654, ROC-AUC 0.500 |
| Tests | 406 passing (384 without data), Python 3.12.13 |
