"""Tests for the column contract."""

from __future__ import annotations

import pandas as pd
import pytest

from churnsense.data import schema
from churnsense.exceptions import SchemaValidationError


def test_raw_columns_cover_every_declared_feature():
    """Nothing can be declared numeric or categorical without being a raw column."""
    declared = set(schema.NUMERIC_COLUMNS) | set(schema.ALLOWED_CATEGORIES)
    assert declared <= set(schema.RAW_COLUMNS)


def test_numeric_and_categorical_sets_are_disjoint():
    assert not set(schema.NUMERIC_COLUMNS) & set(schema.ALLOWED_CATEGORIES)


def test_model_input_columns_exclude_target_and_id():
    columns = schema.model_input_columns()
    assert schema.TARGET not in columns
    assert schema.ID_COLUMN not in columns


@pytest.mark.parametrize("include_total", [True, False])
def test_model_input_columns_respect_total_charges_flag(include_total: bool):
    columns = schema.model_input_columns(("gender",), include_total_charges=include_total)
    assert ("TotalCharges" in columns) is include_total
    assert "gender" not in columns


def test_model_input_column_order_is_stable():
    """Order is persisted in model_meta.json, so it must not depend on set iteration."""
    assert schema.model_input_columns(("gender",)) == schema.model_input_columns(("gender",))


def test_split_feature_types_partitions_without_loss():
    columns = schema.model_input_columns()
    numeric, categorical = schema.split_feature_types(columns)
    assert sorted(numeric + categorical) == sorted(columns)


def test_split_feature_types_rejects_unknown_column():
    with pytest.raises(SchemaValidationError, match="neither the numeric nor the categorical"):
        schema.split_feature_types(["tenure", "NotAFeature"])


def test_validate_frame_accepts_clean_data(clean_frame: pd.DataFrame):
    schema.validate_frame(clean_frame, require_target=True)


def test_validate_frame_rejects_empty_input():
    with pytest.raises(SchemaValidationError, match="no rows"):
        schema.validate_frame(pd.DataFrame(columns=list(schema.RAW_COLUMNS)))


def test_validate_frame_reports_every_problem_at_once(clean_frame: pd.DataFrame):
    """A user fixing a CSV should see all errors in one pass, not one per attempt."""
    broken = clean_frame.drop(columns=["Contract"]).copy()
    broken.loc[broken.index[0], "tenure"] = -5
    broken.loc[broken.index[1], "PaymentMethod"] = "Cheque by pigeon"

    with pytest.raises(SchemaValidationError) as excinfo:
        schema.validate_frame(broken, require_target=True)

    problems = " ".join(excinfo.value.problems)
    assert "Contract" in problems
    assert "tenure" in problems
    assert "PaymentMethod" in problems
    assert len(excinfo.value.problems) >= 3


def test_validate_frame_flags_out_of_range_numerics(clean_frame: pd.DataFrame):
    broken = clean_frame.copy()
    broken.loc[broken.index[0], "MonthlyCharges"] = 10_000
    with pytest.raises(SchemaValidationError, match="outside the valid range"):
        schema.validate_frame(broken)


def test_validate_frame_counts_a_gap_however_it_got_there(clean_frame: pd.DataFrame):
    """An already-NaN value was exempt, and clean_frame makes every typo NaN first."""
    broken = clean_frame.copy()
    broken["tenure"] = broken["tenure"].astype(float)
    broken.loc[broken.index[:3], "tenure"] = float("nan")
    with pytest.raises(SchemaValidationError, match="'tenure' has 3 missing or non-numeric"):
        schema.validate_frame(broken)


def test_validate_frame_requires_whole_months_of_tenure(clean_frame: pd.DataFrame):
    broken = clean_frame.copy()
    broken["tenure"] = broken["tenure"].astype(float)
    broken.loc[broken.index[0], "tenure"] = 5.5
    with pytest.raises(SchemaValidationError, match="'tenure' has 1 non-integer"):
        schema.validate_frame(broken)


def test_validate_frame_rejects_an_unknown_category(clean_frame: pd.DataFrame):
    """There is no lenient mode: a typo must not be encoded into a confident score."""
    broken = clean_frame.copy()
    broken.loc[broken.index[0], "PaymentMethod"] = "Crypto"
    with pytest.raises(SchemaValidationError, match="PaymentMethod"):
        schema.validate_frame(broken)


def test_quoted_values_in_problems_are_bounded(clean_frame: pd.DataFrame):
    """Problems echo uploaded text back; long or numerous values must not balloon them."""
    broken = clean_frame.copy()
    broken["PaymentMethod"] = [f"{i:06d}" + "x" * 10_000 for i in range(len(broken))]
    broken[schema.TARGET] = [f"label{i}" * 1_000 for i in range(len(broken))]
    with pytest.raises(SchemaValidationError) as caught:
        schema.validate_frame(broken, require_target=True)

    assert len(caught.value.problems) == 2
    assert all(len(problem) < 600 for problem in caught.value.problems)
    assert "000000xxx" in " ".join(caught.value.problems)
    # The user is told how much is left, not left to find out one upload at a time.
    hidden = len(broken) - 5
    assert all(f"and {hidden} more" in problem for problem in caught.value.problems)


def test_a_target_problem_quotes_only_the_wrong_labels(clean_frame: pd.DataFrame):
    """Valid labels used to fill the five quoted slots and hide the bad ones."""
    broken = clean_frame.copy()
    broken[schema.TARGET] = (["0", "1", "No", "Yes", "maybe", "x"] * len(broken))[: len(broken)]
    with pytest.raises(SchemaValidationError) as caught:
        schema.validate_frame(broken, require_target=True)
    assert "unexpected labels: ['maybe', 'x']" in " ".join(caught.value.problems)


def test_a_target_mixing_label_styles_is_named_as_such(clean_frame: pd.DataFrame):
    broken = clean_frame.copy()
    broken[schema.TARGET] = (["0", "Yes"] * len(broken))[: len(broken)]
    with pytest.raises(SchemaValidationError, match="mixes Yes/No labels with 0/1"):
        schema.validate_frame(broken, require_target=True)


def test_missing_categories_and_labels_are_counted(clean_frame: pd.DataFrame):
    broken = clean_frame.copy()
    broken["Contract"] = broken["Contract"].astype(object)
    broken.loc[broken.index[:2], "Contract"] = None
    broken[schema.TARGET] = broken[schema.TARGET].astype(object)
    broken.loc[broken.index[0], schema.TARGET] = None
    with pytest.raises(SchemaValidationError) as caught:
        schema.validate_frame(broken, require_target=True)
    assert "'Contract' has 2 missing value(s)" in caught.value.problems
    assert f"target '{schema.TARGET}' has 1 missing label(s)" in caught.value.problems


def test_feature_labels_cover_every_model_input():
    """The dashboard and SHAP narratives look every feature up by label."""
    missing = [c for c in schema.model_input_columns() if c not in schema.FEATURE_LABELS]
    assert missing == []
