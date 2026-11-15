"""The tracked documents state nothing the public demo withholds.

A document beside the API is read by the same outsider: a validation figure at a threshold
the demo does not publish cuts its intervals finer, and a contract's training churn rate
completes a subtraction the demo is built to prevent. Every tracked markdown file is scanned:

* a table that states validation figures by threshold names only the demo's published cuts
  (``legacy_disclosures.RELEASE_CUTS``) - the two immutable release reports excepted, which
  state the legacy thresholds and are checked against them in ``test_legacy_disclosures``;
* a table with a churn-rate column shows none for one- or two-year contracts, except the
  final evaluation's test-partition table, which is immutable;
* no file quotes an association strength to more than two decimals where it could be
  inverted (Contract's Cramér's V).
"""

from __future__ import annotations

import re
import subprocess

import pytest

from churnsense import legacy_disclosures as legacy
from churnsense.config import PROJECT_ROOT
from tests.conftest import LEGACY_REPORTS

#: Immutable release reports: their figures are judged in test_legacy_disclosures.
IMMUTABLE = set(LEGACY_REPORTS)
#: Headers that make a threshold table a table of validation figures.
FIGURES = ("flagged", "precision", "recall", "net benefit", "retained")


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    )
    return [p for p in out.stdout.splitlines() if not p.startswith("audit-reports/")]


def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables, block = [], []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2 and set("".join(block[1])) <= set("-: "):
            tables.append((block[0], block[2:]))
        block = []
    return tables


def _number(cell: str) -> float | None:
    found = re.fullmatch(r"\**\s*([01]\.\d+)\s*\**", cell)
    return float(found.group(1)) if found else None


DOCUMENTS = _tracked_markdown()


def test_the_scan_sees_the_documents_that_state_validation_figures():
    assert {"README.md", "docs/MODEL_CARD.md", "docs/PROJECT_WALKTHROUGH.md"} <= set(DOCUMENTS)


@pytest.mark.parametrize("path", [p for p in DOCUMENTS if p not in IMMUTABLE])
def test_validation_figures_are_stated_only_at_published_cuts(path):
    text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
    for header, rows in _tables(text):
        lower = [h.lower().strip("* ") for h in header]
        if not lower or not lower[0].startswith("threshold"):
            continue
        if not any(any(f in h for f in FIGURES) for h in lower[1:]):
            continue
        for row in rows:
            value = _number(row[0])
            assert value is not None, (path, row)
            assert any(abs(value - cut) < 1e-9 for cut in legacy.RELEASE_CUTS), (path, row)


@pytest.mark.parametrize("path", [p for p in DOCUMENTS if p != "reports/final_evaluation.md"])
def test_no_document_shows_a_long_contracts_churn_rate(path):
    text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
    for header, rows in _tables(text):
        lower = [h.lower() for h in header]
        rate_columns = [i for i, h in enumerate(lower) if "churn rate" in h or h == "churned"]
        for row in rows:
            if not row or row[0].strip("* ") not in ("One year", "Two year"):
                continue
            for i in rate_columns:
                if i < len(row):
                    assert re.search(r"suppressed|withheld", row[i]), (path, row)


def test_no_document_quotes_the_contract_strength_past_two_decimals():
    pattern = re.compile(r"(Cram[ée]r'?s V|Contract)[^\n|]{0,40}\b0\.4\d{2,}")
    for path in DOCUMENTS:
        text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        assert not pattern.search(text), path


def test_the_scan_finds_a_planted_figure(tmp_path):
    """The control: the same parser catches a threshold table at an unpublished cut."""
    planted = "| Threshold | Flagged | Precision |\n|---|---|---|\n| 0.39 | 385 | 0.61 |\n"
    header, rows = _tables(planted)[0]
    assert _number(rows[0][0]) == 0.39 and 0.39 not in legacy.RELEASE_CUTS
    long_rate = "| Contract | Customers | Churn rate |\n|---|---|---|\n| Two year | 1 | 2.5 % |\n"
    header, rows = _tables(long_rate)[0]
    assert "churn rate" in header[2].lower() and not re.search(r"suppressed|withheld", rows[0][2])
