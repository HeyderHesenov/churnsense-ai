"""The documented numbers must match the artifacts that produced them.

This project's central claim is that every figure in its documentation was
measured rather than asserted. That claim decays the moment someone retrains
with a different seed, config or dataset and the README keeps the old
numbers — and nothing else in the suite would notice.

So the headline metrics are parsed back out of the prose and compared to
``artifacts/model_meta.json``. Marked slow: it needs a trained artifact and
skips cleanly without one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from churnsense.config import load_config

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def meta() -> dict:
    path = load_config().paths.model_meta_file
    if not path.is_file():
        pytest.skip("needs `make train && make evaluate`")
    data = json.loads(path.read_text())
    if "test_metrics" not in data:
        pytest.skip("needs `make evaluate` to record test metrics")
    return data


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
def test_readme_quotes_the_measured_test_metrics(meta: dict, label: str, key: str):
    readme = (ROOT / "README.md").read_text()
    expected = round(meta["test_metrics"][key], 4)
    assert expected in _numbers_after(readme, label), (
        f"README does not quote the measured test {key} ({expected:.4f}); "
        "re-run `make evaluate` and update the prose"
    )


def test_readme_quotes_the_shipped_threshold(meta: dict):
    readme = (ROOT / "README.md").read_text()
    assert f"{meta['threshold']:.2f}" in readme


def test_the_model_card_quotes_the_same_metrics_as_the_readme(meta: dict):
    """Two documents, one set of numbers."""
    card = (ROOT / "docs" / "MODEL_CARD.md").read_text()
    for key in ("precision", "recall", "roc_auc", "average_precision"):
        expected = round(meta["test_metrics"][key], 4)
        assert str(expected) in card, f"MODEL_CARD.md is missing test {key} = {expected}"


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
    for name in (
        "README.md",
        "docs/MODEL_CARD.md",
        "docs/PROJECT_WALKTHROUGH.md",
        "docs/INTERVIEW_PREPARATION.md",
    ):
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
