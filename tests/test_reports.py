"""Reading the tracked reports: a missing file is normal, a malformed one is an error."""

from __future__ import annotations

import pandas as pd
import pytest

from churnsense import reports
from churnsense.exceptions import DataError


def test_a_missing_report_reads_as_none(tmp_path):
    assert reports.association_ranking(tmp_path) is None
    assert reports.model_comparison(tmp_path) is None


def test_the_association_ranking_is_read_strongest_first(tmp_path):
    pd.DataFrame(
        {"feature": ["a", "b"], "measure": ["Cramer's V"] * 2, "strength": [0.1, 0.4]}
    ).to_csv(tmp_path / reports.ASSOCIATION_FILE, index=False)
    assert list(reports.association_ranking(tmp_path)["feature"]) == ["b", "a"]


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"feature": ["a"], "strength": [0.1]}),  # no measure column
        pd.DataFrame({"feature": ["a"], "measure": ["V"], "strength": [1.5]}),  # not a strength
    ],
)
def test_a_malformed_ranking_is_refused(tmp_path, frame):
    frame.to_csv(tmp_path / reports.ASSOCIATION_FILE, index=False)
    with pytest.raises(DataError):
        reports.association_ranking(tmp_path)


def test_a_ranking_with_text_for_a_strength_is_refused_not_a_crash(tmp_path):
    """A ``TypeError`` from comparing text with 0 would get past the dashboard's handler as a 500."""
    pd.DataFrame({"feature": ["a"], "measure": ["V"], "strength": ["strong"]}).to_csv(
        tmp_path / reports.ASSOCIATION_FILE, index=False
    )
    with pytest.raises(DataError, match="text where a number belongs"):
        reports.association_ranking(tmp_path)


@pytest.mark.parametrize("column", ["cv_pr_auc", "cv_std", "cv_se", "val_pr_auc", "val_roc_auc"])
def test_a_comparison_with_text_for_a_score_is_refused(tmp_path, column):
    """The finite check looked at numeric columns only, so a score written as text passed."""
    row = {
        "model": ["a"],
        "cv_pr_auc": [0.6],
        "cv_std": [0.02],
        "cv_se": [0.01],
        "within_one_se": [True],
        "val_pr_auc": [0.6],
        "val_roc_auc": [0.8],
        "selected": [True],
    }
    pd.DataFrame({**row, column: ["high"]}).to_csv(tmp_path / reports.COMPARISON_FILE, index=False)
    with pytest.raises(DataError, match="text where a number belongs"):
        reports.model_comparison(tmp_path)
    pd.DataFrame(row).to_csv(tmp_path / reports.COMPARISON_FILE, index=False)
    assert len(reports.model_comparison(tmp_path)) == 1  # the undamaged control


def test_a_report_with_a_blank_number_is_refused(tmp_path):
    pd.DataFrame(
        {
            "model": ["a"],
            "cv_pr_auc": [0.6],
            "cv_std": [float("nan")],
            "cv_se": [0.01],
            "within_one_se": [True],
            "val_pr_auc": [0.6],
            "val_roc_auc": [0.8],
            "selected": [True],
        }
    ).to_csv(tmp_path / reports.COMPARISON_FILE, index=False)
    with pytest.raises(DataError):
        reports.model_comparison(tmp_path)
