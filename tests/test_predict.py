"""Tests for the single inference path.

The dashboard, the API and batch scoring must agree by construction, not by
coincidence. They agree because they all call this module - and these tests
pin the behaviour that makes that safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.exceptions import ModelNotAvailableError, SchemaValidationError
from churnsense.models.predict import Predictor, load_predictor, validate_upload
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
    with pytest.raises(SchemaValidationError, match="rows"):
        validate_upload(demo_csv.read_bytes(), cfg, max_rows=10)


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

    classifier = predictor.model.named_steps["classifier"]
    assert isinstance(classifier, ProbabilityClamp)
    assert classifier.epsilon == pytest.approx(predictor.epsilon)


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
