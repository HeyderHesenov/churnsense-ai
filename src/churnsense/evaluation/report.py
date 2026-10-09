"""Written evaluation reports, generated from real runs.

Two reports live here and they are deliberately separate:

* ``write_comparison_report`` - model selection, scored on **validation**.
  Produced by ``make train``.
* ``write_final_report`` - the single, final scoring on the **test**
  partition, plus the threshold economics. Produced by ``make evaluate``.

Keeping them apart mirrors the protocol: nothing in the selection report has
touched test data, and the final report runs once, after selection is closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from churnsense import viz
from churnsense.config import Config, load_config
from churnsense.data.split import DataSplits
from churnsense.evaluation.metrics import (
    ClassificationMetrics,
    evaluate,
    expected_calibration_error,
    reliability_table,
    segment_performance,
)
from churnsense.evaluation.threshold import (
    DISCLAIMER,
    ThresholdScenario,
    indifference_band,
    recommend_threshold,
    sweep_thresholds,
)
from churnsense.logging_setup import configure_logging
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


#: Dimensions the final report breaks test performance down by.
SEGMENT_COLUMNS: tuple[str, ...] = ("Contract", "InternetService")


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


def write_comparison_report(
    outcome: TrainingOutcome,
    cfg: Config | None = None,
    ablation: pd.DataFrame | None = None,
    directory: Path | None = None,
) -> Path:
    """Write ``reports/model_comparison.md`` from an actual training run."""
    cfg = cfg or load_config()
    directory = Path(directory) if directory else cfg.paths.reports_dir
    selected, dummy = outcome.selected, next(r for r in outcome.results if r.key == "dummy")
    table = outcome.comparison_frame().rename(
        columns={
            "model": "Model",
            "cv_pr_auc": "CV PR-AUC",
            "cv_std": "CV std",
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

    content = f"""# Model comparison - ChurnSense AI

Generated by `make train`. Every number is measured on this run; none is copied
from a previous run or from the literature.

**All scores below are on the validation partition**
({outcome.n_validation:,} customers), never on test. The test partition is
scored exactly once, by `make evaluate`, after selection is closed.

## Results

{_markdown_table(table)}

Selection metric: **{cfg.training.scoring}** (PR-AUC), chosen because the
positive class is the minority and the retention team cares about the precision
of what it flags, not about overall correctness.

## What the table says

**Selected: {selected.display_name}.** Validation PR-AUC
{selected.validation.average_precision:.4f} against {dummy.validation.average_precision:.4f}
for the baseline - a {lift:.2f}x lift over predicting the base rate for everyone.

### Why this model and not the highest number

{outcome.selection_reason}

Selection uses the **one-standard-error rule** rather than a plain argmax.
With the candidates packed this tightly, the nominal winner is decided by
sampling noise: change the seed and the order reshuffles. Treating everything
within one standard error of the leader as tied, and breaking the tie on
simplicity, gives a choice that survives a re-run - and a simpler model is
cheaper to serve, easier to explain to a retention team, and less likely to
degrade on next quarter's data.

The baseline scores ROC-AUC {dummy.validation.roc_auc:.3f} by construction: it
assigns every customer the same probability, so it cannot rank anyone above
anyone else. It is in the table to fix the floor, and because its
*accuracy* would look respectable while being worth nothing.

Cross-validated and validation scores are reported side by side. A large gap
between them is the signal to distrust the selection; here the selected model
scores {selected.cv_score:.4f} in {cfg.training.cv_folds}-fold CV on train and
{selected.validation.average_precision:.4f} on the held-out validation set.

### Calibration

{calibration}

Calibration matters here beyond tidiness: the retention simulator multiplies a
predicted probability by a customer's value, so the number has to mean what it
says. A model that ranks perfectly but reports 0.9 for customers who churn 60%
of the time would produce confident, wrong budgets.

### The model that actually ships

The comparison table scores each *candidate* before calibration. The persisted
model is scored separately, and it is those numbers that `model_meta.json`
reports:

{_shipped_section(outcome)}

That is also why the threshold is treated as a separate business decision
rather than left at 0.5 - see `reports/final_evaluation.md`.

### Hyper-parameters selected

```
{selected.best_params or "(no grid - defaults used)"}
```

## Method

- Stratified 60/20/20 split, seed {cfg.random_seed}.
- {cfg.training.cv_folds}-fold stratified cross-validation on the training
  partition for hyper-parameter search.
- Preprocessing lives inside each `Pipeline`, so it is refitted on every CV fold -
  in the grid search and in the calibration folds alike - and can never be
  fitted on data outside the fold.
- Class imbalance handled by `class_weight`, not resampling: at roughly 1:2.8
  the imbalance is moderate, and weighting keeps the probability scale
  interpretable for the business layer.
- The artifact is fitted on the training partition only. Refitting on
  train+validation would use more data but would mean the estimator had seen
  the data its own calibration and threshold were tuned on.

## Feature decisions, measured

{_ablation_section(ablation, cfg.training.scoring)}

## What this comparison does not tell you

- Nothing here is a test-set result. Read `reports/final_evaluation.md` for that.
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
# Final evaluation: the single scoring of the test partition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinalEvaluation:
    """The outcome of the one and only test-set scoring.

    ``test_optimal_threshold`` is recorded deliberately. It is *not* used for
    anything - it exists so the report can show how far the honestly-chosen
    threshold sits from the one that would have been picked by cheating, which
    is a more informative disclosure than silence.
    """

    threshold: float
    validation_scenario: ThresholdScenario
    validation_sweep: pd.DataFrame
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


def run_final_evaluation(model, splits: DataSplits, cfg: Config | None = None) -> FinalEvaluation:
    """Choose the threshold on validation, then score test exactly once.

    The ordering of the statements below *is* the protocol: validation
    probabilities and the business assumptions fix the threshold, and only then
    is ``X_test`` touched.
    """
    cfg = cfg or load_config()

    validation_proba = model.predict_proba(splits.X_val)[:, 1]
    sweep = sweep_thresholds(
        splits.y_val, validation_proba, splits.X_val["MonthlyCharges"], cfg.business
    )
    scenario = recommend_threshold(
        splits.y_val, validation_proba, splits.X_val["MonthlyCharges"], cfg.business
    )
    threshold = scenario.threshold
    logger.info(
        "threshold %.2f chosen on validation (simulated net benefit %s%.0f)",
        threshold,
        cfg.business.currency,
        scenario.net_benefit,
    )

    # --- the test partition is read from here, and only here -----------------
    test_proba = model.predict_proba(splits.X_test)[:, 1]
    test_charges = splits.X_test["MonthlyCharges"]

    evaluation = FinalEvaluation(
        threshold=threshold,
        validation_scenario=scenario,
        validation_sweep=sweep,
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
        segments=pd.concat(
            {
                column: segment_performance(
                    splits.y_test, test_proba, splits.X_test[column], threshold
                )
                for column in SEGMENT_COLUMNS
            },
            names=["dimension"],
        )
        .reset_index(level=0)
        .reset_index(drop=True),
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


def _unscanned_note(segments: pd.DataFrame) -> str:
    """Name the segments the operating point leaves entirely unflagged."""
    unscanned = segments[segments["flagged"].eq(0) & segments["churners"].gt(0)]
    if unscanned.empty:
        return "Every segment has at least one flagged customer at this threshold."
    names = ", ".join(f"{row.segment} ({row.dimension})" for row in unscanned.itertuples())
    return (
        f"**No customer is flagged in: {names}.** Their {int(unscanned['churners'].sum()):,} "
        "churners would receive nothing from a campaign driven by this score. That is a "
        "property of one global operating point, not of the ranking - a segment with no "
        "flags is *not scanned*, which is different from *no risk*."
    )


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
            "intervention_cost": "Campaign cost",
            "retained_value": "Retained value",
            "net_benefit": "Net benefit",
        }
    )

    content = f"""# Final evaluation - ChurnSense AI

Generated by `make evaluate`. **This is the only place the test partition is
scored, and it is scored once.** The model was selected on validation
(`reports/model_comparison.md`) and the decision threshold was chosen on
validation too; neither used the numbers below.

Model: **{meta.get("display_name", meta.get("model_key", "unknown"))}**.
Test partition: **{m.n:,} customers, {m.positives:,} of them churned
({m.positives / m.n:.1%})**.

## Test-set performance at the chosen operating point

Threshold **{evaluation.threshold:.2f}**, fixed on validation before this
partition was read.

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

{
        _markdown_table(
            evaluation.segments.rename(
                columns={
                    "dimension": "Dimension",
                    "segment": "Segment",
                    "customers": "Customers",
                    "churners": "Churners",
                    "churn_rate": "Churn rate",
                    "flagged": "Flagged",
                    "precision": "Precision",
                    "recall": "Recall",
                }
            )
        )
    }

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

Calibration is reported because the economics above multiply a predicted
probability by a customer's value. A model that ranks well but is
systematically overconfident would produce a plausible-looking budget built on
inflated numbers.

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
    """`make evaluate`: score the test partition once and publish the result."""
    import argparse
    import json

    from churnsense.data.loader import features_and_target, load_clean
    from churnsense.data.split import make_splits
    from churnsense.evaluation import figures
    from churnsense.explainability.shap_explain import load_cached_importance
    from churnsense.models.train import META_FILENAME, load_artifact

    argparse.ArgumentParser(description="Final test-set evaluation.").parse_args(argv)
    configure_logging()
    cfg = load_config()
    cfg.paths.ensure()

    model, meta = load_artifact(cfg.paths.artifacts_dir)
    df, _ = load_clean(cfg)
    X, y = features_and_target(df, cfg)
    splits = make_splits(X, y, cfg)

    evaluation = run_final_evaluation(model, splits, cfg)
    write_final_report(evaluation, meta, cfg)
    evaluation.validation_sweep.to_csv(cfg.paths.reports_dir / "threshold_sweep.csv", index=False)

    out = cfg.paths.figures_dir
    figures.threshold_economics(
        evaluation.validation_sweep,
        evaluation.threshold,
        cfg.business.currency,
        out,
        band=indifference_band(evaluation.validation_sweep, cfg.business),
    )
    figures.reliability(evaluation.reliability, evaluation.test_ece, out)
    figures.precision_recall(splits.y_test, model.predict_proba(splits.X_test)[:, 1], out)
    if (importance := load_cached_importance(cfg)) is not None:
        figures.shap_importance(importance, out)
    else:
        logger.warning("no cached SHAP importance; run `make explain` for that figure")

    # Publish the validation-chosen threshold so the API, dashboard and batch
    # scoring all use the same operating point.
    meta_path = cfg.paths.artifacts_dir / META_FILENAME
    meta["threshold"] = evaluation.threshold
    meta["threshold_source"] = (
        "maximises simulated net benefit on the validation partition under the "
        "assumptions in configs/config.yaml"
    )
    meta["test_metrics"] = evaluation.test_metrics.as_dict()
    meta["test_ece"] = evaluation.test_ece
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
