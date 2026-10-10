"""Tests for cleaning, column selection and partitioning."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense.config import Config
from churnsense.data import schema
from churnsense.data.loader import clean_frame, features_and_target, read_raw
from churnsense.data.split import make_splits
from churnsense.exceptions import DataError, SchemaValidationError


def test_blank_total_charges_at_zero_tenure_becomes_zero(raw_frame: pd.DataFrame):
    """The documented imputation rule, pinned so it cannot silently change."""
    cleaned, report = clean_frame(raw_frame)
    zero_tenure = cleaned[cleaned["tenure"] == 0]

    assert report.total_charges_blank == len(zero_tenure)
    assert report.total_charges_filled_zero == len(zero_tenure)
    assert report.total_charges_filled_derived == 0
    assert (zero_tenure["TotalCharges"] == 0.0).all()


def test_blank_total_charges_with_tenure_is_derived_not_zeroed(raw_frame: pd.DataFrame):
    """A blank on a customer who *has* been billed is recoverable, not zero."""
    frame = raw_frame.copy()
    target_row = frame.index[frame["tenure"].astype(int) > 0][0]
    frame.loc[target_row, "TotalCharges"] = " "

    cleaned, report = clean_frame(frame)

    assert report.total_charges_filled_derived == 1
    expected = float(frame.loc[target_row, "tenure"]) * float(
        frame.loc[target_row, "MonthlyCharges"]
    )
    assert cleaned.loc[target_row, "TotalCharges"] == pytest.approx(expected)


def test_scoring_can_refuse_to_estimate_a_blank_total(raw_frame: pd.DataFrame):
    """Uploads turn the estimate off: the blank is reported, not filled."""
    frame = raw_frame.copy()
    target_row = frame.index[frame["tenure"].astype(int) > 0][0]
    frame.loc[target_row, "TotalCharges"] = " "

    with pytest.raises(SchemaValidationError, match="'TotalCharges' has 1 missing"):
        clean_frame(frame, derive_total_charges=False)


def test_unparseable_total_charges_are_reported_not_recovered(raw_frame: pd.DataFrame):
    """A typo is not a gap: even training data must not estimate over it."""
    frame = raw_frame.copy()
    target_row = frame.index[frame["tenure"].astype(int) > 0][0]
    frame.loc[target_row, "TotalCharges"] = "oops"

    with pytest.raises(SchemaValidationError, match="'TotalCharges' has 1 missing"):
        clean_frame(frame)


def test_clean_frame_produces_numeric_columns(clean_frame: pd.DataFrame):
    for column in schema.NUMERIC_COLUMNS:
        assert pd.api.types.is_numeric_dtype(clean_frame[column]), column


def test_senior_citizen_is_categorical_not_numeric(clean_frame: pd.DataFrame):
    """A 0/1 flag must not be scaled as if it were a magnitude."""
    assert pd.api.types.is_string_dtype(clean_frame["SeniorCitizen"])
    assert set(clean_frame["SeniorCitizen"].unique()) <= {"0", "1"}


def test_target_becomes_binary(clean_frame: pd.DataFrame):
    assert set(clean_frame[schema.TARGET].unique()) <= {0, 1}
    assert pd.api.types.is_integer_dtype(clean_frame[schema.TARGET])


def test_exact_duplicates_are_dropped_and_reported(raw_frame: pd.DataFrame):
    doubled = pd.concat([raw_frame, raw_frame.head(3)], ignore_index=True)
    cleaned, report = clean_frame(doubled)
    assert report.duplicate_rows_dropped == 3
    assert len(cleaned) == len(raw_frame)


def test_scoring_files_keep_their_duplicate_rows(raw_frame: pd.DataFrame):
    """Two customers may share every value; a scoring file is one row out per row in."""
    doubled = pd.concat([raw_frame.head(5), raw_frame.head(1)], ignore_index=True)
    cleaned, report = clean_frame(doubled, deduplicate=False)
    assert report.duplicate_rows_dropped == 0
    assert list(cleaned.index) == list(range(6))


@pytest.mark.parametrize("bad_label", ["maybe", "", "churned"])
def test_an_unknown_target_label_is_rejected_not_read_as_no_churn(
    raw_frame: pd.DataFrame, bad_label: str
):
    """Regression: the label used to be encoded before validation, so any
    value other than "Yes" silently became 0 and the check could never fail."""
    frame = raw_frame.copy()
    frame.loc[frame.index[0], schema.TARGET] = bad_label
    with pytest.raises(SchemaValidationError, match=schema.TARGET):
        clean_frame(frame)


def test_an_already_encoded_target_is_read_correctly(raw_frame: pd.DataFrame):
    expected = raw_frame[schema.TARGET].eq("Yes").astype(int).to_numpy()
    encoded = raw_frame.assign(**{schema.TARGET: expected.astype(str)})
    cleaned, _ = clean_frame(encoded)
    np.testing.assert_array_equal(cleaned[schema.TARGET].to_numpy(), expected)


def test_clean_frame_does_not_mutate_its_input(raw_frame: pd.DataFrame):
    before = raw_frame.copy()
    clean_frame(raw_frame)
    pd.testing.assert_frame_equal(raw_frame, before)


def test_whitespace_is_stripped(raw_frame: pd.DataFrame):
    padded = raw_frame.copy()
    padded["Contract"] = "  " + padded["Contract"] + " "
    cleaned, report = clean_frame(padded)
    assert "Contract" in report.stripped_columns
    assert set(cleaned["Contract"].unique()) <= set(schema.ALLOWED_CATEGORIES["Contract"])


def test_string_dtype_text_is_cleaned_like_object_text(raw_frame: pd.DataFrame):
    """Regression: pandas 3 reads text as ``str`` (StringDtype), not object.
    Only object columns were stripped, so the blank " " TotalCharges at tenure
    0 stayed a space and failed validation instead of becoming 0.0."""
    expected, expected_report = clean_frame(raw_frame.astype(object))
    cleaned, report = clean_frame(raw_frame.astype(pd.StringDtype(na_value=np.nan)))

    assert report == expected_report
    pd.testing.assert_frame_equal(cleaned, expected, check_dtype=False)


def test_read_raw_gives_actionable_error_when_file_is_absent(tmp_path, cfg: Config):
    with pytest.raises(DataError, match="make data"):
        read_raw(tmp_path / "nope.csv", cfg)


def test_features_and_target_applies_config_drops(clean_frame: pd.DataFrame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    for dropped in (*cfg.features.drop_columns, schema.TARGET, schema.ID_COLUMN):
        assert dropped not in X.columns
    assert len(X) == len(y) == len(clean_frame)


def test_a_repeated_customer_id_cannot_become_a_training_set(
    clean_frame: pd.DataFrame, cfg: Config
):
    """The same customer on both sides of a split would leak their label."""
    frame = clean_frame.copy()
    frame.loc[frame.index[1], schema.ID_COLUMN] = frame.loc[frame.index[0], schema.ID_COLUMN]
    with pytest.raises(DataError, match="more than once"):
        features_and_target(frame, cfg)


def test_features_and_target_requires_the_target(clean_frame: pd.DataFrame, cfg: Config):
    with pytest.raises(DataError, match="target column"):
        features_and_target(clean_frame.drop(columns=[schema.TARGET]), cfg)


# --- splitting --------------------------------------------------------------


def test_splits_partition_the_data_without_overlap(clean_frame: pd.DataFrame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)

    indices = [set(part.index) for part in (splits.X_train, splits.X_val, splits.X_test)]
    assert sum(map(len, indices)) == len(X)
    assert set.union(*indices) == set(X.index)
    for a, b in ((0, 1), (0, 2), (1, 2)):
        assert not indices[a] & indices[b], "partitions must not share rows"


def test_split_proportions_match_the_configured_fractions(clean_frame: pd.DataFrame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    summary = make_splits(X, y, cfg).summary()

    assert summary["test"]["share"] == pytest.approx(cfg.split.test_size, abs=0.01)
    assert summary["validation"]["share"] == pytest.approx(cfg.split.validation_size, abs=0.01)
    assert summary["train"]["share"] == pytest.approx(cfg.split.train_size, abs=0.01)


def test_stratification_preserves_the_positive_rate(clean_frame: pd.DataFrame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    summary = make_splits(X, y, cfg).summary()
    overall = float(y.mean())
    for part in summary.values():
        assert part["positive_rate"] == pytest.approx(overall, abs=0.02)


def test_splits_are_reproducible(clean_frame: pd.DataFrame, cfg: Config):
    X, y = features_and_target(clean_frame, cfg)
    first, second = make_splits(X, y, cfg), make_splits(X, y, cfg)
    np.testing.assert_array_equal(first.X_test.index, second.X_test.index)


# --- the committed demo fixture --------------------------------------------


def test_demo_csv_round_trips_through_the_loader(demo_csv, cfg: Config):
    """The committed fixture must stay loadable as the schema evolves."""
    cleaned, report = clean_frame(read_raw(demo_csv, cfg))
    schema.validate_frame(cleaned, require_target=True)
    assert len(cleaned) == report.rows_out == 120


def test_demo_csv_is_unmistakably_synthetic(demo_csv, cfg: Config):
    """Guards the claim in tests/fixtures/README.md: no row can pass as real."""
    raw = read_raw(demo_csv, cfg)
    assert raw[schema.ID_COLUMN].str.startswith("DEMO-").all()
