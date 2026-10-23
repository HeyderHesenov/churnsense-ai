"""Cached data access for the dashboard.

Streamlit reruns the whole script on every widget interaction, so anything
expensive has to be cached or the app becomes unusable. Two different caches
are used on purpose:

* ``cache_resource`` for the model - one object, shared, never copied.
* ``cache_data`` for frames - copied per session, which is what makes
  filtering safe.

Nothing here computes a prediction or decides which outcome may be shown: the
book, its partitions and the withheld rule live in ``churnsense.book``, shared
with the API, and this module only caches them for Streamlit. Sections get an
outcome only through ``labelled()``, so a section that forgot it fails with a
``KeyError`` instead of quietly counting withheld rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from churnsense import book as books
from churnsense.book import Book, score_book
from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.data.loader import load_clean
from churnsense.exceptions import ChurnSenseError
from churnsense.models.predict import Predictor, load_predictor

#: How the dashboard words each outcome code from ``churnsense.book``.
OUTCOMES = {0: "Retained", 1: "Churned"}
WITHHELD = "Withheld"
OUTCOME_WORDS = {books.STAYED: OUTCOMES[0], books.CHURNED: OUTCOMES[1], books.WITHHELD: WITHHELD}


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


def labelled(frame: pd.DataFrame, *partitions: str, shown: pd.Series | None = None) -> pd.DataFrame:
    """``churnsense.book.labelled`` with ``shown`` defaulting to this session's ``outcomes()``."""
    return books.labelled(frame, *partitions, shown=outcomes() if shown is None else shown)


def outcome_words(frame: pd.DataFrame, shown: pd.Series | None = None) -> pd.Series:
    """Each row's outcome in the dashboard's words: Churned, Retained or Withheld."""
    shown = outcomes() if shown is None else shown
    return books.outcome_codes(frame, shown).map(OUTCOME_WORDS)


@st.cache_data(show_spinner=False)
def shap_background() -> pd.DataFrame | None:
    """The reference customers every SHAP explanation is measured against.

    The same training-partition sample ``make explain`` uses - drawn from the
    trusted split in its own order, with the model's own columns - so
    "relative to an average customer" means one thing on every page.
    """
    from churnsense.explainability.shap_explain import background_sample

    book, model = _book(), predictor()
    if book is None or model is None:
        return None
    train = books.training_features(book, model.feature_columns)
    return None if train is None else background_sample(train, config().random_seed)


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
