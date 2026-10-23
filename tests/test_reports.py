"""Reading the tracked reports: a missing file is normal, a malformed one is an error."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from churnsense import reports
from churnsense.exceptions import DataError


def test_a_missing_report_reads_as_none(tmp_path):
    assert reports.association_ranking(tmp_path) is None
    assert reports.model_comparison(tmp_path) is None
    assert reports.final_evaluation(tmp_path, "a" * 64) is None


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


def test_a_recorded_evaluation_is_read_only_for_its_own_artifact(tmp_path):
    path = tmp_path / reports.FINAL_EVALUATION_FILE
    path.write_text(json.dumps({"model_sha256": "a" * 64, "metrics": {}}))
    assert reports.final_evaluation(tmp_path, "a" * 64)["metrics"] == {}
    assert reports.final_evaluation(tmp_path, "b" * 64) is None


@pytest.mark.parametrize("text", ['{"metrics": {}}', '{"model_sha256": "x", "ece": NaN}'])
def test_an_evaluation_record_without_an_owner_or_with_nan_is_refused(tmp_path, text):
    (tmp_path / reports.FINAL_EVALUATION_FILE).write_text(text)
    with pytest.raises(DataError):
        reports.final_evaluation(tmp_path, "x")


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
