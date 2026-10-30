"""No user-visible string claims a cause.

Attributions say what moved *the model's estimate*; associations say what went
together in one historical snapshot. Neither licenses "causes", "drives" or
"will prevent". The SHAP narrative has its own test (test_explainability.py);
this one covers everything else a person reads: the API's OpenAPI text, its
fixed messages and caveats, and - once built - the front end's own bundle.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Phrases that assert causation or certainty. Word-bounded and case-blind.
CAUSAL = re.compile(
    r"\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents|"
    r"guarantees?|guaranteed)\b",
    re.IGNORECASE,
)


def _offending(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in CAUSAL.finditer(text)})


def _string_literals(path: Path) -> list[str]:
    """The string literals in ``path`` that are not docstrings: messages, details, captions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_patterns_catch_what_they_are_for():
    assert _offending("Tenure drives churn and guarantees retention") == ["drives", "guarantees"]
    assert not _offending("Tenure raises the estimate; nothing here identifies a cause")


def test_the_openapi_text_makes_no_causal_claim():
    from churnsense.api.main import app

    assert not _offending(json.dumps(app.openapi()))


@pytest.mark.parametrize(
    "path",
    [
        *sorted((ROOT / "src" / "churnsense" / "api").glob("*.py")),
        ROOT / "src" / "churnsense" / "book.py",
    ],
    ids=lambda p: p.name,
)
def test_no_message_or_caption_the_api_sends_makes_a_causal_claim(path: Path):
    text = "\n".join(_string_literals(path))
    assert not _offending(text), path.name


def test_the_fixed_caveats_make_no_causal_claim():
    from churnsense.api.main import LIMITATIONS
    from churnsense.book import WITHHELD_REASONS
    from churnsense.data.schema import model_input_columns
    from churnsense.evaluation.threshold import DISCLAIMER
    from churnsense.explainability.shap_explain import CAVEAT
    from churnsense.models.predict import upload_rules

    fixed = [
        *LIMITATIONS,
        *WITHHELD_REASONS.values(),
        DISCLAIMER,
        CAVEAT,
        *upload_rules(model_input_columns()),
        *upload_rules(model_input_columns(include_total_charges=False)),
    ]
    for text in fixed:
        assert not _offending(text), text


def _bundle_files() -> list[Path]:
    """The built front end's own code: every chunk but the third-party vendor one."""
    assets = ROOT / "frontend" / "dist" / "assets"
    return [p for p in sorted(assets.glob("*.js")) if not p.name.startswith("vendor")]


@pytest.mark.skipif(not _bundle_files(), reason="the front end is not built (make frontend)")
def test_the_built_front_end_makes_no_causal_claim():
    offenders = {p.name: found for p in _bundle_files() if (found := _offending(p.read_text()))}
    assert not offenders, offenders
