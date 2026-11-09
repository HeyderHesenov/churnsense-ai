"""Written evaluation reports, generated from real runs.

Two reports live here and they are deliberately separate:

* ``write_comparison_report`` - model selection (cross-validation on train),
  calibration and the operating point (validation), and the per-segment view.
  Produced by ``make train``; it never touches the test partition.
* ``render_final_report`` - the official release of the **test** partition, at the
  threshold ``make train`` recorded. Produced by ``make evaluate CONFIRM=yes``,
  the only command that reads that partition, and only when asked to.

Keeping them apart mirrors the protocol: development reruns the first as often
as it likes, while the second has one official answer, frozen by ``evaluation.release``.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from churnsense import viz
from churnsense.config import DEFAULT_CONFIG_PATH, Config, load_config
from churnsense.data.loader import features_and_target, load_clean
from churnsense.data.split import DataSplits, check_provenance, make_splits
from churnsense.evaluation import disclosure, release
from churnsense.evaluation.metrics import evaluate
from churnsense.evaluation.threshold import DISCLAIMER, sweep_thresholds
from churnsense.exceptions import ChurnSenseError, ReleaseRefused
from churnsense.logging_setup import configure_logging
from churnsense.models.registry import BASELINE_KEY
from churnsense.models.train import TrainingOutcome, load_artifact

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


def _with_interval(value: float, low: float, high: float) -> str:
    return "—" if pd.isna(value) else f"{value:.3f} [{low:.2f}, {high:.2f}]"


def _shown(values: pd.Series, template: str) -> list[str]:
    """``values`` written with ``template``; a figure withheld or undefined is "—"."""
    return ["—" if pd.isna(value) else template.format(value) for value in values]


def segment_markdown(segments: pd.DataFrame) -> str:
    """The per-segment table, with each rate's 95 % Wilson interval beside it."""
    table = pd.DataFrame(
        {
            "Dimension": segments["dimension"],
            "Segment": segments["segment"],
            "Customers": segments["customers"].map("{:,}".format),
            "Churners": _shown(segments["churners"].astype("Int64"), "{:,}"),
            "Churn rate": _shown(segments["churn_rate"], "{:.1%}"),
            "Flagged": segments["flagged"].map("{:,}".format),
            "Flag rate": _shown(segments["flagged_rate"], "{:.1%}"),
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
    unscanned = segments[segments["flagged"].eq(0) & segments["churners"].ne(0)]
    if unscanned.empty:
        return "Every segment has at least one flagged customer at this threshold."
    names = ", ".join(f"{row.segment} ({row.dimension})" for row in unscanned.itertuples())
    churners = f"Their {int(unscanned['churners'].sum()):,} churners"
    return (
        f"**No customer is flagged in: {names}.** {churners} "
        "would receive nothing from a campaign driven by this score. That is a "
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

This is a descriptive check of one operating point on one sample; it cannot
establish fairness or legal compliance. Segment-specific thresholds are one
policy option for an unflagged segment, not a guaranteed fix: they would decide
who receives a commercial offer partly by segment membership, which raises
differential-treatment, governance and legal questions, and each would be tuned
on that segment's few churners. Adopting them needs an explicit policy
decision, enough subgroup data, an uncertainty analysis and an assessment of
who would gain or lose an offer. The code makes no such decision: it applies
one global threshold."""
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
  test partition, and the project publishes one release of it; its history is in
  docs/MODEL_CARD.md.
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

The project publishes one release of this partition. Once this checkout's
governance/held_out_release.json records it, this command evaluates nothing else
here, and nothing in it can reopen it. The official release is made only under the
procedure in SECURITY.md; a run anywhere else is not the project's.
Routine development never needs it: `make dev` trains, chooses the threshold
and writes every validation report without touching test data.

Run it only for the final, recorded evaluation:

    make evaluate CONFIRM=yes
"""

#: The report rendered from the release. Never the historical ``final_evaluation.md``,
#: which no code writes or reads any more.
REPORT_FILENAME = "held_out_release.md"


@dataclass(frozen=True, slots=True)
class FinalEvaluation:
    """What one scoring of the test partition may publish, and nothing else.

    It holds neither the scores nor the raw metrics, so nothing rendered from it can
    reach a figure ``evaluation.disclosure`` did not release.
    """

    threshold: float
    validation_sweep: pd.DataFrame
    #: ``disclosure.release``: ``None`` when every figure is withheld.
    released: dict | None


def summary(evaluation: FinalEvaluation) -> str:
    """The evaluation in a few lines for a terminal or a log, which get pasted elsewhere."""
    m = evaluation.released
    if m is None:
        return f"Test partition at threshold {evaluation.threshold:.2f}: every figure withheld."
    return (
        f"Test partition ({m['n']:,} customers) at threshold {evaluation.threshold:.2f}\n"
        f"  precision {m['precision']:.4f}  recall {m['recall']:.4f}  f1 {m['f1']:.4f}"
    )


@dataclass(frozen=True, slots=True)
class ValidationRows:
    """The validation partition and nothing else, for what must not be able to reach the test one.

    ``make_splits`` builds all three partitions, so the test rows are in this process's memory
    from then on. This object is how the rehearsal is kept from them: it has nowhere to hold one.
    """

    X_val: pd.DataFrame
    y_val: pd.Series


def positive_scores(model, rows) -> np.ndarray:
    """Each row's probability of churn, or ``ValueError``: how every partition is scored.

    The second column is churn only if the model says its classes are 0 and 1. The message
    names what is wrong and quotes no value.
    """
    if list(model.classes_) != [0, 1]:
        raise ValueError("the model's classes are not 0 and 1")
    scores = np.asarray(model.predict_proba(rows), dtype=float)
    if scores.shape != (len(rows), 2):
        raise ValueError("the model did not give two probabilities for each row")
    if not np.isfinite(scores).all() or scores.min() < 0 or scores.max() > 1:
        raise ValueError("the model's scores are not probabilities")
    return scores[:, 1]


def score_held_out(model, splits: DataSplits, threshold: float, hold: release.Hold) -> dict | None:
    """Score the test partition: what may be published of it, or ``None``.

    Only under the release lock. ``None`` is the rule's answer, and final. Scores that are
    not probabilities, or a partition with a single outcome, raise: that is a failure and
    not an answer, so it reaches the caller and nothing is published.
    """
    if not isinstance(hold, release.Hold):
        raise ReleaseRefused("the test partition is scored only under the release lock")
    hold.require()
    # --- the test partition is read from here, and only here -----------------
    test_proba = positive_scores(model, splits.X_test)
    return disclosure.release(evaluate(splits.y_test, test_proba, threshold=threshold).as_dict())


def describe_release(
    model,
    rows: DataSplits | ValidationRows,
    threshold: float,
    released: dict | None,
    cfg: Config | None = None,
) -> FinalEvaluation:
    """A release with the validation trade it was chosen from; reads no test row.

    The threshold is an input, fixed on validation before the partition was scored;
    nothing here can move it. The validation sweep is recomputed only so the report can
    show that trade.
    """
    cfg = cfg or load_config()
    validation_proba = positive_scores(model, rows.X_val)
    sweep = sweep_thresholds(
        rows.y_val, validation_proba, rows.X_val["MonthlyCharges"], cfg.business
    )
    return FinalEvaluation(
        threshold=threshold,
        validation_sweep=sweep,
        released=released,
    )


def _withheld_report(meta: dict) -> str:
    """The whole report when the partition's own totals would name its customers."""
    return f"""# Final evaluation - ChurnSense AI

Generated by `make evaluate CONFIRM=yes` from the partition's official release
(`governance/held_out_release.json`). The partition's earlier history is in
docs/MODEL_CARD.md, "Evaluation history".

Model: **{meta.get("display_name", meta.get("model_key", "unknown"))}**, trained
{str(meta.get("trained_at", "unknown"))[:10]}, artifact `model.joblib` sha256
`{meta.get("model_sha256", "not recorded")}`.

Every figure of this evaluation is withheld under the disclosure policy
(`churnsense.evaluation.disclosure`).
"""


def render_final_report(evaluation: FinalEvaluation, meta: dict, cfg: Config | None = None) -> str:
    """The report of the release, as text. Only ``main`` writes it, and only after publishing."""
    cfg = cfg or load_config()
    m = evaluation.released
    if m is None:
        return _withheld_report(meta)

    b, currency = cfg.business, cfg.business.currency
    flagged = m["tp"] + m["fp"]

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

    return f"""# Final evaluation - ChurnSense AI

Generated by `make evaluate CONFIRM=yes` from the partition's official release
(`governance/held_out_release.json`). The model was selected by cross-validation
on the training partition and its threshold fixed on validation by `make train`
(`reports/model_comparison.md`); neither used the numbers below. The partition's
earlier history is in docs/MODEL_CARD.md, "Evaluation history".

Model: **{meta.get("display_name", meta.get("model_key", "unknown"))}**, trained
{str(meta.get("trained_at", "unknown"))[:10]}, artifact `model.joblib` sha256
`{meta.get("model_sha256", "not recorded")}`.
Test partition: **{m["n"]:,} customers, {m["positives"]:,} of them churned
({disclosure.churn_rate(m):.1%})**.

## Test-set performance at the chosen operating point

Threshold **{evaluation.threshold:.2f}**, recorded by `make train` on validation
before this partition was read.

| Metric | Value |
|---|---|
| Precision | {m["precision"]:.4f} |
| Recall | {m["recall"]:.4f} |
| F1 | {m["f1"]:.4f} |
| Accuracy | {m["accuracy"]:.4f} |

Accuracy is last on purpose. Predicting "nobody churns" would score
{disclosure.no_skill_accuracy(m):.1%} on this partition and be worth nothing.

### Confusion matrix at threshold {evaluation.threshold:.2f}

|  | Predicted stay | Predicted churn |
|---|---|---|
| **Actually stayed** | {m["tn"]:,} | {m["fp"]:,} |
| **Actually churned** | {m["fn"]:,} | {m["tp"]:,} |

{flagged:,} customers would be flagged for intervention. {m["fn"]:,} churners
would be missed.

## What this report does not publish

This partition's customers are listed with their features, flags and scores, so a
figure about any group of them is read against known people. No segment breakdown,
reliability bin, calibration error, Brier score, ROC-AUC, PR-AUC, figure at a second
cut, or simulated money is published for it, whatever its value
(`churnsense.evaluation.disclosure`). Segments, calibration and the ranking metrics
are measured on validation, in `reports/model_comparison.md`.

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

These are simulated figures on a partition of a published sample dataset. They are
not a forecast of any company's results, and the offer-success assumption in
particular is an input, not an estimate.

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


#: A table no partition produced, for the part of the rehearsal that needs a released one.
_INVENTED_CELLS = {"tn": 100, "fp": 100, "fn": 100, "tp": 100}


@contextlib.contextmanager
def _rehearsing(step: str) -> Iterator[None]:
    """A failure as its step and class: an error's message can quote the values it choked on."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - whatever it is, the run stops here
        raise ReleaseRefused(
            f"the validation rehearsal failed at {step} ({exc.__class__.__name__}): "
            "the test partition was not scored"
        ) from None


def rehearse(
    model,
    rows: ValidationRows,
    threshold: float,
    meta: dict,
    identity: dict,
    now: Callable[[], str],
    cfg: Config,
) -> None:
    """Run what a release runs, short of the test rows, before anything is reserved.

    A defect that repeats, found after the reservation, can consume the one release, so it is
    looked for while the ledger can still say ``open``. The scoring and the metrics run on
    validation rows and their results are dropped, unread and unlogged. What comes after them
    runs on one withheld evaluation and one invented table, never on validation's figures
    dressed up as the test partition's. Output goes to a temporary directory.

    This reduces the risk and does not remove it: a value only the test rows hold, or a system
    failure later on, can still leave the ledger reserved.
    """
    with _rehearsing("scoring"):
        scores = positive_scores(model, rows.X_val)
    with _rehearsing("metrics"):
        disclosure.release(evaluate(rows.y_val, scores, threshold=threshold).as_dict())
    with _rehearsing("describing"):
        withheld = describe_release(model, rows, threshold, None, cfg)
        invented = replace(
            withheld,
            released=disclosure.release(
                {"threshold": threshold, "n": 400, "positives": 200, **_INVENTED_CELLS}
            ),
        )
    with _rehearsing("summary"):
        for evaluation in (withheld, invented):
            summary(evaluation)
    with _rehearsing("rendering"):
        rendered = [render_final_report(e, meta, cfg) for e in (withheld, invented)]
    with _rehearsing("serialization"):
        release.rehearse_writes(identity, now(), invented.released, meta, "".join(rendered))


#: What a run that stopped has left behind, by the state of the ledger.
_WHAT_A_STATE_MEANS = {
    release.OPEN: "nothing was reserved or published",
    release.RESERVED: "the ledger stays reserved: readers withhold, and only this identity can retry",
    release.PUBLISHED: "the release is published and final",
    None: "the ledger cannot be read, and readers withhold",
}


def _what_the_ledger_holds() -> str:
    """Read and not assumed: a write can fail after the file was already replaced."""
    return _WHAT_A_STATE_MEANS[release.current_state()]


def main(argv: list[str] | None = None) -> int:
    """`make evaluate CONFIRM=yes`: make this checkout's release, or read it back."""
    import argparse

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
    # The default path, by name: CHURNSENSE_CONFIG can move every path and the dataset.
    cfg = load_config(DEFAULT_CONFIG_PATH)
    cfg.paths.ensure()

    try:
        release.require_source_tree()
        release.preflight_environment()
        model, meta = load_artifact(cfg.paths.artifacts_dir)
        release.preflight_artifact(meta)
        df, cleaning = load_clean(cfg)
        X, y = features_and_target(df, cfg)
        splits = make_splits(X, y, cfg)
        # Refuse before anything is scored if the data or the split is not the
        # one the artifact - and its threshold - were built on.
        check_provenance(splits, cleaning.source_sha256, meta)
        if cleaning.source_sha256 != cfg.dataset.sha256:
            raise ReleaseRefused("the dataset is not the one this project pins")
        threshold = float(meta["threshold"])
        identity = release.identity_of(cfg, meta, splits.digest(), cleaning.source_sha256)

        def now() -> str:
            return datetime.now(UTC).isoformat(timespec="seconds")

        rehearse(
            model, ValidationRows(splits.X_val, splits.y_val), threshold, meta, identity, now, cfg
        )
        ledger = release.freeze(
            identity, lambda hold: score_held_out(model, splits, threshold, hold), now
        )
    except ChurnSenseError as exc:
        logger.error("final evaluation refused: %s; %s", exc, _what_the_ledger_holds())
        return 1
    except Exception as exc:  # noqa: BLE001 - a failure of any kind leaves the ledger as it is
        # The class only: a numerical error's message can quote the values it choked on.
        logger.error(
            "final evaluation failed (%s): %s", exc.__class__.__name__, _what_the_ledger_holds()
        )
        return 1

    # Nothing about the partition is said before this point: a run that died earlier,
    # or was refused, printed and logged no figure. From here the ledger is final, so
    # nothing below scores anything or writes to it.
    try:
        evaluation = describe_release(model, splits, threshold, ledger.released, cfg)
        said = summary(evaluation)
        release.write_report(
            cfg.paths.reports_dir / REPORT_FILENAME, render_final_report(evaluation, meta, cfg)
        )
        print(said)
    except Exception as exc:  # noqa: BLE001 - the release stands whatever goes wrong here
        logger.error(
            "the release is published and unchanged, but its report could not be produced (%s). "
            "The same command renders it from the ledger "
            "without scoring the test partition again, for as long as every fingerprint is "
            "unchanged.",
            exc.__class__.__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
