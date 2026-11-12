"""The documented numbers must match the reports that produced them.

This project's central claim is that every figure in its documentation was
measured rather than asserted. That claim decays the moment someone retrains
or re-evaluates and the README keeps the old numbers - and nothing else in the
suite would notice.

So the selection rule and the operating point are parsed back out of the prose
and compared to ``reports/model_comparison.md``, in git, so this runs in CI too. Those
are integration checks: they need the tracked file (``tests.conftest.tracked_report``).

The held-out partition is different: what may be said of it is a policy
(``evaluation.disclosure``), so those tests read the documents alone, on their own
terms and against synthetic lines, over every document in ``documentation_surface``.
No test reads ``reports/final_evaluation.md``.
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import tracked_report

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
def comparison() -> dict[str, str]:
    """The operating point as `make train` wrote it: a local report, not tracked since E8."""
    return _table_rows(tracked_report("model_comparison.md").read_text())


@pytest.fixture(scope="module")
def selection() -> dict[str, str]:
    """The one-standard-error rule's figures, recomputed from the tracked comparison table,
    as the report prints them (four decimals)."""
    import pandas as pd

    table = pd.read_csv(tracked_report("model_comparison.csv"), float_precision="round_trip")
    leader = table.loc[table["cv_pr_auc"].idxmax()]
    return {
        "Standard error = leader's CV std / sqrt(5)": f"{leader['cv_se']:.4f}",
        "Cutoff = leader - one standard error": f"{leader['cv_pr_auc'] - leader['cv_se']:.4f}",
    }


def _numbers_after(text: str, label: str) -> list[float]:
    """Every number on the lines that mention ``label``."""
    return [
        float(match)
        for line in text.splitlines()
        if label.lower() in line.lower()
        for match in re.findall(r"\d+\.\d+", line)
    ]


def test_readme_quotes_the_operating_point_training_chose(comparison: dict):
    """The threshold `make train` records, which the final evaluation uses."""
    assert comparison["Threshold"] in (ROOT / "README.md").read_text()


# --- the held-out policy, as the documents state it ---------------------------------------

_SCALAR = r"(?:roc[- ]?auc|pr[- ]?auc|average precision|brier|\bece\b|calibration error)"
_NUMBER = r"\b[01]?\.\d{2,}\b"
_HELD_OUT = (
    r"\b(?:on test|test (?:partition|set|figures?|roc|pr|brier|ece|recall|precision)|held[- ]out)\b"
)


def _sections(text: str) -> Iterator[tuple[str, list[str]]]:
    """Each markdown section's heading and lines."""
    heading, lines = "", []
    for line in text.splitlines():
        if line.startswith("#"):
            yield heading, lines
            heading, lines = line, []
        else:
            lines.append(line)
    yield heading, lines


#: Folders the documentation scan never enters: what they hold is protected, generated or
#: not documentation. ``reports`` above all: its contents are neither read nor printed.
_NOT_DOCUMENTATION = frozenset(
    {
        "reports",
        "data",
        "artifacts",
        "audit-reports",
        "node_modules",
        "dist",
        "build",
        "htmlcov",
        "coverage",
        "playwright-report",
        "test-results",
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        ".playwright-mcp",
        "__pycache__",
    }
)
_DOCUMENT_SUFFIXES = frozenset({".md", ".html", ".txt"})


def documentation_surface(root: Path = ROOT) -> list[str]:
    """Every document of the repository, as paths relative to ``root``."""
    found = []
    for folder, children, files in os.walk(root):
        children[:] = [
            c for c in children if c not in _NOT_DOCUMENTATION and not c.endswith(".egg-info")
        ]
        found += [
            str((Path(folder) / name).relative_to(root))
            for name in files
            if Path(name).suffix.lower() in _DOCUMENT_SUFFIXES
        ]
    return sorted(found)


def _is_test_heading(heading: str) -> bool:
    lowered = heading.lower()
    return bool(re.search(r"\btest\b|held[- ]out", lowered)) and "validation" not in lowered


def policy_violations(text: str) -> list[str]:
    """What a document says of the held-out partition that the policy does not publish.

    A rule over prose, so it errs towards a finding: a figure the reader might take
    for a test figure should say which partition it belongs to.
    """
    found = []
    for heading, lines in _sections(text):
        in_test_section = _is_test_heading(heading)
        for paragraph in "\n".join(lines).split("\n\n"):
            for line in paragraph.splitlines():
                lowered = line.lower()
                scalar = re.search(_SCALAR, lowered)
                number = re.search(_NUMBER, lowered)
                is_row = line.startswith("|")
                if scalar and number:
                    if is_row:
                        first_cell = lowered.split("|")[1]
                        if in_test_section or re.search(r"\btest\b", first_cell):
                            found.append(line)
                    elif re.search(_HELD_OUT, lowered):
                        found.append(line)
                if re.search(rf"\b(?:recall|ece)\W+{_NUMBER}\s*(?:→|->)", lowered) and (
                    "validation" not in lowered
                ):
                    found.append(line)
                if re.search(rf"test[- ]optimal\D{{0,40}}{_NUMBER}", lowered):
                    found.append(line)
                if "the no-skill" in lowered and "\u00d7" in lowered and in_test_section:
                    found.append(line)
            flat = " ".join(paragraph.split()).lower()
            if "third decimal" in flat and re.search(r"\btest\b|figures moved", flat):
                found.append(paragraph)
            if (
                re.search(_SCALAR, flat)
                and re.search(rf"(?:on|at) test\W+(?:at\W+)?{_NUMBER}|\btest\W+{_NUMBER}", flat)
                and paragraph not in found
            ):
                found.append(paragraph)
    return found


#: What only a test-marked part of a page may not put a number beside. The scalars, and
#: the parts of the partition the policy keeps back whole.
_HTML_WITHHELD = rf"{_SCALAR}|\bsegment|reliabilit|precision.recall"


def _html_sections(page: str) -> Iterator[list[str]]:
    """A page's visible text, line by line, one list per section.

    Styles and drawings go: nothing in them is read as text. A section's ``aria-label``
    is kept, because it may be all that says which partition the section describes.
    """
    page = re.sub(r"<(style|svg)\b.*?</\1>", "\n", page, flags=re.S | re.I)
    page = re.sub(r'<section\b[^>]*aria-label="([^"]*)"[^>]*>', "\n\f\\1\n", page, flags=re.I)
    page = re.sub(r"<section\b[^>]*>", "\n\f\n", page, flags=re.I)
    page = re.sub(r"<[^>]+>", "\n", page)
    for section in html.unescape(page).split("\f"):
        yield [line.strip() for line in section.splitlines() if line.strip()]


def html_violations(page: str) -> list[str]:
    """``policy_violations`` for a page, where a label and its value are separate elements.

    A section is about the test partition if its own words say so and not validation,
    or if it carries a bare ``TEST`` badge. Within one, a withheld thing followed by a
    number, in its own line or the next two, is a finding; anywhere, so is a label that
    names the test partition itself, as a sidebar's does.
    """
    found: list[str] = []
    for lines in _html_sections(page):
        badge = "TEST" in lines and "VALIDATION" not in lines
        label = bool(lines) and _is_test_heading(lines[0])
        in_test = badge or label
        for index, line in enumerate(lines):
            lowered = line.lower()
            near = " ".join(lines[index : index + 3]).lower()
            if "validation" in lowered or not re.search(_NUMBER, near):
                continue
            if re.search(rf"\btest\b\W*(?:{_SCALAR})", lowered) or (
                in_test and re.search(_HTML_WITHHELD, lowered)
            ):
                found.append(line)
        found += policy_violations("\n".join(lines))
    return found


def violations_in(name: str, text: str) -> list[str]:
    """What a document says that the policy does not publish, whatever its format."""
    return html_violations(text) if name.lower().endswith(".html") else policy_violations(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "## Test set\n\n| Metric | Value |\n|---|---|\n| ROC-AUC | 0.8123 |\n",
            id="test-table-roc",
        ),
        pytest.param("### Test partition\n\n| PR-AUC | 0.61 |\n", id="test-table-pr"),
        pytest.param("## Held-out figures\n\n| Brier score | 0.14 |\n", id="held-out-table-brier"),
        pytest.param("## Test set\n\n| Expected calibration error | 0.03 |\n", id="test-table-ece"),
        pytest.param("## Any\n\n| Test (holdout) | P 0.5 · ROC-AUC 0.81 |\n", id="a-test-row"),
        pytest.param("Measured on the held-out test partition: PR-AUC 0.65.", id="prose-pr"),
        pytest.param("Brier **0.1402** on test; more follows.", id="prose-brier"),
        pytest.param("the same test ROC-AUC (0.8123 for the old model)", id="prose-roc"),
        pytest.param(
            "error fell on validation,\nand it holds on test at **0.031**. Good.\nECE too.",
            id="split-over-lines",
        ),
        pytest.param("Validation PR-AUC 0.65, test 0.61; both small.", id="beside-validation"),
        pytest.param("Scored twice, recall 0.6952 → 0.7011.", id="cross-model-recall"),
        pytest.param("| 2026 | re-score | Recall 0.69 → 0.70, ECE 0.03 → 0.02 |", id="history-row"),
        pytest.param("Effect on test (ece 0.03 → 0.02) was small.", id="cross-model-ece"),
        pytest.param("The effect on\ntest was in the third decimal.", id="size-of-a-difference"),
        pytest.param("| re-score | Figures moved in the third decimal |", id="size-in-a-row"),
        pytest.param("The test-optimal cut (0.27) is discarded.", id="best-cut"),
        pytest.param("## Test set\n\nIt reaches 2.4\u00d7 the no-skill line.\n", id="multiple"),
    ],
)
def test_the_policy_scan_finds_what_the_policy_forbids(text: str):
    """The scan is itself a claim: these synthetic lines are what it exists to catch."""
    assert policy_violations(text), text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "## Test set\n\n| Precision | 0.5 |\n| Recall | 0.61 |\n| F1 | 0.55 |\n",
            id="rates-may-stay",
        ),
        pytest.param(
            "## Validation\n\n| ROC-AUC | 0.81 |\n| Brier | 0.14 |\n", id="validation-table"
        ),
        pytest.param(
            "| Model | Val PR-AUC | Val ROC-AUC |\n|---|---|---|\n| a | 0.65 | 0.84 |\n",
            id="validation-columns",
        ),
        pytest.param("Validation keeps PR-AUC 0.65 and ROC-AUC 0.84.", id="validation-prose"),
        pytest.param(
            "validation ECE: 0.03 → 0.02, and precision 0.5 → 0.6, recall 0.7 → 0.6 on validation.",
            id="validation-arrow",
        ),
        pytest.param(
            "## Test set\n\nBrier, ROC-AUC and PR-AUC are not published here.\n",
            id="saying-it-is-withheld",
        ),
        pytest.param(
            "## Test set\n\n| Expected calibration error | — |\n", id="a-dash-is-no-figure"
        ),
    ],
)
def test_the_policy_scan_leaves_alone_what_the_policy_allows(text: str):
    assert not policy_violations(text), text


def test_the_third_decimal_alone_says_nothing_of_the_test_partition():
    assert not policy_violations("A form and a job start disagreeing in the third decimal.")


_SIDEBAR = (
    '<div><div style="color: #8b949e">{label}</div><div style="font: 13px">{value}</div></div>'
)
_CARD = (
    '<div style="padding: 14px"><div style="color: #adb7c2">{label}</div>'
    '<div style="font-size: 26px">{value}</div><div style="color: #8b949e">{sub}</div></div>'
)
_TEST_METRICS = '<section aria-label="Test metrics" style="display: grid">{}</section>'
_BADGED = "<section><h2>{title}</h2><span>TEST</span>{body}</section>"


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(_SIDEBAR.format(label="Test ROC-AUC", value="0.81"), id="sidebar-roc"),
        pytest.param(_SIDEBAR.format(label="Test ECE", value="0.03"), id="sidebar-ece"),
        pytest.param(
            _TEST_METRICS.format(_CARD.format(label="ROC-AUC", value="0.81", sub="ranking")),
            id="labelled-section-card",
        ),
        pytest.param(
            _TEST_METRICS.format(
                "<div><span>PR-AUC <span>· primary metric</span></span><span>TEST</span></div>"
                "<div><span>0.61</span></div>"
            ),
            id="value-after-a-badge",
        ),
        pytest.param(
            _TEST_METRICS.format(
                _CARD.format(label="Calibration error", value="—", sub="Brier 0.13")
            ),
            id="brier-in-a-subtitle",
        ),
        pytest.param(
            _BADGED.format(
                title="Performance by segment",
                body="<table><tr><td>Segment</td></tr><tr><td>One year</td><td>0.44</td></tr></table>",
            ),
            id="segment-table",
        ),
        pytest.param(
            _BADGED.format(title="Reliability", body="<p>Observed 0.31 in the first bin.</p>"),
            id="reliability",
        ),
        pytest.param(
            _BADGED.format(
                title="Precision\u2013recall curve", body="<span>0.72 at the cut</span>"
            ),
            id="pr-figure",
        ),
        pytest.param("<p>The test-optimal cut (0.27) is discarded.</p>", id="best-cut"),
        pytest.param("<p>The effect on test was in the third decimal.</p>", id="size-of-a-change"),
    ],
)
def test_the_page_scan_finds_what_the_policy_forbids(page: str):
    assert html_violations(page), page


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(_SIDEBAR.format(label="Test recall", value="0.70"), id="a-released-rate"),
        pytest.param(_SIDEBAR.format(label="Validation ROC-AUC", value="0.83"), id="validation"),
        pytest.param(
            _TEST_METRICS.format(_CARD.format(label="ROC-AUC", value="—", sub="not published")),
            id="a-dash-is-no-figure",
        ),
        pytest.param(
            '<section aria-label="Validation metrics">'
            + _CARD.format(label="Brier", value="0.13", sub="validation")
            + "</section>",
            id="validation-section",
        ),
        pytest.param(
            "<section><h2>Sweep</h2><span>VALIDATION</span><p>ECE 0.03 and ROC-AUC 0.83</p></section>",
            id="validation-badge",
        ),
        pytest.param(
            '<section style="letter-spacing: -0.028em"><p>Test ROC-AUC is withheld.</p></section>',
            id="a-style-is-no-figure",
        ),
        pytest.param(
            "<svg><text>Test ROC-AUC 0.81</text></svg><style>.a{top:0.5px}</style>",
            id="drawings-go",
        ),
    ],
)
def test_the_page_scan_leaves_alone_what_the_policy_allows(page: str):
    assert not html_violations(page), page


def test_the_scan_chooses_by_format():
    page = _SIDEBAR.format(label="Test ROC-AUC", value="0.81")
    assert violations_in("Board.dc.html", page)
    assert violations_in("BOARD.HTML", page)
    assert not violations_in("note.md", "Test ROC-AUC is withheld.")


def test_the_surface_reaches_every_kind_of_document(tmp_path):
    for name in (
        "README.md",
        "SECURITY.md",
        "docs/MODEL_CARD.md",
        "docs/notes.txt",
        "docs/design/Board.dc.html",
        "frontend/README.md",
        "reports/final_evaluation.md",
        "data/raw/note.txt",
        "artifacts/note.md",
        "audit-reports/note.md",
        "frontend/node_modules/x/README.md",
        "src/churnsense.egg-info/SOURCES.txt",
        "frontend/dist/index.html",
        "docs/design/canvas.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    assert documentation_surface(tmp_path) == [
        "README.md",
        "SECURITY.md",
        "docs/MODEL_CARD.md",
        "docs/design/Board.dc.html",
        "docs/notes.txt",
        "frontend/README.md",
    ]


def test_the_surface_holds_the_documents_this_policy_was_written_for():
    surface = documentation_surface()
    for name in (*DOCS, "SECURITY.md", "docs/frontend/PLAN.md", "docs/design/README.md"):
        assert name in surface, name
    boards = [name for name in surface if name.endswith(".dc.html")]
    assert len(boards) == 8, boards
    assert not [name for name in surface if name.split("/")[0] in _NOT_DOCUMENTATION]


@pytest.mark.parametrize("name", documentation_surface())
def test_no_document_quotes_a_withheld_held_out_figure(name: str):
    """Test ECE, Brier, ROC-AUC, PR-AUC, and recall compared across models."""
    found = violations_in(name, (ROOT / name).read_text(encoding="utf-8", errors="replace"))
    # Lines hold real figures: the failure names how many and where, not what. A bare
    # assert would print them, since pytest shows the list it fails on.
    if found:
        pytest.fail(f"{name}: {len(found)} line(s) break the held-out policy", pytrace=False)


@pytest.mark.parametrize("name", [*DOCS, "SECURITY.md"])
def test_no_document_links_to_the_historical_report(name: str):
    text = (ROOT / name).read_text()
    assert "](reports/final_evaluation.md)" not in text
    assert "blob/main/reports/final_evaluation.md" not in text


def _published_table(name: str) -> dict[str, float]:
    """The test table of a document: every metric row, and the four cells."""
    text = (ROOT / name).read_text()
    if name == "README.md":
        block = text.split("| Metric | Test |")[1].split("### ")[0]
    else:
        block = text.split("### Test set")[1].split("### ")[0]
    rows = _table_rows(block)
    return {
        **{k: float(v) for k, v in rows.items() if re.fullmatch(r"[\d.]+", v)},
        **_cells(block),
    }


def _cells(block: str) -> dict[str, float]:
    stayed = re.search(r"\*\*Actually stayed\*\* \| ([\d,*]+) \| ([\d,*]+) \|", block)
    churned = re.search(r"\*\*Actually churned\*\* \| ([\d,*]+) \| ([\d,*]+) \|", block)
    assert stayed and churned, "the confusion matrix is missing"
    count = lambda text: int(text.strip("*").replace(",", ""))  # noqa: E731
    return {
        "tn": count(stayed[1]),
        "fp": count(stayed[2]),
        "fn": count(churned[1]),
        "tp": count(churned[2]),
    }


@pytest.mark.parametrize("name", ["README.md", "docs/MODEL_CARD.md"])
def test_the_published_table_respects_the_cell_floor_and_says_only_what_it_gives(name: str):
    """Reported rates are worked out from the document's own four cells, nothing else."""
    from churnsense.evaluation.disclosure import MIN_CELL, release

    table = _published_table(name)
    assert min(table["tn"], table["fp"], table["fn"], table["tp"]) >= MIN_CELL, name
    assert set(table) == {"Precision", "Recall", "F1", "Accuracy", "tn", "fp", "fn", "tp"}, (
        "a test table holds the table's rates and nothing else"
    )
    tn, fp, fn, tp = (int(table[key]) for key in ("tn", "fp", "fn", "tp"))
    released = release(
        {
            "threshold": 0.5,
            "n": tn + fp + fn + tp,
            "positives": tp + fn,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "tp": tp,
        }
    )
    for label, key in (
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("F1", "f1"),
        ("Accuracy", "accuracy"),
    ):
        assert table[label] == pytest.approx(released[key], abs=1e-4), label


def test_two_documents_quote_one_table():
    assert _published_table("README.md") == _published_table("docs/MODEL_CARD.md")


@pytest.mark.parametrize("name", [*DOCS, "SECURITY.md"])
def test_no_document_claims_a_ledger_reaches_beyond_its_checkout(name: str):
    """A clone evaluates on its own ledger: the rule is a working tree's, then a procedure's."""
    text = " ".join((ROOT / name).read_text().split()).lower()
    for claim in (
        "no other artifact, threshold or configuration can be evaluated",
        "no other model, threshold or configuration can",
        "partition has one release",
        "global lock prevents",
    ):
        assert claim not in text, f"{name}: {claim}"


def test_the_refusal_does_not_claim_a_ledger_reaches_beyond_its_checkout():
    from churnsense.evaluation.report import REFUSAL

    assert "no other model" not in " ".join(REFUSAL.split()).lower()
    assert "SECURITY.md" in REFUSAL


def test_the_security_policy_tells_the_levels_apart():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    for needed in (
        "One working tree",
        "not a global lock",
        "MacBook Air M3",
        "~/Desktop/Customer Retention Intelligence Platform",
        "held-out-release-governance",
        "An independent calculation",
        "Manual step.",
        "governance/release_recoveries.json",
        "Maintainer review",
    ):
        assert needed in text, needed


def test_the_documents_keep_the_levels_of_the_release():
    for name in ("README.md", "docs/MODEL_CARD.md", "SECURITY.md"):
        text = " ".join((ROOT / name).read_text().split())
        assert "working tree" in text, name
        assert "best-effort consistency check" in text, name
        assert "not a required check" in text, name
        assert "public dataset" in text, name


#: Everything that describes the ledger's check, in code as well as in prose.
_GOVERNANCE_TEXTS = (
    "README.md",
    "SECURITY.md",
    "docs/MODEL_CARD.md",
    "docs/frontend/PLAN.md",
    "src/churnsense/evaluation/release.py",
    "src/churnsense/evaluation/disclosure.py",
    "src/churnsense/evaluation/report.py",
    "scripts/check_release_ledger.py",
    ".github/workflows/held-out-release-governance.yml",
)

#: Claims the audit found untrue of a ``pull_request`` check that is in no ruleset.
_FALSE_CLAIMS = (
    r"the required check",
    r"a required check on\b",
    r"its required checks",
    r"required check `?held-out",
    r"refuses a second(?: release|[.,;])",
    r"rules (themselves )?come from",
    r"cannot rewrite the rules",
    r"enforced by github",
    r"guards `?main`?",
    r"reviewed maintenance",
    r"reviewed by a person",
    r"recovery record",
    r"reviewed pr\b",
    r"on the protected branch, ",
    r"once every check passes",
    r"not used up",
    r"do not burn",
    r"cannot use up",
    r"one machine and one operator mean one ledger",
    r"the change that first adds the script runs its own copy\.",
    r"command ignores `?churnsense_config`?\.",
    r"\(removed\)",
    r"no dry run",
    r"first runs after the reservation",
    r"(dry run|rehearsal) (removes|eliminates|guarantees)",
)


def _false_claims_in(text: str) -> list[str]:
    flat = " ".join(text.lower().split()).replace("# ", "")
    return [claim for claim in _FALSE_CLAIMS if re.search(claim, flat)]


@pytest.mark.parametrize("name", _GOVERNANCE_TEXTS)
def test_no_document_calls_the_check_an_active_gate(name: str):
    found = _false_claims_in((ROOT / name).read_text())
    assert not found, f"{name}: {found}"


@pytest.mark.parametrize(
    "sentence",
    [
        "The required check stops that reaching main.",
        "a required check on\n`main` refuses a second. Another clone",
        "is on protected main and its required checks are green",
        "The rules themselves come from main.",
        "# The rules come from the target, so a change cannot\n# rewrite the rules it is judged by.",
        "The protected branch, enforced by GitHub.",
        "`held-out-release-governance` guards `main`.",
        "left to a reviewed maintenance change",
        "In a pull request reviewed by a person, set the ledger back.",
        "only with a recovery record",
        "Pull requests only merge once every check passes.",
        "A broken scorer leaves it to retry, so the one release is not used up.",
        "**Failures do not burn the release.**",
        "An unsuitable environment cannot use up the release.",
        "One machine and one operator\nmean one ledger.",
        "The change that first\nadds the script runs its own copy.",
        "The command ignores\n`CHURNSENSE_CONFIG`.",
        "quoted test precision, which no command produces (removed)",
        "There is no dry run to catch it first.",
        "The model first runs after the reservation.",
        "This rehearsal eliminates the risk.",
        "The dry run guarantees it cannot fail.",
    ],
)
def test_the_scan_would_notice_each_false_claim(sentence: str):
    """A list of patterns nothing has ever matched protects nothing."""
    assert _false_claims_in(sentence), sentence


def test_the_scan_leaves_an_honest_sentence_alone():
    for honest in (
        "It is not a required check today.",
        "`score()` refuses a second press by the live request.",
        "The job is a required check, so any known vulnerability blocks merging.",
    ):
        assert not _false_claims_in(honest), honest


def test_the_policy_states_the_trust_limit_and_the_manual_review():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    for needed in (
        "not a trusted boundary",
        "does not make it tamper-resistant",
        "`.github/workflows/**`",
        "`scripts/check_release_ledger.py`",
        "`governance/**`",
        "`src/churnsense/evaluation/release.py`",
        "`src/churnsense/evaluation/disclosure.py`",
        "trusted GitHub App",
        "pull_request_target",
    ):
        assert needed in text, needed


def test_the_policy_says_a_reservation_has_no_way_back_and_how_to_keep_it():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    for needed in (
        "`git restore`",
        "Run no Git command that writes",
        "the boundary is operational, not cryptographic",
        "There is no recovery path",
        "A different identity",
        "not active today",
    ):
        assert needed in text, needed
    assert "required approval" in text.lower()


def test_the_policy_says_a_valid_shape_is_not_a_true_value():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    assert "A valid shape is not a true value" in text
    assert "outside what this project's readers can check" in text


def test_the_policy_tells_the_four_kinds_of_failure_apart():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    for needed in (
        "**Before the reservation.**",
        "**After it, and transient.**",
        "**After it, and deterministic.**",
        "**After publication.**",
        "can permanently consume the release",
        "without scoring",
    ):
        assert needed in text, needed


def test_the_policy_says_what_the_rehearsal_does_and_cannot_do():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    for needed in (
        "validation partition",
        "It reduces this risk and does not remove it",
        "unexpected failure specific to the test rows",
        "a system-level failure",
        "`make_splits` loads the whole file and stratifies on every label",
        "held in the process's memory",
    ):
        assert needed in text, needed


def test_the_code_says_the_rehearsal_does_not_remove_the_risk():
    for name in ("src/churnsense/evaluation/release.py", "src/churnsense/evaluation/report.py"):
        assert "does not remove" in " ".join((ROOT / name).read_text().split()), name


def test_the_policy_lists_what_may_not_happen_during_a_reservation():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    section = text.split("### After a reservation")[1].split("### ")[0]
    for needed in (
        "`make train`",
        "`make dev`",
        "`make all`",
        "`make clean`",
        "lock",
        "Dependabot",
        "Python",
        "scoring",
        "fingerprinted",
        "artifact",
        "configuration",
        "no new branch",
        "no commit",
        "rebase",
        "`git restore`",
        "same identity",
        "stop",
    ):
        assert needed in section, needed


def test_a_reservation_is_kept_by_changing_nothing_not_by_a_branch():
    """A branch and a pull request mid-release would be the Git change the same section forbids.

    The canonical checkout may sit on a branch carrying other work, so a pull request made from it
    could carry that work too. Nothing in Git moves until the release is published.
    """
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    section = text.split("### After a reservation")[1].split("### ")[0]
    for absent in ("new branch made at", "open a pull request", "commit the reservation"):
        assert absent not in section, absent
    assert "committed only then" in section
    release_steps = text.split("### The official release")[1].split("### ")[0]
    assert "Only once the ledger reads `published`" in release_steps
    code = " ".join((ROOT / "src/churnsense/evaluation/release.py").read_text().split())
    assert "right after a failed run" not in code
    assert "A reservation is not committed" in code


def test_a_report_rendered_again_says_which_partition_is_not_scored():
    """Validation is scored again to draw the report; the test partition is not."""
    phrase = "without scoring the test partition again"
    for name in ("SECURITY.md", "src/churnsense/evaluation/report.py"):
        assert phrase in " ".join((ROOT / name).read_text().split()), name


def test_the_policy_does_not_send_a_retry_to_update_main():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    assert "or for a retry the committed reservation" not in text
    assert "Do not pull" in text


def test_the_policy_is_exact_about_the_config_variable():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    assert "logging may still read `CHURNSENSE_CONFIG`" in text


def test_the_policy_limits_the_next_policy():
    text = " ".join((ROOT / "SECURITY.md").read_text().split())
    assert "at most one version ahead" in text
    assert "in the same change" in text


def test_the_code_says_what_the_check_is():
    for name in ("src/churnsense/evaluation/release.py", "scripts/check_release_ledger.py"):
        text = " ".join((ROOT / name).read_text().split())
        assert "best-effort consistency check" in text, name
        assert "not a required check" in text, name


def test_validation_keeps_its_ranking_metrics_under_its_own_name():
    """Withholding a test figure does not remove the validation one, nor relabel it."""
    readme = (ROOT / "README.md").read_text()
    assert (
        "| Model | CV PR-AUC | CV std | CV SE | Within 1 SE | Val PR-AUC | Val ROC-AUC |" in readme
    )
    interview = (ROOT / "docs" / "INTERVIEW_PREPARATION.md").read_text()
    assert re.search(r"Validation keeps PR-AUC [\d.]+ and ROC-AUC [\d.]+\.", interview)
    test_block = readme.split("| Metric | Test |")[1].split("### ")[0]
    assert "validation" not in test_block.split("The test partition's customers")[0].lower()


def test_the_documents_say_the_release_is_frozen_and_the_history_is_open():
    security = (ROOT / "SECURITY.md").read_text()
    for needed in (
        "governance/held_out_release.json",
        "Run the same command again",
        "commit the ledger",
        "differential privacy",
    ):
        assert needed in security, needed
    for name in ("README.md", "docs/MODEL_CARD.md", "SECURITY.md"):
        text = " ".join((ROOT / name).read_text().split())
        assert "reports/final_evaluation.md" in text or "Its report remains" in text, name
    card = " ".join((ROOT / "docs" / "MODEL_CARD.md").read_text().split())
    assert "open governance issue, not a resolved one" in card
    readme = " ".join((ROOT / "README.md").read_text().split())
    assert "open governance issue, not a resolved one" in readme
    assert "That exposure is open" in " ".join(security.split())


@pytest.mark.parametrize(
    "step",
    ["Standard error = leader's CV std / sqrt(5)", "Cutoff = leader - one standard error"],
)
def test_the_documents_quote_the_selection_rule_as_measured(selection: dict, step: str):
    """The cutoff and its standard error, recomputed from the tracked comparison table."""
    value = selection[step]
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


@pytest.mark.parametrize("name", [*DOCS, "src/churnsense/evaluation/report.py"])
def test_nothing_claims_the_test_partition_was_scored_only_once(name: str):
    """It was scored for several model versions and re-read by reproducibility
    runs; docs/MODEL_CARD.md records that history. No document may say otherwise."""
    text = (ROOT / name).read_text().lower()
    found = [phrase for phrase in FALSE_HISTORY if phrase.lower() in text]
    assert not found, f"{name} repeats a claim the history contradicts: {found}"


def test_the_tracked_comparison_report_repeats_no_false_history():
    """Integration: the report as committed. ``test_train`` checks what the renderer writes."""
    text = tracked_report("model_comparison.md").read_text().lower()
    found = [phrase for phrase in FALSE_HISTORY if phrase.lower() in text]
    assert not found, f"the tracked comparison repeats a claim the history contradicts: {found}"


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


@pytest.mark.parametrize("name", [*DOCS, "src/churnsense/evaluation/report.py"])
def test_no_document_presents_per_segment_thresholds_as_a_fix(name: str):
    text = _prose(name)
    found = [phrase for phrase in SEGMENT_FIX_CLAIMS if phrase in text]
    assert not found, f"{name} presents per-segment thresholds as a fix: {found}"


def test_the_tracked_comparison_report_presents_no_threshold_as_a_fix():
    """Integration: the report as committed. ``test_train`` checks what the renderer writes."""
    text = " ".join(tracked_report("model_comparison.md").read_text().lower().split())
    found = [phrase for phrase in SEGMENT_FIX_CLAIMS if phrase in text]
    assert not found, f"the tracked comparison presents thresholds as a fix: {found}"


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


def _readme_links() -> set[str]:
    readme = (ROOT / "README.md").read_text()
    return {
        target
        for target in re.findall(r"\]\((?!https?:)([^)#]+)\)", readme)
        if not target.startswith("#")
    }


def test_readme_links_resolve():
    """A broken relative link in the front door of a portfolio project."""
    targets = {t for t in _readme_links() if not t.startswith("reports/")}
    missing = sorted(t for t in targets if not (ROOT / t).exists())
    assert not missing, f"README links to missing paths: {missing}"


def test_readme_links_to_reports_resolve():
    """Integration: the tracked reports the README points at. Only their names are used."""
    for target in sorted(t for t in _readme_links() if t.startswith("reports/")):
        tracked_report(target.removeprefix("reports/"))
