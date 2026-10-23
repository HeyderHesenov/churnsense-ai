"""What the service loads once and serves many times: the model and the book.

Both are loaded lazily, on the first request that needs them, and kept for the
process lifetime. Neither is ever replaced by a placeholder: a missing model is
a 503 ``model_unavailable`` and a missing dataset a 503 ``dataset_unavailable``,
because a figure invented to fill a page is worse than an honest outage.

The locations are module-level so tests can point the service at temporary
directories. They are never taken from a request: unpickling is equivalent to
code execution, so the path must not be caller-controlled.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from churnsense import insights
from churnsense.book import Book, load_book, training_features
from churnsense.config import Business, Config
from churnsense.data import schema
from churnsense.exceptions import ChurnSenseError, ModelNotAvailableError
from churnsense.explainability.shap_explain import (
    PERMUTATIONS,
    CustomerExplanation,
    background_sample,
    explain_customer,
)
from churnsense.models.predict import Predictor, load_predictor

logger = logging.getLogger(__name__)

#: The artifact directory; ``None`` means the configured one.
ARTIFACTS_DIR: Path | None = None
#: The reports directory; ``None`` means the configured one.
REPORTS_DIR: Path | None = None
#: How the book is built from the served model. Replaced in tests, which must
#: never read the real dataset.
BOOK_LOADER: Callable[[Predictor], Book] = load_book

#: Explanations kept per process: a page of the retention queue is eight, and
#: one customer is often explained again from another page.
EXPLANATION_CACHE_SIZE = 512
#: Distinct sets of business assumptions whose sweep is kept: slider positions.
ECONOMICS_CACHE_SIZE = 64
#: Explanation requests allowed in flight at once. They queue on the explainer
#: lock, so more would only park threads of the server's shared pool; past
#: this a request is turned away with 503 and asked to retry.
EXPLAIN_SLOTS = 3
#: After the dataset fails to load, how long to answer "unavailable" from
#: memory before trying - and logging - again.
RETRY_AFTER_FAILURE_SECONDS = 30.0


@lru_cache(maxsize=1)
def get_predictor() -> Predictor:
    """The loaded model, cached for the process lifetime."""
    return load_predictor(ARTIFACTS_DIR)


def require_predictor() -> Predictor:
    try:
        return get_predictor()
    except ModelNotAvailableError as exc:
        logger.error("request refused: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="No trained model is available. Train one with `make train`.",
        ) from exc


class Unavailable(HTTPException):
    """A 503 with its own stable ``error`` code, so a client can tell why."""

    def __init__(self, error: str, detail: str, retry_after: int | None = None) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        super().__init__(status_code=503, detail=detail, headers=headers)
        self.error = error


class DatasetUnavailableError(Unavailable):
    """The model is loaded but the customers it should score are not."""

    def __init__(self) -> None:
        super().__init__(
            "dataset_unavailable",
            "The customer dataset is not available. Download it with `make data`.",
        )


@dataclass(eq=False)
class Dashboard:
    """The served model, its book, and the computations pages share."""

    model: Predictor
    book: Book
    reports_dir: Path
    shap_cache: Path
    #: The book plus each row's outcome code and monthly revenue at risk.
    frame: pd.DataFrame = field(init=False)
    #: The 100 training customers every explanation is measured against.
    background: pd.DataFrame | None = field(init=False)
    _explanations: OrderedDict[tuple, CustomerExplanation] = field(
        init=False, default_factory=OrderedDict
    )
    _economics: OrderedDict[Business, insights.Economics | None] = field(
        init=False, default_factory=OrderedDict
    )
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    #: Each customer's row label in ``frame``, for constant-time lookups by ID.
    _positions: dict[str, Any] = field(init=False)
    _memo: dict[str, Any] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.frame = insights.with_outcomes(self.book)
        self._positions = dict(zip(self.frame[schema.ID_COLUMN], self.frame.index, strict=True))
        train = training_features(self.book, self.model.feature_columns)
        self.background = (
            None if train is None else background_sample(train, self.model.config.random_seed)
        )

    @property
    def config(self) -> Config:
        return self.model.config

    def memo(self, key: str, compute: Callable[[], Any]) -> Any:
        """``compute()``, once per dashboard: the book never changes while it is served."""
        with self._lock:
            if key in self._memo:
                return self._memo[key]
        value = compute()
        with self._lock:
            return self._memo.setdefault(key, value)

    def customer(self, customer_id: str) -> pd.Series | None:
        """One customer's row of ``frame``, or ``None`` if the book has no such ID."""
        position = self._positions.get(customer_id)
        return None if position is None else self.frame.loc[position]

    def economics(self, business: Business) -> insights.Economics | None:
        """The validation sweep under ``business``; the most recent ones are kept."""
        with self._lock:
            if business in self._economics:
                self._economics.move_to_end(business)
                return self._economics[business]
        result = insights.economics(self.book, business)
        with self._lock:
            self._economics[business] = result
            while len(self._economics) > ECONOMICS_CACHE_SIZE:
                self._economics.popitem(last=False)
        return result

    def explain(self, record: pd.DataFrame) -> CustomerExplanation:
        """Every contribution to one customer's served probability.

        Each record is explained on its own, with the fixed seed: an explainer
        run over several rows draws each row's orderings from one random
        stream, so a customer's contributions would depend on who was explained
        beside them. The cache is keyed on the record the model actually scores.
        """
        if self.background is None:
            raise ExplanationsUnavailableError
        key = tuple(record.iloc[0].tolist())
        with self._lock:
            if key in self._explanations:
                self._explanations.move_to_end(key)
                return self._explanations[key]
        explanation = explain_customer(
            self.model.model,
            record,
            row=0,
            top_k=record.shape[1],
            background=self.background,
            seed=self.config.random_seed,
            permutations=PERMUTATIONS,
        )
        with self._lock:
            self._explanations[key] = explanation
            while len(self._explanations) > EXPLANATION_CACHE_SIZE:
                self._explanations.popitem(last=False)
        return explanation


class ExplanationsUnavailableError(Unavailable):
    """No verified training partition, so no population to explain against."""

    def __init__(self) -> None:
        super().__init__(
            "explanations_unavailable",
            "Explanations need the training partition the model was trained on, and it "
            "could not be verified. Retrain with `make train`.",
        )


_explain_slots = threading.BoundedSemaphore(EXPLAIN_SLOTS)


@contextmanager
def explanation_slot() -> Iterator[None]:
    """Hold one of ``EXPLAIN_SLOTS`` while explaining; 503 at once if none is free."""
    if not _explain_slots.acquire(blocking=False):
        raise Unavailable(
            "explanations_busy",
            "Explanations are being computed for other requests. Try again shortly.",
            retry_after=2,
        )
    try:
        yield
    finally:
        _explain_slots.release()


_dashboard_lock = threading.Lock()
#: The model and loader the current dashboard was built for, and the dashboard.
_built: tuple[Predictor, Callable[[Predictor], Book], Dashboard] | None = None
#: The model, loader and time of the last failed build, so it is not retried
#: (and logged) on every request.
_failed: tuple[Predictor, Callable[[Predictor], Book], float] | None = None


def get_dashboard() -> Dashboard:
    """The dashboard for the served model, built on first use; 503 if it cannot be."""
    model = require_predictor()
    return _dashboard_for(model, BOOK_LOADER)


def optional_dashboard() -> Dashboard | None:
    """``get_dashboard()``, or ``None`` when the model or the dataset is missing."""
    try:
        return get_dashboard()
    except HTTPException:
        return None


def _dashboard_for(model: Predictor, loader: Callable[[Predictor], Book]) -> Dashboard:
    """One dashboard per model and loader object: a reloaded model or a test's
    loader gets a fresh book, never one built for another."""
    global _built, _failed
    with _dashboard_lock:
        if _built is not None and _built[0] is model and _built[1] is loader:
            return _built[2]
        if (
            _failed is not None
            and _failed[0] is model
            and _failed[1] is loader
            and time.monotonic() - _failed[2] < RETRY_AFTER_FAILURE_SECONDS
        ):
            raise DatasetUnavailableError
        try:
            dashboard = _build(model, loader)
        except DatasetUnavailableError:
            _failed = (model, loader, time.monotonic())
            raise
        _built, _failed = (model, loader, dashboard), None
        return dashboard


def _build(model: Predictor, loader: Callable[[Predictor], Book]) -> Dashboard:
    try:
        book = loader(model)
    except ChurnSenseError as exc:
        logger.error("dataset unavailable: %s", exc)
        raise DatasetUnavailableError from exc
    reports = REPORTS_DIR if REPORTS_DIR is not None else model.config.paths.reports_dir
    return Dashboard(
        model=model, book=book, reports_dir=reports, shap_cache=model.source / "shap_global.json"
    )


def reset() -> None:
    """Forget the loaded model and book; the next request loads them again."""
    global _built, _failed
    get_predictor.cache_clear()
    with _dashboard_lock:
        _built = _failed = None
