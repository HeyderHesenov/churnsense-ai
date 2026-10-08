"""Cached data access for the dashboard.

Streamlit reruns the whole script on every widget interaction, so anything
expensive has to be cached or the app becomes unusable. Two different caches
are used on purpose:

* ``cache_resource`` for the model - one object, shared, never copied.
* ``cache_data`` for frames - copied per session, which is what makes
  filtering safe.

Nothing here computes a prediction itself; it calls
``churnsense.models.predict`` like every other consumer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from churnsense import analysis as an
from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.data.loader import load_clean
from churnsense.data.split import DataSplits, make_splits
from churnsense.exceptions import ChurnSenseError
from churnsense.models.predict import Predictor, load_predictor


@st.cache_resource(show_spinner=False)
def config() -> Config:
    return load_config()


@st.cache_resource(show_spinner="Loading model...")
def predictor() -> Predictor | None:
    """The shipped model, or ``None`` when no artifact exists."""
    try:
        return load_predictor()
    except ChurnSenseError:
        return None


@st.cache_data(show_spinner="Loading customers...")
def customers() -> pd.DataFrame | None:
    """The cleaned dataset with derived display columns, or ``None`` if absent."""
    try:
        frame, _ = load_clean()
    except ChurnSenseError:
        return None
    return frame.assign(
        tenure_bucket=an.tenure_bucket(frame["tenure"]),
        charge_band=an.charge_band(frame["MonthlyCharges"]),
        outcome=frame[schema.TARGET].map({0: "Retained", 1: "Churned"}),
    )


@st.cache_data(show_spinner="Scoring customers...")
def scored() -> pd.DataFrame | None:
    """Every customer with their churn probability and risk band.

    Scored through the same ``Predictor`` the API uses, so a number read off
    this dashboard and a number returned by ``POST /predict`` for the same
    customer are the same number.
    """
    frame, model = customers(), predictor()
    if frame is None or model is None:
        return None
    predictions = model.predict_frame(frame)
    return pd.concat([frame, predictions, _partition_labels(frame.index)], axis=1)


@st.cache_data(show_spinner=False)
def splits() -> DataSplits | None:
    """Train/validation/test partitions, for the performance section."""
    frame = customers()
    if frame is None:
        return None
    from churnsense.data.loader import features_and_target

    X, y = features_and_target(frame)
    return make_splits(X, y)


def _partition_labels(index: pd.Index) -> pd.Series:
    """Label each row with the partition it belongs to.

    Shown in the explorer so a user can see at a glance whether a customer was
    part of training - which matters a great deal when judging how impressive a
    confident, correct prediction looks.

    Not cached: ``st.cache_data`` hashes its arguments and cannot hash a
    pandas Index. The work is three set intersections over an already-cached
    split, so caching would buy nothing anyway; it is computed once inside
    ``scored()`` and travels with the frame.
    """
    labels = pd.Series("unknown", index=index, dtype="object", name="partition")
    parts = splits()
    if parts is not None:
        for name, frame in (
            ("train", parts.X_train),
            ("validation", parts.X_val),
            ("test", parts.X_test),
        ):
            labels.loc[labels.index.intersection(frame.index)] = name
    return labels


@st.cache_data(show_spinner=False)
def shap_importance() -> pd.DataFrame | None:
    from churnsense.explainability.shap_explain import load_cached_importance

    return load_cached_importance()


@st.cache_data(show_spinner=False)
def report_json(name: str) -> dict | None:
    """Read a cached JSON artifact, tolerating its absence."""
    path: Path = config().paths.artifacts_dir / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def report_csv(name: str) -> pd.DataFrame | None:
    path: Path = config().paths.reports_dir / name
    return pd.read_csv(path) if path.is_file() else None
