"""The documented numbers must match the reports that produced them.

This project's central claim is that every figure in its documentation was
measured rather than asserted. That claim decays the moment someone retrains
or re-evaluates and the README keeps the old numbers - and nothing else in the
suite would notice.

So the headline figures are parsed back out of the prose and compared to the
tracked reports that generated them: test metrics to
``reports/final_evaluation.md``, the selection rule and the operating point to
``reports/model_comparison.md``. Both are in git, so this runs in CI too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "README.md",
    "docs/MODEL_CARD.md",
    "docs/PROJECT_WALKTHROUGH.md",
    "docs/INTERVIEW_PREPARATION.md",
)


def _table_rows(section: str) -> dict[str, str]:
    """``| label | value |`` rows of a two-column markdown table."""
    return {
        label.strip("* "): value.strip("* ")
        for label, value in re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|$", section, re.M)
    }


@pytest.fixture(scope="module")
def test_report() -> dict[str, float]:
    """The test metrics as `make evaluate` wrote them."""
    text = (ROOT / "reports" / "final_evaluation.md").read_text()
    rows = _table_rows(text.split("## Test-set performance")[1].split("###")[0])
    return {
        "precision": float(rows["Precision"]),
        "recall": float(rows["Recall"]),
        "roc_auc": float(rows["ROC-AUC"]),
        "average_precision": float(rows["PR-AUC (average precision)"]),
        "brier": float(rows["Brier score"]),
        "threshold": float(re.search(r"Threshold \*\*([0-9.]+)\*\*", text).group(1)),
    }


@pytest.fixture(scope="module")
def comparison() -> dict[str, str]:
    """The selection rule's steps and the operating point as `make train` wrote them."""
    text = (ROOT / "reports" / "model_comparison.md").read_text()
    return _table_rows(text)


def _numbers_after(text: str, label: str) -> list[float]:
    """Every number on the lines that mention ``label``."""
    return [
        float(match)
        for line in text.splitlines()
        if label.lower() in line.lower()
        for match in re.findall(r"\d+\.\d+", line)
    ]


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("ROC-AUC", "roc_auc"),
        ("PR-AUC", "average_precision"),
        ("Brier", "brier"),
    ],
)
def test_readme_quotes_the_measured_test_metrics(test_report: dict, label: str, key: str):
    readme = (ROOT / "README.md").read_text()
    assert test_report[key] in _numbers_after(readme, label), (
        f"README does not quote the test {key} in reports/final_evaluation.md "
        f"({test_report[key]:.4f})"
    )


def test_the_model_card_quotes_the_same_test_metrics(test_report: dict):
    """Two documents, one set of numbers."""
    card = (ROOT / "docs" / "MODEL_CARD.md").read_text()
    for key in ("precision", "recall", "roc_auc", "average_precision"):
        assert f"{test_report[key]:.4f}" in card, f"MODEL_CARD.md is missing test {key}"


def test_readme_quotes_the_operating_point_training_chose(comparison: dict, test_report: dict):
    """The threshold `make train` records, which the final evaluation used."""
    readme = (ROOT / "README.md").read_text()
    threshold = comparison["Threshold"]
    assert float(threshold) == pytest.approx(test_report["threshold"])
    assert threshold in readme


@pytest.mark.parametrize(
    "step",
    ["Standard error = leader's CV std / sqrt(5)", "Cutoff = leader - one standard error"],
)
def test_the_documents_quote_the_selection_rule_as_measured(comparison: dict, step: str):
    """The cutoff and its standard error, as the regenerated report has them."""
    value = comparison[step].split("=")[-1].strip()
    for name in ("README.md", "docs/PROJECT_WALKTHROUGH.md"):
        assert value in (ROOT / name).read_text(), f"{name} does not quote {step} = {value}"


#: Claims about the test partition that its history contradicts.
FALSE_HISTORY = (
    "scored exactly once",
    "scored **once**",
    "scored once",
    "single test-set scoring",
    "only place the test partition",
    "test was touched once",
    "one and only test-set scoring",
)


@pytest.mark.parametrize(
    "name",
    [*DOCS, "reports/model_comparison.md", "src/churnsense/evaluation/report.py"],
)
def test_nothing_claims_the_test_partition_was_scored_only_once(name: str):
    """It was scored for several model versions and re-read by reproducibility
    runs; docs/MODEL_CARD.md records that history. No document may say otherwise."""
    text = (ROOT / name).read_text().lower()
    found = [phrase for phrase in FALSE_HISTORY if phrase.lower() in text]
    assert not found, f"{name} repeats a claim the history contradicts: {found}"


def _prose(name: str) -> str:
    """A file's text, lower-cased, with line wrapping undone so a phrase split
    across two lines is still one phrase."""
    return " ".join((ROOT / name).read_text().lower().split())


#: Claims that per-segment thresholds would settle the coverage gap. They are one
#: policy option with costs of their own (docs/MODEL_CARD.md, "Performance by
#: segment"), and the code does not implement them.
SEGMENT_FIX_CLAIMS = ("thresholds would fix", "would fix it", "the better answer")

#: The dashboard's book views score every customer, test rows included; what it
#: never does is compute a figure from them. Saying it never scores them is false.
DASHBOARD_SCORING_CLAIMS = (
    "dashboard never scores",
    "never scored by this dashboard",
    "dashboard does not score",
    "and the api never score",
    "page never scores",
    # False beside the recorded test figures the same page shows.
    "no figure on this page comes from",
)


@pytest.mark.parametrize(
    "name",
    [*DOCS, "reports/model_comparison.md", "src/churnsense/evaluation/report.py"],
)
def test_no_document_presents_per_segment_thresholds_as_a_fix(name: str):
    text = _prose(name)
    found = [phrase for phrase in SEGMENT_FIX_CLAIMS if phrase in text]
    assert not found, f"{name} presents per-segment thresholds as a fix: {found}"


@pytest.mark.parametrize("name", [*DOCS, "app/sections/performance.py"])
def test_nothing_claims_the_dashboard_never_scores_the_test_rows(name: str):
    text = _prose(name)
    found = [phrase for phrase in DASHBOARD_SCORING_CLAIMS if phrase in text]
    assert not found, f"{name} says the dashboard never scores test rows: {found}"


def test_the_model_card_keeps_the_evaluation_history():
    card = (ROOT / "docs" / "MODEL_CARD.md").read_text()
    assert "## Evaluation history" in card


def test_no_document_claims_a_measured_business_saving():
    """Monetary figures must always travel with the simulation label.

    The one claim this project cannot make is that it saved anyone money.
    """
    banned = [
        "saved the business",
        "actual savings",
        "proven roi",
        "guaranteed return",
        "increased retention by",
    ]
    for name in DOCS:
        text = (ROOT / name).read_text().lower()
        for phrase in banned:
            assert phrase not in text, f"{name} contains an unsupportable claim: {phrase!r}"


def test_every_monetary_document_carries_the_simulation_caveat():
    for name in ("README.md", "docs/MODEL_CARD.md"):
        text = (ROOT / name).read_text().lower()
        assert "simulat" in text, f"{name} shows money without saying it is simulated"


def test_readme_links_resolve():
    """A broken relative link in the front door of a portfolio project."""
    readme = (ROOT / "README.md").read_text()
    targets = {
        target
        for target in re.findall(r"\]\((?!https?:)([^)#]+)\)", readme)
        if not target.startswith("#")
    }
    missing = sorted(t for t in targets if not (ROOT / t).exists())
    assert not missing, f"README links to missing paths: {missing}"
