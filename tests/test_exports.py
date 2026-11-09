"""Spreadsheet-safe CSV exports, checked against the cases the front end shares."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from churnsense.exports import csv_bytes, neutralise_formulas

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "csv_neutralisation_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[repr(c["input"]) for c in CASES])
def test_each_shared_case_is_defused_as_recorded(case):
    """frontend/src/lib/csv.ts runs the same cases: the two exporters agree cell for cell."""
    frame = pd.DataFrame({"cell": [case["input"]]})
    assert neutralise_formulas(frame)["cell"].iloc[0] == case["expected"]


def test_the_shared_cases_cover_every_trigger():
    starts = {c["input"][:1] for c in CASES if c["expected"].startswith("'")}
    assert {"=", "+", "-", "@", "\t", "\r"} <= starts
    assert any("'" in c["expected"][1:] and not c["expected"].startswith("'") for c in CASES)


def test_a_csv_export_defuses_column_names_too():
    payload = csv_bytes(pd.DataFrame({"=cmd": ["ok"]})).decode()
    assert payload.splitlines()[0] == "'=cmd"
