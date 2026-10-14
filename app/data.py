"""Cached data access for the dashboard.

Streamlit reruns the whole script on every widget interaction, so anything
expensive has to be cached or the app becomes unusable. Two different caches
are used on purpose:

* ``cache_resource`` for the model - one object, shared, never copied.
* ``cache_data`` for frames - copied per session, which is what makes
  filtering safe.

Nothing here computes a prediction itself; it calls
``churnsense.models.predict`` like every other consumer.

**The test partition's outcomes never reach a view.** This docstring is the
one statement of the rule; the code below is its only implementation.

* ``scored()`` - the frame every section receives - has every customer's
  features, score and partition, and *no outcome column at all*.
* The outcomes the dashboard may show live apart, in ``outcomes()``: the
  target for training and validation rows only. Test-partition labels are
  never copied into it.
* ``labelled(frame)`` joins the two for exactly those rows. It is the only way
  a section gets an outcome, so a section that forgot it would fail with a
  ``KeyError`` - not quietly count withheld rows as retained ones.
* Partitions are rebuilt from the data and configuration, and trusted only
  when the data is the file the served model was trained on (its SHA-256)
  and the rows fall where the model's recorded split digest says. Otherwise -
  missing, malformed or different provenance alike - every row is
  "unknown", no outcome is shown, and ``withheld_reason()`` says why.

The final evaluation (``make evaluate CONFIRM=yes``) is the one place a figure
is computed from the test labels. Rebuilding the stratified split here reads
every label, as training does, only to put each row where training put it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import streamlit as st

from churnsense import analysis as an
from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.data.loader import features_and_target, load_clean
from churnsense.data.split import DataSplits, check_split, make_splits
from churnsense.exceptions import ChurnSenseError, DataError
from churnsense.models.predict import Predictor, load_predictor

logger = logging.getLogger(__name__)

#: Partitions whose historical outcome the dashboard may show.
SHOWN_PARTITIONS: tuple[str, ...] = ("train", "validation")
OUTCOMES = {0: "Retained", 1: "Churned"}
WITHHELD = "Withheld"

#: Why the partitions could not be verified, one reason per state. Fixed text,
#: never an exception's message, so no path or internal name reaches a page.
WITHHELD_REASONS: dict[str, str] = {
    "no dataset hash": (
        "the model's metadata records no hash of the dataset it was trained on, so "
        "this dataset cannot be matched to it. Retrain with `make train`."
    ),
    "invalid dataset hash": (
        "the model's metadata holds a dataset hash that is not a valid SHA-256, so "
        "this dataset cannot be matched to it. Retrain with `make train`."
    ),
    "dataset unhashed": (
        "the current dataset's hash is unknown, so it cannot be matched to the one "
        "the model was trained on. Re-download it with `make data`."
    ),
    "dataset differs": (
        "the dataset differs from the one the model was trained on. Retrain with `make train`."
    ),
    "no split digest": (
        "the model's metadata records no split digest, so the partitions cannot be "
        "matched to the ones it was trained on. Retrain with `make train`."
    ),
    "invalid split digest": (
        "the model's metadata holds a split digest that is not a valid SHA-256, so the "
        "partitions cannot be matched to the ones it was trained on. Retrain with `make train`."
    ),
    "split failed": (
        "the dataset could not be split the way the model's training data was. "
        "Retrain with `make train`."
    ),
    "split differs": (
        "the training, validation and test split differs from the one the model was "
        "trained on (the seed or split sizes changed). Retrain with `make train`."
    ),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class Book(NamedTuple):
    """The dashboard's view of the customers, built once by ``score_book``."""

    #: Every customer: features, display buckets, score and partition. No outcome.
    frame: pd.DataFrame
    #: The target of training and validation customers only, keyed by customerID.
    outcomes: pd.Series
    #: The training partition in split order, for the SHAP background; empty if untrusted.
    train_index: pd.Index
    #: Why no partition could be trusted, or ``None``.
    withheld: str | None


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
def _dataset() -> tuple[pd.DataFrame, str | None] | None:
    """The cleaned dataset - every label included - and the SHA-256 of the file read."""
    try:
        frame, cleaning = load_clean()
    except ChurnSenseError:
        return None
    return frame, cleaning.source_sha256


# st.cache_data keys a function on its own source only, so an edit to
# score_book on a running dev server needs "Clear cache" or a restart.
@st.cache_data(show_spinner="Scoring customers...")
def _book() -> Book | None:
    dataset, model = _dataset(), predictor()
    if dataset is None or model is None:
        return None
    return score_book(*dataset, model)


def scored() -> pd.DataFrame | None:
    """Every customer, scored, without outcomes: ``Book.frame``."""
    book = _book()
    return None if book is None else book.frame


def outcomes() -> pd.Series:
    """The outcomes that may be shown: ``Book.outcomes`` (empty without a book)."""
    book = _book()
    empty = pd.Series(dtype="int64", name=schema.TARGET, index=pd.Index([], name=schema.ID_COLUMN))
    return empty if book is None else book.outcomes


def withheld_reason() -> str | None:
    """Why every outcome is withheld, or ``None`` when partitions are trusted."""
    book = _book()
    return None if book is None else book.withheld


def score_book(customers: pd.DataFrame, data_sha256: str | None, model: Predictor) -> Book:
    """Score every customer and separate the outcomes that may be shown.

    Scored through the same ``Predictor`` the API uses, so a number read off
    this dashboard and a number returned by ``POST /predict`` for the same
    customer are the same number. The partitions are rebuilt from
    ``customers`` itself, so they cannot be paired with another frame.
    """
    parts, withheld = _verified_split(customers, data_sha256, model.meta)
    partition = pd.Series("unknown", index=customers.index, dtype="object", name="partition")
    if parts is None:
        logger.warning("outcomes withheld: %s", withheld)
    else:
        for name, part in (
            ("train", parts.X_train),
            ("validation", parts.X_val),
            ("test", parts.X_test),
        ):
            partition.loc[part.index] = name

    features = customers.drop(columns=schema.TARGET)
    frame = pd.concat(
        [
            features.assign(
                tenure_bucket=an.tenure_bucket(features["tenure"]),
                charge_band=an.charge_band(features["MonthlyCharges"]),
            ),
            model.predict_frame(features),
            partition,
        ],
        axis=1,
    )
    shown = customers.loc[partition.isin(SHOWN_PARTITIONS)].set_index(schema.ID_COLUMN)
    train_index = pd.Index([]) if parts is None else parts.X_train.index
    return Book(frame, shown[schema.TARGET], train_index, withheld)


def _verified_split(
    customers: pd.DataFrame, data_sha256: str | None, meta: dict
) -> tuple[DataSplits | None, str | None]:
    """The partitions the served model was trained on, or why they cannot be trusted.

    Fails closed: provenance that is missing, malformed or different withholds
    every outcome, each state with its own ``WITHHELD_REASONS`` entry.
    """
    recorded = meta.get("data_sha256")
    if recorded is None:
        return None, WITHHELD_REASONS["no dataset hash"]
    if not _is_sha256(recorded):
        return None, WITHHELD_REASONS["invalid dataset hash"]
    if data_sha256 is None:
        return None, WITHHELD_REASONS["dataset unhashed"]
    if data_sha256 != recorded:
        return None, WITHHELD_REASONS["dataset differs"]
    digest = meta.get("split_digest")
    if digest is None:
        return None, WITHHELD_REASONS["no split digest"]
    if not _is_sha256(digest):
        return None, WITHHELD_REASONS["invalid split digest"]
    try:
        X, y = features_and_target(customers)
        parts = make_splits(X, y, log=False)
    except (DataError, ValueError) as exc:
        # The detail (a column, a repeated ID) is for the server log, not the page.
        logger.warning("partitions could not be rebuilt: %s", exc)
        return None, WITHHELD_REASONS["split failed"]
    try:
        check_split(parts, meta)  # the digest is present and well formed: only a mismatch
    except DataError:
        return None, WITHHELD_REASONS["split differs"]
    return parts, None


def _is_sha256(value: object) -> bool:
    """A hex SHA-256 as ``hashlib`` writes it: 64 lower-case hex digits."""
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def labelled(frame: pd.DataFrame, *partitions: str, shown: pd.Series | None = None) -> pd.DataFrame:
    """The rows of ``frame`` whose outcome may be read, with the outcome attached.

    Training and validation by default; name one to narrow it. ``shown``
    defaults to ``outcomes()``. Every outcome aggregate on the dashboard is
    computed on this, so withheld rows count neither in a rate nor in its
    denominator.
    """
    chosen = partitions or SHOWN_PARTITIONS
    if not set(chosen) <= set(SHOWN_PARTITIONS):
        raise ValueError(f"outcomes are shown only for {SHOWN_PARTITIONS}, not {chosen}")
    shown = outcomes() if shown is None else shown
    rows = frame[frame["partition"].isin(chosen)]
    target = rows[schema.ID_COLUMN].map(shown)
    if target.isna().any():
        raise KeyError("rows with no shown outcome: the frame is not from this book")
    return rows.assign(**{schema.TARGET: target.astype(int)})


@st.cache_data(show_spinner=False)
def shap_background() -> pd.DataFrame | None:
    """The reference customers every SHAP explanation is measured against.

    The same training-partition sample ``make explain`` uses - drawn from the
    trusted split in its own order, with the model's own columns - so
    "relative to an average customer" means one thing on every page.
    """
    from churnsense.explainability.shap_explain import background_sample

    book, model = _book(), predictor()
    if book is None or model is None or book.train_index.empty:
        return None
    train = book.frame.loc[book.train_index, model.feature_columns]
    return background_sample(train, config().random_seed)


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
