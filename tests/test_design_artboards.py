"""The design artboards hold invented figures only, and say so on their face.

``docs/design/*.dc.html`` once embedded the validation sweep at every 0.01, segment counts
of a few customers and other measured figures. Every figure they show now comes from
``scripts/design_mock_data.py``: checked here constant by constant, together with the
generator's independence from the dataset, the model and the reports.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re

import pytest

from churnsense.config import PROJECT_ROOT

DESIGN = PROJECT_ROOT / "docs" / "design"
GENERATOR = PROJECT_ROOT / "scripts" / "design_mock_data.py"
ARTBOARDS = sorted(DESIGN.glob("*.dc.html"))


@pytest.fixture(scope="module")
def mock():
    spec = importlib.util.spec_from_file_location("design_mock_data", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _const(text: str, name: str) -> str:
    found = re.findall(rf"^const {name} = (.*?);$", text, flags=re.M)
    assert len(found) == 1, name
    return found[0]


def _js(literal: str):
    """A JS array or object literal of numbers, strings and plain keys, as Python data."""
    quoted = re.sub(r"(?<=[{,])\s*([A-Za-z_]\w*):", r'"\1":', literal.replace("'", '"'))
    return json.loads(re.sub(r",\s*([\]}])", r"\1", quoted))


def test_there_are_eight_artboards():
    assert len(ARTBOARDS) == 8


@pytest.mark.parametrize("path", ARTBOARDS, ids=lambda p: p.name)
def test_every_artboard_says_its_figures_are_illustrative(path, mock):
    text = path.read_text(encoding="utf-8")
    banner = re.search(r'<div role="note" data-mock="illustrative"[^>]*>(.*?)</div>', text)
    assert banner is not None and banner.group(1).startswith(mock.BANNER)
    # Visible: right inside <main>, before the page's own header.
    assert text.index('data-mock="illustrative"') < text.index("<header", text.index("<main"))
    assert f"// {mock.BANNER}" in text


@pytest.mark.parametrize("name", ["Performance.dc.html", "Simulator.dc.html"])
def test_the_sweep_is_the_generators(name, mock):
    text = (DESIGN / name).read_text(encoding="utf-8")
    sweep = mock.validation_sweep()
    assert _js(_const(text, "F")) == sweep["F"]
    assert _js(_const(text, "T")) == sweep["T"]
    if name == "Simulator.dc.html":
        assert _js(_const(text, "S")) == sweep["S"]
        assert _const(text, "POS") == str(mock.POS)
    else:
        assert _const(text, "POS") == f"{mock.POS}, NEG = {mock.NEG}"


def test_the_overview_shows_the_generators_book(mock):
    text = (DESIGN / "Main.dc.html").read_text(encoding="utf-8")
    assert _js(_const(text, "HIST")) == mock.histogram()
    bands = _js(
        re.search(r"^const BANDS = (\[.*?\]);$", text, flags=re.M | re.S).group(1).replace("\n", "")
    )
    assert bands == mock.bands()
    segments = mock.segments()
    assert _js(_const(text, "BINS")) == segments["tenure"]
    for key in ("contract", "payment", "internet", "price"):
        line = re.search(
            rf"^  {key}: {{ label: '[^']*', rows: (\[.*?\]\]), note:", text, flags=re.M
        )
        assert _js(line.group(1)) == segments[key], key
    assert mock.net_benefit_path() in text


def test_the_patterns_associations_are_the_generators(mock):
    text = (DESIGN / "Patterns.dc.html").read_text(encoding="utf-8")
    assert [tuple(r) for r in _js(_const(text, "ASSOC"))] == list(mock.ASSOCIATION)


def test_the_comparison_chart_sits_where_the_invented_scores_put_it(mock):
    text = (DESIGN / "Performance.dc.html").read_text(encoding="utf-8")
    for name, mean, *_ in mock.CANDIDATES[:3]:
        assert f">{mean:.4f}</span></span>" in text, name
        assert f"left: {mock.position(mean)}" in text, name


def test_the_generator_reads_nothing_and_imports_nothing_of_the_project():
    """Invented, not derived: no file is opened, no project module or data library loaded."""
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert modules <= {"__future__", "json", "math", "sys", "numpy"}
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name | ast.Attribute)
    }
    assert not calls & {"open", "read_text", "read_csv", "load", "loads", "read_bytes"}


def test_the_generator_is_deterministic(mock):
    assert mock.everything() == mock.everything()


def test_no_artboard_holds_a_reports_figure():
    """Spot checks against figures the tracked reports print: none appears in an artboard.

    Taken from the tracked files at run time, so nothing measured is written here."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    measured = re.search(r"\| \*\*0\.36\*\* \| \*\*([\d,]+)\*\* \|", readme).group(1)
    eda = (PROJECT_ROOT / "reports" / "eda_report.md").read_text(encoding="utf-8")
    train = re.search(r"\| Customers \| ([\d,]+) \|", eda).group(1)
    for path in ARTBOARDS:
        text = path.read_text(encoding="utf-8")
        assert f">{measured} <" not in text and f"TRAIN · {train}" not in text, path.name
