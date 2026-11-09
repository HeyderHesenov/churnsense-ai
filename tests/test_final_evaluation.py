"""Tests for the final evaluation - the only code that scores the test partition.

Synthetic data throughout: nothing here reads the real dataset.

The protocol under test:
  * the operating threshold is chosen on **validation**, by training;
  * the final evaluation receives it and cannot move it;
  * nothing about the model or the threshold is chosen using test data.
"""

from __future__ import annotations

import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.evaluation import release
from churnsense.evaluation.report import (
    FinalEvaluation,
    describe_release,
    render_final_report,
    score_held_out,
    summary,
)
from churnsense.evaluation.threshold import recommend_threshold
from churnsense.models.train import train_all

#: Invented figures as ``disclosure.release`` writes them.
_RELEASED = {
    "threshold": 0.36,
    "n": 1000,
    "positives": 300,
    "tn": 500,
    "fp": 200,
    "fn": 150,
    "tp": 150,
    "accuracy": 0.65,
    "precision": 0.4286,
    "recall": 0.5,
    "f1": 0.4615,
}


def _scored(model, splits, threshold, cfg: Config) -> FinalEvaluation:
    with release.exclusive() as hold:
        released = score_held_out(model, splits, threshold, hold)
    return describe_release(model, splits, threshold, released, cfg)


@pytest.fixture(scope="module")
def evaluated(clean_frame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    evaluation = _scored(outcome.model, splits, outcome.operating_point.threshold, cfg)
    return evaluation, outcome, splits


def test_threshold_is_chosen_on_validation_not_on_test(evaluated, cfg: Config):
    """Recomputing the recommendation from validation alone must reproduce it."""
    evaluation, outcome, splits = evaluated
    expected = recommend_threshold(
        splits.y_val,
        outcome.model.predict_proba(splits.X_val)[:, 1],
        splits.X_val["MonthlyCharges"],
        cfg.business,
    )
    assert evaluation.threshold == pytest.approx(expected.threshold)


def test_the_evaluation_scores_at_the_threshold_it_is_given(evaluated, cfg: Config):
    """The threshold is an input recorded by training; evaluation cannot move it."""
    _, outcome, splits = evaluated
    evaluation = _scored(outcome.model, splits, 0.42, cfg)
    assert evaluation.threshold == 0.42
    assert evaluation.released is None or evaluation.released["threshold"] == 0.42


def test_the_evaluation_keeps_nothing_a_writer_could_leak(evaluated):
    """Every output is written from what it holds, so it holds what may be published."""
    from dataclasses import fields

    evaluation = evaluated[0]
    assert {f.name for f in fields(evaluation)} == {
        "threshold",
        "validation_sweep",
        "released",
    }


def test_the_sweep_table_in_the_report_has_no_missing_rows(evaluated, cfg: Config):
    """Regression: float equality silently dropped the 0.70 row.

    The sweep grid is `np.linspace(0, 1, 101)`, whose 0.7 element is
    0.7000000000000001. Selecting highlight rows with
    `.isin([0.1, ..., 0.8])` therefore matched 7 of 8, and the published
    report jumped straight from 0.60 to 0.80 with nothing to indicate a row
    was missing.
    """
    from dataclasses import replace

    evaluation = replace(evaluated[0], released=_RELEASED)
    text = render_final_report(evaluation, {"display_name": "Test Model"}, cfg)

    sweep_section = text.split("### Sweep on the validation partition")[1].split("##")[0]
    thresholds = [
        line.split("|")[1].strip() for line in sweep_section.splitlines() if line.startswith("| 0.")
    ]
    assert thresholds == ["0.10", "0.20", "0.30", "0.40", "0.50", "0.60", "0.70", "0.80"], (
        f"the highlight table is missing rows: {thresholds}"
    )


def test_the_report_prints_the_released_figures_and_no_score_metric(evaluated, cfg: Config):
    from dataclasses import replace

    evaluation = replace(evaluated[0], released=_RELEASED)
    text = render_final_report(evaluation, {"display_name": "Test Model"}, cfg)
    assert "## Test-set performance" in text
    assert "| Precision | 0.4286 |" in text
    assert "| Recall | 0.5000 |" in text
    assert "SIMULAT" in text.upper()
    assert "| None |" not in text and "| nan |" not in text.lower()
    for row in ("| Brier", "| ROC-AUC", "| PR-AUC"):
        assert row not in text, row
    assert "roc_auc" not in summary(evaluation) and "pr_auc" not in summary(evaluation)


def test_a_withheld_evaluation_says_so_and_nothing_more(evaluated, cfg: Config):
    from dataclasses import replace

    evaluation = replace(evaluated[0], released=None)
    text = render_final_report(evaluation, {"display_name": "Test Model"}, cfg)
    assert "withheld" in text.lower() and "Test Model" in text
    assert "## Test-set performance" not in text
    assert "| Precision |" not in text
    assert "every figure withheld" in summary(evaluation)


# --- the note under the validation segment table ------------------------------------


def _note_table(*rows: tuple[str, int, int]):
    import pandas as pd

    return pd.DataFrame(
        [{"dimension": "Contract", "segment": s, "flagged": f, "churners": c} for s, f, c in rows]
    )


def test_the_note_names_the_unflagged_levels_and_adds_up_their_churners():
    from churnsense.evaluation.report import _unscanned_note

    note = _unscanned_note(_note_table(("A", 0, 25), ("B", 0, 9), ("C", 5, 9)))
    assert "Their 34 churners" in note
    assert "A (Contract)" in note and "C (Contract)" not in note


def test_the_note_leaves_out_a_level_nobody_in_churned():
    from churnsense.evaluation.report import _unscanned_note

    assert "Every segment" in _unscanned_note(_note_table(("A", 0, 0), ("B", 5, 9)))
