"""Dashboard helpers that need no trained model, so they run in CI."""

from __future__ import annotations

import io

import pandas as pd
import pytest
from app.components import neutralise_formulas
from app.sections.batch import with_predictions

from churnsense.config import Config
from churnsense.data import schema
from churnsense.models.predict import validate_upload


def _inputs(cfg: Config) -> list[str]:
    return schema.model_input_columns(cfg.features.drop_columns, cfg.features.include_total_charges)


def test_text_cells_a_spreadsheet_would_execute_become_text():
    frame = pd.DataFrame(
        {"customerID": ['=HYPERLINK("https://example.test","x")', "+1", "-1", "@SUM(A1)", "\tx"]}
    )
    out = neutralise_formulas(frame)["customerID"].tolist()
    assert all(cell.startswith("'") for cell in out)
    assert out[0] == '\'=HYPERLINK("https://example.test","x")'


def test_a_trigger_after_a_semicolon_or_tab_is_defused():
    """Excel with ';' as list separator splits an unquoted cell there."""
    frame = pd.DataFrame({"customerID": ["DEMO;=HYPERLINK(1)", "a\t+1", "=a;@b", "A;B"]})
    assert neutralise_formulas(frame)["customerID"].tolist() == [
        "DEMO;'=HYPERLINK(1)",
        "a\t'+1",
        "'=a;'@b",
        "A;B",
    ]


def test_a_trigger_after_a_line_break_or_quote_is_defused():
    """';'-Excel ends the row at a line break inside a non-first comma-CSV field."""
    frame = pd.DataFrame({"note": ["a\n=HYPERLINK(1)", "b\r\n+1", 'c"=1']})
    assert neutralise_formulas(frame)["note"].tolist() == [
        "a\n'=HYPERLINK(1)",
        "b\r\n'+1",
        "c\"'=1",
    ]


def test_column_names_are_neutralised_too():
    frame = pd.DataFrame({'=HYPERLINK("https://example.test","x")': ["a"], "ok": ["b"]})
    assert list(neutralise_formulas(frame).columns) == [
        '\'=HYPERLINK("https://example.test","x")',
        "ok",
    ]


def test_numbers_and_ordinary_text_are_left_alone():
    frame = pd.DataFrame(
        {"MonthlyCharges": [-5.0, 70.35], "Contract": ["Month-to-month", "Two year"]}
    )
    # Not even copied: this runs on every rerun for every export button.
    assert neutralise_formulas(frame) is frame


def test_repeated_column_names_and_categorical_columns_are_covered():
    """Regression: a repeated name crashed the export, and categoricals were skipped."""
    frame = pd.DataFrame([["=1", "a", "=2"]], columns=["x", "x", "band"])
    frame["band"] = pd.Categorical(frame["band"])
    out = neutralise_formulas(frame)
    assert out.iloc[0].tolist() == ["'=1", "a", "'=2"]
    assert list(out.columns) == ["x", "x", "band"]


def test_non_strings_in_a_text_column_survive_and_the_input_is_untouched():
    frame = pd.DataFrame({"note": ["=1+1", None, 7, "plain"]})
    out = neutralise_formulas(frame)
    assert out["note"].tolist() == ["'=1+1", None, 7, "plain"]
    assert frame["note"].tolist() == ["=1+1", None, 7, "plain"]


def test_a_neutralised_export_can_be_uploaded_again(demo_csv):
    """The defence must not break the round trip: the exported file is valid input."""
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    frame.loc[0, "customerID"] = "=1+1"

    exported = neutralise_formulas(frame).to_csv(index=False).encode("utf-8")
    reloaded = validate_upload(exported)

    assert reloaded.loc[0, "customerID"] == "'=1+1"
    assert len(reloaded) == len(frame)
    assert pd.read_csv(io.BytesIO(exported), dtype=str).loc[0, "customerID"] == "'=1+1"


def _fake_predictions(index: pd.Index, probability: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"churn_probability": probability, "risk_band": "Moderate", "flagged": True}, index=index
    )


def test_the_scored_export_keeps_only_known_columns(demo_csv, cfg: Config):
    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    raw["notes"] = "private"
    raw['=HYPERLINK("https://example.test","x")'] = "y"
    cleaned = validate_upload(raw.to_csv(index=False).encode())

    scored = with_predictions(cleaned, _fake_predictions(cleaned.index, 0.4), _inputs(cfg))

    assert "notes" not in scored.columns
    assert not any(str(c).startswith("=") for c in scored.columns)
    assert {"customerID", "Churn", "churn_probability", "flagged"} <= set(scored.columns)


def test_a_scored_export_can_be_scored_again(demo_csv, cfg: Config):
    """Regression: re-uploading the export duplicated `flagged` and crashed the page."""
    cleaned = validate_upload(demo_csv.read_bytes())
    first = with_predictions(cleaned, _fake_predictions(cleaned.index, 0.4), _inputs(cfg))
    exported = neutralise_formulas(first).to_csv(index=False).encode()

    again = validate_upload(exported)
    second = with_predictions(again, _fake_predictions(again.index, 0.9), _inputs(cfg))

    assert not second.columns.duplicated().any()
    assert int(second["flagged"].sum()) == len(second)
    assert second["churn_probability"].eq(0.9).all()


@pytest.mark.parametrize("value", ["=1+1", "a;=1"])
def test_the_scored_export_is_neutralised_end_to_end(demo_csv, cfg: Config, value: str):
    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    raw.loc[0, "customerID"] = value
    cleaned = validate_upload(raw.to_csv(index=False).encode())
    scored = with_predictions(cleaned, _fake_predictions(cleaned.index, 0.4), _inputs(cfg))

    text = neutralise_formulas(scored).to_csv(index=False)
    assert "=1" not in text.replace("'=1", "")
