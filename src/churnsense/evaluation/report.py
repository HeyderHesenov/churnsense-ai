"""Written evaluation reports, generated from real runs.

Two reports live here and they are deliberately separate:

* ``write_comparison_report`` - model selection (cross-validation on train),
  calibration and the operating point (validation), and the per-segment view.
  Produced by ``make train``; it never touches the test partition.
* ``write_final_report`` - the scoring of the **test** partition at the
  threshold ``make train`` recorded. Produced by ``make evaluate CONFIRM=yes``,
  the only command that reads that partition, and only when asked to.

Keeping them apart mirrors the protocol: development reruns the first as often
as it likes, while every run of the second adds to the test partition's
history (docs/MODEL_CARD.md, "Evaluation history").
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from churnsense import viz
from churnsense.config import Config, load_config
from churnsense.data.split import DataSplits
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
    reliability_table,
    segment_table,
)
from churnsense.evaluation.threshold import (
    DISCLAIMER,
    ThresholdScenario,
    recommend_threshold,
    scenario_at,
    sweep_thresholds,
)
from churnsense.exceptions import ChurnSenseError
from churnsense.logging_setup import configure_logging
from churnsense.models.registry import BASELINE_KEY
from churnsense.models.train import TrainingOutcome

logger = logging.getLogger(__name__)


def _markdown_table(frame: pd.DataFrame, floats: str = "{:.4f}") -> str:
    """Render a frame as a GitHub markdown table."""
    formatted = frame.copy()
    for column in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[column]):
            # NaN means "undefined here" (e.g. precision with nothing flagged).
            formatted[column] = formatted[column].map(
                lambda v: "—" if pd.isna(v) else floats.format(v)
            )
        elif pd.api.types.is_bool_dtype(formatted[column]):
            formatted[column] = formatted[column].map({True: "**yes**", False: ""})
    header = "| " + " | ".join(str(c) for c in formatted.columns) + " |"
    rule = "|" + "|".join("---" for _ in formatted.columns) + "|"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in formatted.to_numpy()]
    return "\n".join([header, rule, *rows])


#: Dimensions the final report breaks test performance down by (model inputs,
#: so they are present in ``X_test``).
SEGMENT_COLUMNS: tuple[str, ...] = ("Contract", "InternetService", "SeniorCitizen")


def _with_interval(value: float, low: float, high: float) -> str:
    return "—" if pd.isna(value) else f"{value:.3f} [{low:.2f}, {high:.2f}]"


def segment_markdown(segments: pd.DataFrame) -> str:
    """The per-segment table, with each rate's 95 % Wilson interval beside it."""
    table = pd.DataFrame(
        {
            "Dimension": segments["dimension"],
            "Segment": segments["segment"],
            "Customers": segments["customers"].map("{:,}".format),
            "Churners": segments["churners"].map("{:,}".format),
            "Churn rate": segments["churn_rate"].map("{:.1%}".format),
            "Flagged": segments["flagged"].map("{:,}".format),
            "Flag rate": segments["flagged_rate"].map("{:.1%}".format),
            "Precision (95% CI)": [
                _with_interval(*row)
                for row in segments[["precision", "precision_low", "precision_high"]].to_numpy()
            ],
            "Recall (95% CI)": [
                _with_interval(*row)
                for row in segments[["recall", "recall_low", "recall_high"]].to_numpy()
            ],
            "ROC-AUC within": segments["roc_auc"].map(lambda v: "—" if pd.isna(v) else f"{v:.3f}"),
        }
    )
    return _markdown_table(table)


def _unscanned_note(segments: pd.DataFrame) -> str:
    """Name the segments the operating point leaves entirely unflagged."""
    unscanned = segments[segments["flagged"].eq(0) & segments["churners"].gt(0)]
    if unscanned.empty:
        return "Every segment has at least one flagged customer at this threshold."
    names = ", ".join(f"{row.segment} ({row.dimension})" for row in unscanned.itertuples())
    return (
        f"**No customer is flagged in: {names}.** Their {int(unscanned['churners'].sum()):,} "
        "churners would receive nothing from a campaign driven by this score. That is a "
        "property of one global operating point - a segment with no flags is *not "
        "scanned*, which is different from *no risk*. The within-segment ROC-AUC says "
        "whether the model could still rank those customers if the business chose to "
        "reach them."
    )


def _ablation_section(ablation: pd.DataFrame | None, scoring: str) -> str:
    if ablation is None:
        return "Not computed in this run (`make train --no-report` skips it)."
    table = ablation.rename(
        columns={
            "variant": "Feature set",
            "features": "Columns",
            "cv_score": f"CV {scoring}",
            "cv_std": "CV std",
            "change": "Change vs configured",
        }
    )
    return f"""Two columns are handled by configuration rather than left to the model:
`gender` is excluded as a protected attribute, and `TotalCharges` is kept despite
being nearly collinear with `tenure x MonthlyCharges`. Each decision is reversed
below for the selected model with its tuned hyper-parameters, cross-validated on
the training partition with the same folds as the search.

{_markdown_table(table)}

Read each change against the CV standard deviation beside it: the score varies
from fold to fold by about that much, so a smaller change is not a measurable
difference."""


def _shipped_section(outcome: TrainingOutcome) -> str:
    selected, shipped = outcome.selected.validation, outcome.shipped_validation
    if outcome.calibration_applied:
        label = f"Shipped (calibrated, {outcome.calibration_method})"
        reading = f"""Ranking barely moves (ROC-AUC {selected.roc_auc:.4f} -> {shipped.roc_auc:.4f}). The
calibrated model averages a monotone map over each cross-validation refit, so it
can reorder customers only slightly. What moves is the *operating point*:
class-weighted training inflates raw scores, so the same 0.5 cut flags
{selected.flagged:,} validation customers before calibration and {shipped.flagged:,}
after. Reporting the candidate's precision and recall for the shipped model
would therefore have described a different decision rule."""
    else:
        label = "Shipped"
        reading = """Calibration was not applied, so the shipped model is the candidate behind
the probability clamp; the two columns differ only where the clamp binds."""
    return f"""| Metric | Candidate (uncalibrated) | **{label}** |
|---|---|---|
| PR-AUC | {selected.average_precision:.4f} | **{shipped.average_precision:.4f}** |
| ROC-AUC | {selected.roc_auc:.4f} | **{shipped.roc_auc:.4f}** |
| Precision @ 0.5 | {selected.precision:.4f} | **{shipped.precision:.4f}** |
| Recall @ 0.5 | {selected.recall:.4f} | **{shipped.recall:.4f}** |
| Brier | {selected.brier:.4f} | **{shipped.brier:.4f}** |

{reading}"""


def _selection_section(outcome: TrainingOutcome, cfg: Config) -> str:
    selection, selected = outcome.selection, outcome.selected
    best_validation = max(outcome.results, key=lambda r: r.validation.average_precision)
    eligible = ", ".join(r.display_name for r in selection.eligible)
    k = selection.leader.cv_folds
    return f"""{selection.reason}

| Step | Value |
|---|---|
| Leader (best mean CV {cfg.training.scoring}) | {selection.leader.display_name}, {selection.leader.cv_score:.4f} |
| Standard error = leader's CV std / sqrt({k}) | {selection.leader.cv_std:.4f} / {k**0.5:.3f} = {selection.standard_error:.4f} |
| Cutoff = leader - one standard error | {selection.cutoff:.4f} |
| Candidates at or above the cutoff | {eligible} |
| Selected (simplest of those) | **{selected.display_name}**, {selected.cv_score - selection.cutoff:.4f} above the cutoff |

Selection uses the **one-standard-error rule** rather than a plain argmax, and
every quantity in it comes from the same {k} cross-validation folds on the
training partition: the leader, its standard error, and the scores compared
with the cutoff. With candidates this close the nominal winner is decided by
sampling noise; treating everything within one standard error of the leader as
tied, and breaking the tie on simplicity, gives a choice that survives a
re-run - and a simpler model is cheaper to serve and easier to explain.

Validation scores are evidence, not part of the rule. Here the selected model
scores {selected.validation.average_precision:.4f} validation PR-AUC; the best
validation score is {best_validation.validation.average_precision:.4f}
({best_validation.display_name}).

Two caveats travel with the rule. The folds share training rows, so the
spread of their scores understates the true uncertainty of a cross-validated
estimate (no unbiased estimator of that variance exists; Bengio and
Grandvalet, 2004); the standard error is a working yardstick, not a confidence
interval. And each candidate's CV score is the best over its own grid, which
flatters the larger grids slightly; the rule's lean toward the simpler model
works against that bias, and nested cross-validation would remove it at
roughly {k} times the compute."""


def _operating_point_section(
    outcome: TrainingOutcome, cfg: Config, sensitivity: pd.DataFrame | None
) -> str:
    point, b = outcome.operating_point, cfg.business
    best = outcome.threshold_sweep.loc[outcome.threshold_sweep["net_benefit"].idxmax()]
    currency = b.currency
    table = (
        "Not computed in this run."
        if sensitivity is None
        else _markdown_table(
            pd.DataFrame(
                {
                    "Offer success rate (assumed)": sensitivity["offer_success_rate"].map(
                        "{:.0%}".format
                    ),
                    "Threshold": sensitivity["threshold"].map("{:.2f}".format),
                    "Flagged": sensitivity["flagged"],
                    "Precision": sensitivity["precision"].map("{:.3f}".format),
                    "Recall": sensitivity["recall"].map("{:.3f}".format),
                    "Simulated net benefit": sensitivity["net_benefit"].map(
                        lambda v: viz.money(v, currency)
                    ),
                }
            )
        )
    )
    return f"""> **{DISCLAIMER}**

The threshold is chosen here, on the validation partition, with the model that
ships - so `model.joblib` and the threshold in `model_meta.json` leave
`make train` together, and no test data is involved.

| Quantity (validation, {outcome.n_validation:,} customers) | Value |
|---|---|
| **Threshold** | **{point.threshold:.2f}** |
| Customers flagged | {point.flagged:,} |
| Precision / recall | {point.precision:.3f} / {point.recall:.3f} |
| Simulated net benefit | {viz.money(point.net_benefit, currency)} |
| Best simulated net benefit on the sweep | {viz.money(float(best["net_benefit"]), currency)} at {float(best["threshold"]):.2f} |

The rule: among thresholds whose simulated net benefit is within one retention
offer's cost ({viz.money(b.retention_offer_cost, currency)}) of the best, take the
highest - the one that spends least and contacts fewest customers. The tolerance
is a money unit the simulation cannot resolve below, not a statistical test.

Assumptions (`configs/config.yaml`): offer cost
{viz.money(b.retention_offer_cost, currency)}, offer success rate
{b.offer_success_rate:.0%}, horizon {b.expected_horizon_months} months, gross
margin {b.gross_margin:.0%}. The value of a retained customer is monthly charges
x horizon x margin; retained value counts only flagged customers who really
churned, times the assumed success rate. The model estimates churn *risk*, not
what an offer would change: a high score does not mean an offer would keep that
customer.

### How the choice moves with the one input nobody can measure

{table}

Every row uses the same validation probabilities; only the assumed offer
success rate changes."""


def write_comparison_report(
    outcome: TrainingOutcome,
    cfg: Config | None = None,
    ablation: pd.DataFrame | None = None,
    segments: pd.DataFrame | None = None,
    sensitivity: pd.DataFrame | None = None,
    directory: Path | None = None,
) -> Path:
    """Write ``reports/model_comparison.md`` from an actual training run."""
    cfg = cfg or load_config()
    directory = Path(directory) if directory else cfg.paths.reports_dir
    selected = outcome.selected
    dummy = next(r for r in outcome.results if r.key == BASELINE_KEY)
    table = outcome.comparison_frame().rename(
        columns={
            "model": "Model",
            "cv_pr_auc": "CV PR-AUC",
            "cv_std": "CV std",
            "cv_se": "CV SE",
            "within_one_se": "Within 1 SE",
            "val_pr_auc": "Val PR-AUC",
            "val_roc_auc": "Val ROC-AUC",
            "val_precision": "Val precision",
            "val_recall": "Val recall",
            "val_f1": "Val F1",
            "val_brier": "Val Brier",
            "fit_seconds": "Fit (s)",
            "selected": "Selected",
        }
    )

    lift = selected.validation.average_precision / dummy.validation.average_precision
    calibration = (
        f"Applied ({outcome.calibration_method}). Expected calibration error on validation "
        f"improved from {outcome.uncalibrated_ece:.4f} to {outcome.calibrated_ece:.4f}."
        if outcome.calibration_applied
        else (
            "**Not applied.** Calibration was fitted and measured, and it did not improve "
            f"the expected calibration error ({outcome.uncalibrated_ece:.4f} uncalibrated "
            f"vs {outcome.calibrated_ece:.4f} calibrated), so the simpler uncalibrated "
            "model ships."
            if outcome.calibrated_ece is not None
            else "Disabled in configuration."
        )
    )
    threshold = outcome.operating_point.threshold
    segment_section = (
        "Not computed in this run (`make train --no-report` skips it)."
        if segments is None
        else f"""Validation partition, threshold {threshold:.2f}. Each rate carries a 95 %
Wilson interval: with a few dozen churners in a segment, the interval - not
the point estimate - is the finding. `gender` is not a model input; it is
reported because dropping a column does not show the model treats the groups
alike.

{segment_markdown(segments)}

{_unscanned_note(segments)}

This is a descriptive check of one operating point on one sample, not a
fairness certification. Per-segment thresholds would change who is contacted;
that is a business-policy decision (budget, who may be targeted, on what
grounds) and is deliberately not made by the code."""
    )

    content = f"""# Model comparison - ChurnSense AI

Generated by `make train`. Every number is measured on this run; none is copied
from a previous run or from the literature.

**No number here touches the test partition.** Cross-validated scores come from
{selected.cv_folds}-fold stratified CV on the training partition
({outcome.n_train:,} customers); validation scores from the validation partition
({outcome.n_validation:,} customers).

## Results

{_markdown_table(table)}

Selection metric: **{cfg.training.scoring}** (PR-AUC), chosen because the
positive class is the minority and the retention team cares about the precision
of what it flags, not about overall correctness. "CV SE" is the standard error
of the CV score (CV std / sqrt(folds)); "Within 1 SE" marks the candidates the
selection rule treats as tied with the leader.

## How the model was selected

{_selection_section(outcome, cfg)}

The baseline scores ROC-AUC {dummy.validation.roc_auc:.3f} by construction: it
assigns every customer the same probability, so it cannot rank anyone above
anyone else. It fixes the floor - the selected model's validation PR-AUC of
{selected.validation.average_precision:.4f} is a {lift:.2f}x lift over its
{dummy.validation.average_precision:.4f} - and if the rule ever selected it,
training would stop with an error instead of shipping it.

### Calibration

{calibration}

Calibration matters because the probability itself is what people read: the
API returns it, the dashboard shows it, and the risk bands are fixed probability
ranges. A model that ranks well but reports 0.9 for customers who churn 60% of
the time would put the wrong customers in "Critical". Calibration is accepted
on validation ECE, so the validation ECE above is the number that made the
decision and is optimistic for that reason.

### The model that actually ships

The comparison table scores each *candidate* before calibration. The persisted
model is scored separately, and it is those numbers that `model_meta.json`
reports:

{_shipped_section(outcome)}

That is also why the threshold is treated as a separate business decision
rather than left at 0.5 - see the next section.

### Hyper-parameters selected

```
{selected.best_params or "(no grid - defaults used)"}
```

## Operating point

{_operating_point_section(outcome, cfg, sensitivity)}

## Performance by segment

{segment_section}

## Method

- Stratified 60/20/20 split, seed {cfg.random_seed}.
- {selected.cv_folds}-fold stratified cross-validation on the training
  partition, shared by every candidate, for the hyper-parameter search and the
  selection rule.
- Validation decides two things: whether calibration is kept, and the
  decision threshold. Its scores are reported, not selected on.
- Preprocessing lives inside each `Pipeline`, so it is refitted on every CV fold -
  in the grid search and in the calibration folds alike - and can never be
  fitted on data outside the fold.
- Class imbalance handled by `class_weight`, not resampling: at roughly 1:2.8
  the imbalance is moderate, and weighting keeps the probability scale
  interpretable.
- The artifact is fitted on the training partition only. Refitting on
  train+validation would use more data but would mean the estimator had seen
  the data its own calibration and threshold were tuned on.

## Feature decisions, measured

{_ablation_section(ablation, cfg.training.scoring)}

## What this comparison does not tell you

- Nothing here is a test-set result. Only `make evaluate CONFIRM=yes` reads the
  test partition; its results and history are in `reports/final_evaluation.md`
  and docs/MODEL_CARD.md.
- A better PR-AUC is not automatically a better business outcome; the operating
  point matters more than the ranking metric, and it is chosen separately.
- These numbers describe one public snapshot of one operator. They do not
  transfer to another book of business without retraining and recalibration.
"""
    path = directory / "model_comparison.md"
    path.write_text(content, encoding="utf-8")
    logger.info("wrote %s", path.name)
    return path


# ---------------------------------------------------------------------------
# Final evaluation: the only code that reads the test partition
# ---------------------------------------------------------------------------

#: Printed when the final evaluation is started without the confirmation.
REFUSAL = """\
Refusing to run: this command scores the held-out TEST partition.

Every scoring is part of that partition's history (docs/MODEL_CARD.md,
"Evaluation history"), and each one weakens it as an independent estimate.
Routine development never needs it: `make dev` trains, chooses the threshold
and writes every validation report without touching test data.

Run it only for a final, recorded evaluation:

    make evaluate CONFIRM=yes
"""


@dataclass(frozen=True, slots=True)
class FinalEvaluation:
    """The outcome of one scoring of the test partition.

    ``test_optimal_threshold`` is recorded deliberately. It is *not* used for
    anything - it exists so the report can show how far the honestly-chosen
    threshold sits from the one that would have been picked by cheating, which
    is a more informative disclosure than silence.
    """

    threshold: float
    validation_scenario: ThresholdScenario
    validation_sweep: pd.DataFrame
    test_proba: np.ndarray
    test_metrics: ClassificationMetrics
    test_metrics_at_half: ClassificationMetrics
    test_scenario: ThresholdScenario
    test_optimal_threshold: float
    test_ece: float
    reliability: pd.DataFrame
    segments: pd.DataFrame

    @property
    def tuning_gain(self) -> dict[str, float]:
        """What moving off 0.5 bought, in test metrics."""
        return {
            "precision": self.test_metrics.precision - self.test_metrics_at_half.precision,
            "recall": self.test_metrics.recall - self.test_metrics_at_half.recall,
            "f1": self.test_metrics.f1 - self.test_metrics_at_half.f1,
        }


def run_final_evaluation(
    model, splits: DataSplits, threshold: float, cfg: Config | None = None
) -> FinalEvaluation:
    """Score the test partition at the threshold ``make train`` recorded.

    The threshold is an input, fixed on validation before this function runs;
    nothing here can move it. The validation sweep is recomputed only so the
    report can show the trade it was chosen from. ``X_test`` is predicted in
    one place, below, and every test figure derives from those probabilities.
    """
    cfg = cfg or load_config()

    validation_proba = model.predict_proba(splits.X_val)[:, 1]
    sweep = sweep_thresholds(
        splits.y_val, validation_proba, splits.X_val["MonthlyCharges"], cfg.business
    )

    # --- the test partition is read from here, and only here -----------------
    test_proba = model.predict_proba(splits.X_test)[:, 1]
    test_charges = splits.X_test["MonthlyCharges"]

    evaluation = FinalEvaluation(
        threshold=threshold,
        validation_scenario=scenario_at(sweep, threshold, cfg.business),
        validation_sweep=sweep,
        test_proba=test_proba,
        test_metrics=evaluate(splits.y_test, test_proba, threshold=threshold),
        test_metrics_at_half=evaluate(splits.y_test, test_proba, threshold=0.5),
        test_scenario=recommend_threshold(
            splits.y_test, test_proba, test_charges, cfg.business, thresholds=[threshold]
        ),
        test_optimal_threshold=recommend_threshold(
            splits.y_test, test_proba, test_charges, cfg.business
        ).threshold,
        test_ece=expected_calibration_error(splits.y_test, test_proba),
        reliability=reliability_table(splits.y_test, test_proba),
        segments=segment_table(
            splits.y_test, test_proba, splits.X_test, SEGMENT_COLUMNS, threshold
        ),
    )
    logger.info(
        "test: pr_auc=%.4f roc_auc=%.4f precision=%.4f recall=%.4f ece=%.4f",
        evaluation.test_metrics.average_precision,
        evaluation.test_metrics.roc_auc,
        evaluation.test_metrics.precision,
        evaluation.test_metrics.recall,
        evaluation.test_ece,
    )
    return evaluation


def write_final_report(
    evaluation: FinalEvaluation,
    meta: dict,
    cfg: Config | None = None,
    directory: Path | None = None,
) -> Path:
    """Write ``reports/final_evaluation.md`` from a real test-set scoring."""
    cfg = cfg or load_config()
    directory = Path(directory) if directory else cfg.paths.reports_dir
    directory.mkdir(parents=True, exist_ok=True)

    b, m, half = cfg.business, evaluation.test_metrics, evaluation.test_metrics_at_half
    scenario, currency = evaluation.test_scenario, cfg.business.currency
    gain = evaluation.tuning_gain

    # Positional, not `.isin([0.1, ..., 0.8])`: the sweep grid is
    # np.linspace(0, 1, 101), whose 0.7 is 0.7000000000000001, so float
    # equality silently dropped that row and the published table jumped
    # 0.60 -> 0.80.
    highlights = evaluation.validation_sweep.iloc[10:81:10][
        [
            "threshold",
            "flagged",
            "precision",
            "recall",
            "intervention_cost",
            "retained_value",
            "net_benefit",
        ]
    ].rename(
        columns={
            "threshold": "Threshold",
            "flagged": "Flagged",
            "precision": "Precision",
            "recall": "Recall",
            "intervention_cost": f"Campaign cost ({currency})",
            "retained_value": f"Retained value ({currency})",
            "net_benefit": f"Net benefit ({currency})",
        }
    )

    content = f"""# Final evaluation - ChurnSense AI

Generated by `make evaluate CONFIRM=yes`, the only command that reads the test
partition. The model was selected by cross-validation on the training
partition and its threshold fixed on validation by `make train`
(`reports/model_comparison.md`); neither used the numbers below. Every run of
this command adds to the partition's history - see docs/MODEL_CARD.md,
"Evaluation history".

Model: **{meta.get("display_name", meta.get("model_key", "unknown"))}**, trained
{str(meta.get("trained_at", "unknown"))[:10]}, artifact `model.joblib` sha256
`{meta.get("model_sha256", "not recorded")}`.
Test partition: **{m.n:,} customers, {m.positives:,} of them churned
({m.positives / m.n:.1%})**.

## Test-set performance at the chosen operating point

Threshold **{evaluation.threshold:.2f}**, recorded by `make train` on validation
before this partition was read.

| Metric | Value |
|---|---|
| Precision | {m.precision:.4f} |
| Recall | {m.recall:.4f} |
| F1 | {m.f1:.4f} |
| ROC-AUC | {m.roc_auc:.4f} |
| PR-AUC (average precision) | {m.average_precision:.4f} |
| Brier score | {m.brier:.4f} |
| Expected calibration error | {evaluation.test_ece:.4f} |
| Accuracy | {m.accuracy:.4f} |

Accuracy is last on purpose. Predicting "nobody churns" would score
{1 - m.positives / m.n:.1%} on this partition and be worth nothing.

### Confusion matrix at threshold {evaluation.threshold:.2f}

|  | Predicted stay | Predicted churn |
|---|---|---|
| **Actually stayed** | {m.tn:,} | {m.fp:,} |
| **Actually churned** | {m.fn:,} | {m.tp:,} |

{m.flagged:,} customers would be flagged for intervention. {m.fn:,} churners
would be missed.

### What tuning the threshold bought

| Metric | At 0.50 (default) | At {evaluation.threshold:.2f} (tuned) | Change |
|---|---|---|---|
| Precision | {half.precision:.4f} | {m.precision:.4f} | {gain["precision"]:+.4f} |
| Recall | {half.recall:.4f} | {m.recall:.4f} | {gain["recall"]:+.4f} |
| F1 | {half.f1:.4f} | {m.f1:.4f} | {gain["f1"]:+.4f} |
| Customers flagged | {half.flagged:,} | {m.flagged:,} | {m.flagged - half.flagged:+,} |

### Performance by segment at threshold {evaluation.threshold:.2f}

An aggregate recall can hide a segment the model never flags. Precision is
shown as "—" where nothing in the segment was flagged: undefined, not zero.
Each rate carries a 95 % Wilson interval.

{segment_markdown(evaluation.segments)}

{_unscanned_note(evaluation.segments)}

For reference only: the threshold that would have been optimal *on this test
partition* is {evaluation.test_optimal_threshold:.2f}, against the
{evaluation.threshold:.2f} chosen on validation. That number is reported and
then discarded - using it would be tuning on the held-out set. The gap is a
fair measure of how much threshold choice moves between samples.

## Threshold economics (SIMULATED)

> **{DISCLAIMER}**

Assumptions, all configurable in `configs/config.yaml` and adjustable live in
the dashboard:

| Assumption | Value |
|---|---|
| Cost of one retention offer | {viz.money(b.retention_offer_cost, currency)} |
| Probability an offer retains a would-be churner | {b.offer_success_rate:.0%} |
| Horizon over which retained revenue counts | {b.expected_horizon_months} months |
| Gross margin on revenue | {b.gross_margin:.0%} |

Value of one retained customer = monthly charges x horizon x margin.
Campaign cost = every flagged customer x offer cost. Net benefit = retained
value - campaign cost.

### Sweep on the validation partition

{_markdown_table(highlights, "{:,.2f}")}

The recommended operating point is **{evaluation.threshold:.2f}**, not 0.5. That is
the whole point of treating the threshold as a business parameter: a missed churner
costs a customer's remaining margin, while a false positive costs one offer.
Those are not symmetric, so the cut that balances them is not the cut that
balances the probability.

### Applied to the test partition

| Quantity | Value |
|---|---|
| Customers flagged | {scenario.flagged:,} |
| Of those, real churners | {scenario.true_positives:,} |
| Churners missed | {scenario.false_negatives:,} |
| Simulated campaign cost | {viz.money(scenario.intervention_cost, currency)} |
| Simulated retained value | {viz.money(scenario.retained_value, currency)} |
| **Simulated net benefit** | **{viz.money(scenario.net_benefit, currency)}** |

These are simulated figures for a {m.n:,}-customer partition of a published
sample dataset. They are not a forecast of any company's results, and the
offer-success assumption in particular is an input, not an estimate.

## Calibration on test

Expected calibration error **{evaluation.test_ece:.4f}**, Brier score
**{m.brier:.4f}**.

{
        _markdown_table(
            evaluation.reliability.rename(
                columns={
                    "mean_predicted": "Mean predicted",
                    "observed_rate": "Observed rate",
                    "count": "Customers",
                    "gap": "Gap",
                }
            ),
            "{:.4f}",
        )
    }

Calibration is reported because the probability itself is what people read -
the API and the dashboard show it, and the risk bands are fixed probability
ranges. A model that ranks well but is systematically overconfident would put
the wrong customers in the wrong band.

## Limitations

- **One split of one public dataset.** The intervals around every number here
  are wider than the decimal places suggest; a different seed moves them.
- **No causal claim.** The model finds customers who resemble past churners.
  It does not establish why they leave, and it cannot say what a retention
  offer would do to any individual.
- **The economics are assumptions.** No campaign outcomes exist in this data.
  Change `offer_success_rate` and every monetary figure changes with it.
- **Distribution shift is unaddressed.** Pricing, product mix and competitive
  conditions all move; a deployed version of this would need monitoring and
  periodic recalibration, neither of which is in scope here.
"""
    path = directory / "final_evaluation.md"
    path.write_text(content, encoding="utf-8")
    logger.info("wrote %s", path.name)
    return path


def main(argv: list[str] | None = None) -> int:
    """`make evaluate CONFIRM=yes`: score the test partition and publish the result."""
    import argparse
    import json

    from churnsense.data.loader import features_and_target, load_clean
    from churnsense.data.split import check_split, make_splits
    from churnsense.evaluation import figures
    from churnsense.models.train import META_FILENAME, load_artifact

    parser = argparse.ArgumentParser(description="Final evaluation on the held-out test partition.")
    parser.add_argument(
        "--confirm-test-read",
        action="store_true",
        help="required: confirms that this run should score the held-out test partition",
    )
    if not parser.parse_args(argv).confirm_test_read:
        print(REFUSAL, file=sys.stderr, end="")
        return 2

    configure_logging()
    cfg = load_config()
    cfg.paths.ensure()

    try:
        model, meta = load_artifact(cfg.paths.artifacts_dir)
        df, cleaning = load_clean(cfg)
        X, y = features_and_target(df, cfg)
        splits = make_splits(X, y, cfg)
        # Refuse before anything is scored if the data or the split is not the
        # one the artifact - and its threshold - were built on.
        if cleaning.source_sha256 != meta.get("data_sha256"):
            raise ChurnSenseError(
                "the dataset differs from the one the model was trained on. "
                "Retrain with `make train`."
            )
        check_split(splits, meta)
    except ChurnSenseError as exc:
        logger.error("final evaluation refused: %s", exc)
        return 1

    evaluation = run_final_evaluation(model, splits, float(meta["threshold"]), cfg)
    write_final_report(evaluation, meta, cfg)

    out = cfg.paths.figures_dir
    figures.reliability(evaluation.reliability, evaluation.test_ece, out)
    figures.precision_recall(splits.y_test, evaluation.test_proba, out)

    # Recorded beside the artifact, so the model's metadata says when it was
    # last evaluated on test and what that scoring found.
    meta_path = cfg.paths.artifacts_dir / META_FILENAME
    meta["test_metrics"] = evaluation.test_metrics.as_dict()
    meta["test_ece"] = evaluation.test_ece
    meta["test_evaluated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    m = evaluation.test_metrics
    print(
        f"Test partition ({m.n:,} customers) at threshold {evaluation.threshold:.2f}\n"
        f"  precision {m.precision:.4f}  recall {m.recall:.4f}  f1 {m.f1:.4f}\n"
        f"  roc_auc   {m.roc_auc:.4f}  pr_auc {m.average_precision:.4f}  ece {evaluation.test_ece:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
