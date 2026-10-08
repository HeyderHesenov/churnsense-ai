"""Integration tests for the FastAPI service.

The service is exercised through a real HTTP client against a real trained
artifact - no mocked model. A mocked prediction would test the plumbing and
miss the thing that matters most here: that the API returns exactly what the
dashboard and batch path return.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from churnsense.config import Config
from churnsense.data.loader import features_and_target
from churnsense.data.split import make_splits
from churnsense.models.predict import load_predictor
from churnsense.models.train import save_artifact, train_all


@pytest.fixture(scope="module")
def artifacts(clean_frame, cfg: Config, tmp_path_factory):
    X, y = features_and_target(clean_frame, cfg)
    splits = make_splits(X, y, cfg)
    outcome = train_all(splits.X_train, splits.y_train, splits.X_val, splits.y_val, cfg=cfg)
    directory = tmp_path_factory.mktemp("api_artifacts")
    save_artifact(outcome, directory, cfg)
    return directory


@pytest.fixture(scope="module")
def client(artifacts, monkeypatch_session):
    from churnsense.api import main as api_main

    monkeypatch_session.setattr(api_main, "ARTIFACTS_DIR", artifacts)
    with TestClient(api_main.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def payload(clean_frame, cfg: Config) -> dict:
    record = features_and_target(clean_frame, cfg)[0].iloc[0].to_dict()
    return {k: (int(v) if k == "tenure" else v) for k, v in record.items()}


def test_health_reports_a_loaded_model(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_model_info_describes_the_artifact(client):
    body = client.get("/model-info").json()
    assert body["model_key"]
    assert 0.0 <= body["threshold"] <= 1.0
    assert body["feature_columns"]
    assert "validation_metrics" in body


def test_model_info_does_not_leak_local_paths(client):
    assert "/Users/" not in client.get("/model-info").text


def test_predict_returns_the_documented_shape(client, payload):
    body = client.post("/predict", json=payload).json()
    assert set(body) >= {"churn_probability", "risk_band", "flagged", "threshold"}
    assert 0.0 <= body["churn_probability"] <= 1.0
    assert isinstance(body["flagged"], bool)


def test_api_agrees_with_the_library_to_the_last_digit(client, payload, artifacts):
    """The contract that makes dashboard, API and batch interchangeable."""
    api_probability = client.post("/predict", json=payload).json()["churn_probability"]
    local = load_predictor(artifacts).predict_one(payload)
    assert api_probability == pytest.approx(local.churn_probability, abs=1e-12)


def test_predict_rejects_a_missing_field(client, payload):
    body = dict(payload)
    del body["Contract"]
    response = client.post("/predict", json=body)
    assert response.status_code == 422
    assert "Contract" in response.text


def test_predict_rejects_an_out_of_range_number(client, payload):
    response = client.post("/predict", json={**payload, "tenure": -3})
    assert response.status_code == 422


def test_predict_rejects_an_unknown_category(client, payload):
    response = client.post("/predict", json={**payload, "Contract": "Lifetime"})
    assert response.status_code == 422


def test_validation_errors_do_not_leak_internals(client, payload):
    text = client.post("/predict", json={**payload, "tenure": "abc"}).text
    assert "Traceback" not in text
    assert "/Users/" not in text


def test_batch_scores_every_row(client, demo_csv):
    response = client.post(
        "/predict/batch",
        files={"file": ("customers.csv", demo_csv.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["n_scored"] == 120
    assert len(body["predictions"]) == 120
    assert all(0.0 <= p["churn_probability"] <= 1.0 for p in body["predictions"])


def test_batch_agrees_with_single_prediction(client, demo_csv, artifacts, cfg: Config):
    """Row-by-row equality between the batch endpoint and the single endpoint."""
    from churnsense.models.predict import validate_upload

    predictions = client.post(
        "/predict/batch", files={"file": ("c.csv", demo_csv.read_bytes(), "text/csv")}
    ).json()["predictions"]
    frame = validate_upload(demo_csv.read_bytes(), cfg)
    expected = load_predictor(artifacts).predict_frame(frame)

    for position in (0, 50, 119):
        assert predictions[position]["churn_probability"] == pytest.approx(
            expected["churn_probability"].iloc[position], abs=1e-12
        )


def test_batch_rejects_a_non_csv_upload(client):
    response = client.post(
        "/predict/batch", files={"file": ("x.csv", b"\x00\x01 nonsense", "text/csv")}
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_batch_rejects_an_oversized_upload(client, cfg: Config):
    payload = b"a," * (cfg.api.max_upload_bytes // 2 + 10)
    response = client.post("/predict/batch", files={"file": ("big.csv", payload, "text/csv")})
    assert response.status_code == 413


def test_service_reports_unavailable_rather_than_guessing(tmp_path, monkeypatch):
    """With no artifact the API must fail loudly, never return a default score."""
    from churnsense.api import main as api_main

    monkeypatch.setattr(api_main, "ARTIFACTS_DIR", tmp_path / "empty")
    api_main.get_predictor.cache_clear()
    with TestClient(api_main.app) as unloaded:
        assert unloaded.get("/health").json()["model_loaded"] is False
        assert unloaded.post("/predict", json={}).status_code in (422, 503)


def test_openapi_schema_is_served(client):
    schema = client.get("/openapi.json").json()
    assert "/predict" in schema["paths"]
    assert "/health" in schema["paths"]


# --- the API's contract must not drift from the model's ---------------------


def test_api_category_vocabularies_match_the_training_contract():
    """The aliases in api/schemas.py are hand-written; this is what keeps them true.

    If a category is added to the training contract and not to the API, the
    service would reject input the model handles perfectly well - and the
    failure would surface as a confusing 422 in production, not here.
    """
    import typing

    from churnsense.api.schemas import CATEGORY_ALIASES
    from churnsense.config import load_config
    from churnsense.data import schema

    cfg = load_config()
    model_inputs = set(
        schema.model_input_columns(cfg.features.drop_columns, cfg.features.include_total_charges)
    )
    # Only the categoricals the model actually consumes: `gender` is in the raw
    # contract but is dropped as a protected attribute, so the API has no field
    # for it and must not grow one.
    expected = {c for c in schema.ALLOWED_CATEGORIES if c in model_inputs}

    assert set(CATEGORY_ALIASES) == expected
    for column, alias in CATEGORY_ALIASES.items():
        assert set(typing.get_args(alias)) == set(schema.ALLOWED_CATEGORIES[column]), column


def test_api_accepts_every_model_input_column():
    """Every feature the pipeline consumes must be a field on the request model."""
    from churnsense.api.schemas import CustomerFeatures
    from churnsense.config import load_config
    from churnsense.data import schema

    cfg = load_config()
    required = schema.model_input_columns(
        cfg.features.drop_columns, cfg.features.include_total_charges
    )
    assert set(required) == set(CustomerFeatures.model_fields)
