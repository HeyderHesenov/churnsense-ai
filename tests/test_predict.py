"""Tests for the single inference path.

The dashboard, the API and batch scoring must agree by construction, not by
coincidence. They agree because they all call this module - and these tests
pin the behaviour that makes that safe.
"""

from __future__ import annotations

import csv
import dataclasses

import numpy as np
import pandas as pd
import pytest

from churnsense.config import Config
from churnsense.data import schema
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.exceptions import ModelNotAvailableError, SchemaValidationError
from churnsense.models.predict import (
    Predictor,
    _read_contract_columns,
    ignored_columns,
    load_predictor,
    validate_upload,
)
from churnsense.models.train import save_artifact, train_all


@pytest.fixture(scope="module")
def predictor(clean_frame, cfg: Config, tmp_path_factory) -> Predictor:
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    directory = tmp_path_factory.mktemp("artifacts")
    save_artifact(outcome, directory, cfg)
    return load_predictor(directory)


@pytest.fixture(scope="module")
def sample(clean_frame, cfg: Config) -> pd.DataFrame:
    return features_and_target(clean_frame, cfg)[0].head(25)


def test_predictor_exposes_its_operating_point(predictor: Predictor):
    assert 0.0 <= predictor.threshold <= 1.0
    assert predictor.feature_columns
    assert predictor.model_key


def test_predict_frame_returns_one_row_per_input(predictor: Predictor, sample):
    out = predictor.predict_frame(sample)
    assert len(out) == len(sample)
    assert list(out.index) == list(sample.index)


def test_predict_frame_columns_are_the_documented_contract(predictor: Predictor, sample):
    out = predictor.predict_frame(sample)
    assert list(out.columns) == ["churn_probability", "risk_band", "flagged"]
    assert out["churn_probability"].between(0, 1).all()
    assert out["flagged"].dtype == bool


def test_flagged_follows_the_artifact_threshold(predictor: Predictor, sample):
    out = predictor.predict_frame(sample)
    expected = out["churn_probability"] >= predictor.threshold
    pd.testing.assert_series_equal(out["flagged"], expected, check_names=False)


def test_a_threshold_override_is_honoured(predictor: Predictor, sample):
    """The simulator slider changes the operating point without retraining."""
    assert predictor.predict_frame(sample, threshold=0.0)["flagged"].all()
    assert not predictor.predict_frame(sample, threshold=1.01)["flagged"].any()


def test_predict_one_matches_predict_frame_exactly(predictor: Predictor, sample):
    """The form, the API and batch scoring must not drift apart."""
    frame_result = predictor.predict_frame(sample)
    for position in (0, 7, 24):
        record = sample.iloc[position].to_dict()
        single = predictor.predict_one(record)
        assert single.churn_probability == pytest.approx(
            frame_result["churn_probability"].iloc[position], abs=1e-12
        )
        assert single.risk_band == frame_result["risk_band"].iloc[position]
        assert single.flagged == bool(frame_result["flagged"].iloc[position])


def test_column_order_does_not_change_the_prediction(predictor: Predictor, sample):
    shuffled = sample[list(sample.columns)[::-1]]
    np.testing.assert_allclose(
        predictor.predict_frame(shuffled)["churn_probability"],
        predictor.predict_frame(sample)["churn_probability"],
    )


def test_extra_columns_are_ignored_not_fatal(predictor: Predictor, sample):
    """Callers routinely pass customerID and the label along with the features."""
    noisy = sample.assign(customerID="X-1", Churn="No", internal_note="ignore me")
    np.testing.assert_allclose(
        predictor.predict_frame(noisy)["churn_probability"],
        predictor.predict_frame(sample)["churn_probability"],
    )


def test_a_missing_feature_column_is_a_clear_error(predictor: Predictor, sample):
    with pytest.raises(SchemaValidationError) as excinfo:
        predictor.predict_frame(sample.drop(columns=["Contract"]))
    assert "Contract" in str(excinfo.value)


def test_predict_one_rejects_an_incomplete_record(predictor: Predictor, sample):
    record = sample.iloc[0].to_dict()
    del record["tenure"]
    with pytest.raises(SchemaValidationError, match="tenure"):
        predictor.predict_one(record)


def test_an_empty_frame_is_rejected(predictor: Predictor, sample):
    with pytest.raises(SchemaValidationError, match="no rows"):
        predictor.predict_frame(sample.head(0))


def test_the_library_path_rejects_an_unknown_category(predictor: Predictor, sample):
    """The API refuses a typo; a direct predict_frame call must not score it either."""
    rogue = sample.copy()
    rogue.loc[rogue.index[0], "Contract"] = "Monthly"
    with pytest.raises(SchemaValidationError, match="Contract"):
        predictor.predict_frame(rogue)


def test_the_library_path_rejects_an_impossible_number(predictor: Predictor, sample):
    with pytest.raises(SchemaValidationError, match="tenure"):
        predictor.predict_frame(sample.assign(tenure=-1))


@pytest.mark.parametrize("value", [None, np.nan])
def test_the_library_path_rejects_a_missing_category(predictor: Predictor, sample, value):
    """Regression: the encoder ignored the unseen 'None' and scored the row as normal."""
    broken = sample.copy()
    broken["Contract"] = broken["Contract"].astype(object)
    broken.loc[broken.index[0], "Contract"] = value
    with pytest.raises(SchemaValidationError, match="'Contract' has 1 missing value"):
        predictor.predict_frame(broken)


@pytest.mark.parametrize("column", ["tenure", "MonthlyCharges", "TotalCharges"])
def test_the_library_path_rejects_a_missing_number(predictor: Predictor, sample, column: str):
    """A NaN reached the pipeline's median imputer and came back as a confident score."""
    broken = sample.copy()
    broken.loc[broken.index[0], column] = np.nan
    with pytest.raises(SchemaValidationError, match=f"'{column}' has 1 missing"):
        predictor.predict_frame(broken)


def test_validated_values_are_what_gets_scored(predictor: Predictor, sample):
    """Regression: an integer SeniorCitizen - what plain read_csv produces - and
    padded text passed validation, then crashed inside the encoder."""
    raw_shaped = sample.assign(
        SeniorCitizen=sample["SeniorCitizen"].astype(int),
        Contract=" " + sample["Contract"] + " ",
        tenure=sample["tenure"].astype(str),
    )
    np.testing.assert_allclose(
        predictor.predict_frame(raw_shaped)["churn_probability"],
        predictor.predict_frame(sample)["churn_probability"],
    )


def test_a_missing_artifact_never_yields_fake_predictions(tmp_path):
    with pytest.raises(ModelNotAvailableError):
        load_predictor(tmp_path / "does-not-exist")


def test_the_predictor_is_cached_per_directory(predictor: Predictor, tmp_path_factory):
    """Streamlit reruns and API requests must not reload the model every time."""
    assert load_predictor(predictor.source) is predictor


# --- upload validation ------------------------------------------------------


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode()


def test_a_valid_upload_is_accepted(cfg: Config, demo_csv):
    frame = validate_upload(demo_csv.read_bytes(), cfg)
    assert len(frame) == 120


def test_an_oversized_upload_is_refused_before_parsing(cfg: Config):
    payload = b"x" * (cfg.api.max_upload_bytes + 1)
    with pytest.raises(SchemaValidationError, match="too large"):
        validate_upload(payload, cfg)


def test_an_upload_with_too_many_rows_is_refused(cfg: Config, demo_csv):
    with pytest.raises(SchemaValidationError, match=r"more than 10 data rows \(about 121 lines\)"):
        validate_upload(demo_csv.read_bytes(), cfg, max_rows=10)


def test_the_row_limit_must_be_positive(cfg: Config, demo_csv):
    with pytest.raises(ValueError, match="max_rows"):
        validate_upload(demo_csv.read_bytes(), cfg, max_rows=0)


def _wide(columns: int) -> bytes:
    return ",".join(f"c{i}" for i in range(columns)).encode()


@pytest.fixture
def pandas_must_not_parse(monkeypatch):
    """Fail if an upload reaches pandas' CSV parser, whose header handling is quadratic."""

    def refuse(*args, **kwargs):
        raise AssertionError("an upload reached pd.read_csv")

    monkeypatch.setattr(pd, "read_csv", refuse)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        # Built per test, not at import: the first is 4.7 MB. It is the original
        # finding, more than five minutes of CPU before the fix.
        pytest.param(lambda: _wide(560_000) + b"\n,\n", "560,000 columns", id="header"),
        pytest.param(lambda: b"\n\n" + _wide(50_000), "50,000 columns", id="blank-lines-first"),
        pytest.param(lambda: b"   \t\r\n" + _wide(50_000), "50,000 columns", id="whitespace"),
        # These three got past a comma-counting guard that ran before pandas.
        pytest.param(lambda: b'"x\n",' + _wide(50_000), "50,001 columns", id="quoted-break"),
        pytest.param(lambda: b"\xef\xbb\xbf\n" + _wide(50_000), "50,000 columns", id="bom-blank"),
        pytest.param(
            lambda: b"customerID\n" + b"," * 500_000 + b"\n",
            "data row 1 has 500,001 fields but the header has 1",
            id="wide-first-data-row",
        ),
    ],
)
def test_a_wide_upload_is_refused_as_it_is_read(
    cfg: Config, pandas_must_not_parse, build, message: str
):
    payload = build()
    assert len(payload) <= cfg.api.max_upload_bytes, "must pass the size cap to test width"
    with pytest.raises(SchemaValidationError, match=message):
        validate_upload(payload, cfg)


def test_a_semicolon_separated_file_is_named_as_such(cfg: Config, demo_csv):
    """Excel in comma-decimal locales (az-AZ among them) saves ';'-separated CSV."""
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    with pytest.raises(SchemaValidationError, match="semicolon-separated"):
        validate_upload(frame.to_csv(index=False, sep=";").encode(), cfg)


@pytest.mark.parametrize(
    "prefix",
    [b"\n\xef\xbb\xbf", b"\n\xef\xbb\xbf\n", b"\xef\xbb\xbf\xef\xbb\xbf"],
    ids=["after-blank-line", "on-a-line-of-its-own", "doubled"],
)
@pytest.mark.parametrize("quoted", [False, True], ids=["plain-header", "quoted-header"])
def test_a_stray_bom_does_not_rename_the_first_column(
    cfg: Config, demo_csv, prefix: bytes, quoted: bool
):
    """Regression: the column was silently dropped as unknown, or the BOM became the header."""
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    body = frame.to_csv(index=False, quoting=csv.QUOTE_ALL if quoted else csv.QUOTE_MINIMAL)
    cleaned = validate_upload(prefix + body.encode(), cfg)
    assert "customerID" in cleaned.columns
    assert len(cleaned) == len(frame)


def test_ignored_columns_names_what_the_reader_drops(demo_csv):
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    frame["customer_id"] = "x"
    frame["notes"] = "y"
    assert ignored_columns(_csv(frame)) == ["customer_id", "notes"]
    assert ignored_columns(demo_csv.read_bytes()) == []


def test_an_unquoted_comma_in_the_first_row_is_refused_not_shifted(cfg: Config, demo_csv):
    """Regression: pandas read one surplus field there as an index, shifting every value."""
    lines = demo_csv.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b"DEMO-", b"DEMO,", 1)
    with pytest.raises(
        SchemaValidationError, match="data row 1 has 22 fields but the header has 21"
    ):
        validate_upload(b"\n".join(lines), cfg)


def test_a_wide_row_anywhere_is_refused_with_its_number(cfg: Config, demo_csv):
    payload = demo_csv.read_bytes() + b"," * 500_000 + b"\n"
    with pytest.raises(SchemaValidationError, match="data row 121 has 500,001 fields"):
        validate_upload(payload, cfg)


@pytest.mark.parametrize("junk", [b'""\n', b"\x0c\n", b"\xef\xbb\xbf\n\n"])
def test_a_junk_first_line_does_not_displace_the_header(cfg: Config, demo_csv, junk: bytes):
    """One tokenizer decides what a blank line is, so the header is never misread."""
    assert len(validate_upload(junk + demo_csv.read_bytes(), cfg)) == 120


def test_a_repeated_contract_column_is_refused(cfg: Config, demo_csv):
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    doubled = pd.concat([frame, frame[["tenure"]]], axis=1)
    with pytest.raises(SchemaValidationError, match="more than once: tenure"):
        validate_upload(_csv(doubled), cfg)


def test_an_export_with_many_extra_columns_is_scored_and_trimmed(cfg: Config, demo_csv):
    """Extra columns are dropped, not rejected - and never built into a frame."""
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    extra = pd.DataFrame({f"crm_{i}": "x" for i in range(300)}, index=frame.index)
    cleaned = validate_upload(_csv(pd.concat([frame, extra], axis=1)), cfg)
    assert len(cleaned) == len(frame)
    assert not any(column.startswith("crm_") for column in cleaned.columns)


def test_columns_the_served_model_needs_survive_a_config_ablation(cfg: Config, demo_csv):
    """Regression: the kept columns came from the config, not the contract.

    With `include_total_charges: false` in the config but the shipped model
    still using TotalCharges, an upload that carried it lost it on the way in.
    """
    ablated = dataclasses.replace(
        cfg, features=dataclasses.replace(cfg.features, include_total_charges=False)
    )
    assert "TotalCharges" in validate_upload(demo_csv.read_bytes(), ablated).columns


def test_a_file_of_only_unknown_columns_reports_the_missing_ones(cfg: Config):
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_upload(b"a,b\n1,2\n3,4\n", cfg)
    assert "missing columns" in " ".join(excinfo.value.problems)


@pytest.mark.parametrize("terminator", ["\r\n", "\r"])
def test_other_line_endings_are_read_like_newlines(cfg: Config, demo_csv, terminator: str):
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    payload = frame.to_csv(index=False, lineterminator=terminator).encode()
    assert len(validate_upload(payload, cfg)) == len(frame)


def test_the_reader_returns_what_pandas_would(cfg: Config, demo_csv):
    """Same strings, same order, for every contract column of a well-formed file."""
    ours = _read_contract_columns(demo_csv.read_bytes(), cfg, limit=10_000)
    theirs = pd.read_csv(demo_csv, dtype=str, keep_default_na=False, na_values=[])
    assert ours.equals(theirs[list(ours.columns)])


def test_an_upload_that_is_not_utf8_is_refused_without_a_crash(cfg: Config):
    payload = "customerID,Contract\nDEMO-1,Ünïcödé\n".encode("utf-16")
    with pytest.raises(SchemaValidationError, match="could not be read"):
        validate_upload(payload, cfg)


def test_an_over_long_field_is_named_as_the_reason(cfg: Config, demo_csv):
    lines = demo_csv.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b"DEMO-", b'"' + b"x" * 200_000 + b'"', 1)
    with pytest.raises(SchemaValidationError, match="field longer than"):
        validate_upload(b"\n".join(lines), cfg)


def test_an_upload_missing_feature_columns_is_refused(cfg: Config, demo_csv):
    frame = pd.read_csv(demo_csv, dtype=str).drop(columns=["Contract", "tenure"])
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_upload(_csv(frame), cfg)
    assert "Contract" in str(excinfo.value)


def test_an_unparseable_upload_is_refused_without_leaking_internals(cfg: Config):
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_upload(b"\x00\x01\x02 not a csv at all", cfg)
    message = str(excinfo.value)
    assert "Traceback" not in message
    assert "/Users/" not in message


def test_an_empty_upload_is_refused(cfg: Config):
    with pytest.raises(SchemaValidationError):
        validate_upload(b"", cfg)


def test_an_upload_with_the_real_file_s_blank_charges_quirk_is_accepted(cfg: Config, demo_csv):
    """Regression: validating before cleaning rejected the project's own data.

    The IBM file stores a blank in TotalCharges for zero-tenure customers.
    An earlier version validated the raw text first and reported those as
    'non-numeric values', so uploading the real dataset failed.
    """
    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    blanks = raw["TotalCharges"].str.strip().eq("")
    assert blanks.any(), "fixture must contain the blank-charges quirk"

    cleaned = validate_upload(_csv(raw), cfg)
    assert (cleaned.loc[blanks.to_numpy(), "TotalCharges"] == 0.0).all()


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"tenure": "abc"}, "'tenure' has 1 missing or non-numeric value(s)"),
        ({"tenure": ""}, "'tenure' has 1 missing or non-numeric value(s)"),
        ({"MonthlyCharges": "n/a"}, "'MonthlyCharges' has 1 missing or non-numeric value(s)"),
        ({"tenure": "5.5"}, "'tenure' has 1 non-integer value(s)"),
        # Was "recovered" as tenure x MonthlyCharges: a typo treated as a gap.
        (
            {"tenure": "24", "TotalCharges": "oops"},
            "'TotalCharges' has 1 missing or non-numeric value(s)",
        ),
        # A real gap, but on a billed customer only an estimate could fill it.
        (
            {"tenure": "24", "TotalCharges": ""},
            "'TotalCharges' has 1 missing or non-numeric value(s)",
        ),
    ],
)
def test_an_upload_never_scores_a_guessed_number(
    cfg: Config, demo_csv, changes: dict, problem: str
):
    """Regression: each of these was accepted, imputed, and scored with confidence."""
    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    for column, value in changes.items():
        raw.loc[0, column] = value
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_upload(_csv(raw), cfg)
    assert problem in excinfo.value.problems


def test_an_upload_keeps_every_row_in_file_order(cfg: Config, demo_csv):
    """Regression: exact duplicates were dropped, shifting every later row."""
    raw = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    doubled = pd.concat([raw.head(1), raw.head(4)], ignore_index=True)
    cleaned = validate_upload(_csv(doubled), cfg)
    assert list(cleaned.index) == list(range(5))
    assert (cleaned[schema.ID_COLUMN] == doubled[schema.ID_COLUMN]).all()


def test_an_upload_with_an_unknown_label_is_refused(cfg: Config, demo_csv):
    frame = pd.read_csv(demo_csv, dtype=str, keep_default_na=False)
    frame.loc[0, schema.TARGET] = "unknown"
    with pytest.raises(SchemaValidationError, match=schema.TARGET):
        validate_upload(_csv(frame), cfg)


def test_an_upload_without_the_target_or_id_is_accepted(cfg: Config, demo_csv):
    """A scoring file has no label and need not carry columns the model drops."""
    frame = pd.read_csv(demo_csv, dtype=str).drop(columns=["Churn", "customerID", "gender"])
    assert len(validate_upload(_csv(frame), cfg)) == 120


def test_an_upload_with_an_unknown_category_is_refused(cfg: Config, demo_csv):
    """A typo must not be silently bucketed into a confident prediction."""
    frame = pd.read_csv(demo_csv, dtype=str)
    frame.loc[0, "Contract"] = "Monthly"  # plausible typo for "Month-to-month"
    with pytest.raises(SchemaValidationError, match="Contract"):
        validate_upload(_csv(frame), cfg)


# --- calibration saturation -------------------------------------------------
#
# The clamp itself lives in the artifact and is tested in tests/test_clamp.py.
# What belongs here is the integration: that a Predictor loaded from disk
# reports the bound and never serves a probability outside it.


def test_the_predictor_reports_the_artifact_s_bound(predictor: Predictor):
    assert predictor.epsilon == pytest.approx(1.0 / (2 * predictor.meta["n_train"]))
    assert 0.0 < predictor.epsilon < 0.01


def test_a_loaded_predictor_never_serves_certainty(predictor: Predictor, sample):
    """End to end: train, persist, reload, score - no 0.0 and no 1.0."""
    served = predictor.predict_frame(sample)["churn_probability"]
    assert (served > 0.0).all()
    assert (served < 1.0).all()


def test_the_bound_survives_the_round_trip(predictor: Predictor):
    """The epsilon in the metadata must match the one baked into the pickle."""
    from churnsense.models.calibration_clamp import ProbabilityClamp

    assert isinstance(predictor.model, ProbabilityClamp)
    assert predictor.model.epsilon == pytest.approx(predictor.epsilon)


def test_a_file_without_total_charges_is_accepted_when_the_config_drops_it(cfg: Config, demo_csv):
    """Regression: a documented ablation produced HTTP 500.

    `features.include_total_charges: false` is advertised in config.yaml and
    the README as an ablation knob. With it set, a valid scoring file has no
    TotalCharges column — and clean_frame used to dereference that column
    unconditionally, raising a bare KeyError. KeyError is not a
    SchemaValidationError, so the API turned a perfectly good upload into a
    generic 500 instead of explaining anything.
    """
    import dataclasses

    ablated = dataclasses.replace(
        cfg, features=dataclasses.replace(cfg.features, include_total_charges=False)
    )
    frame = pd.read_csv(demo_csv, dtype=str).drop(columns=["TotalCharges"])

    cleaned = validate_upload(_csv(frame), ablated)

    assert len(cleaned) == 120
    assert "TotalCharges" not in cleaned.columns


def test_a_genuinely_missing_column_is_still_a_clean_rejection(cfg: Config, demo_csv):
    """The other half of the above: dropping a *required* column must 422."""
    frame = pd.read_csv(demo_csv, dtype=str).drop(columns=["Contract"])
    with pytest.raises(SchemaValidationError, match="Contract"):
        validate_upload(_csv(frame), cfg)
