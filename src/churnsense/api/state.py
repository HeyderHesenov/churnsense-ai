"""What the service loads once and serves many times: the model and the book.

Both are loaded lazily, on the first request that needs them, and kept for the
process lifetime. Neither is ever replaced by a placeholder: a missing model is
a 503 ``model_unavailable`` and a missing dataset a 503 ``dataset_unavailable``,
because a figure invented to fill a page is worse than an honest outage.

Which book is built is the configuration's stated choice
(``deployment.customer_records``), and the two never mix:

* ``dataset`` - the dataset's own customers, scored (``churnsense.book.load_book``),
  with every observed figure computed from them;
* ``synthetic`` - generated demo customers (``churnsense.demo.customers``), with
  every observed figure read from the published aggregates
  (``churnsense.evidence``). The dataset is never opened on this path, and when
  the demo data cannot be built the answer is 503 ``demo_data_unavailable`` -
  never the other book.

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
from churnsense.book import SYNTHETIC, Book, load_book, synthetic_book, training_features
from churnsense.config import Business, Config, Deployment
from churnsense.data import schema
from churnsense.demo.customers import demo_customers, records_sha256
from churnsense.evidence import (
    DEMO_EXPLANATIONS_FILE,
    EVIDENCE_FILE,
    Evidence,
    LiveEvidence,
    load_published,
)
from churnsense.exceptions import ChurnSenseError, DemoDataError, ModelNotAvailableError
from churnsense.explainability.shap_explain import (
    PERMUTATIONS,
    CustomerExplanation,
    background_sample,
    explain_customer,
    load_cached_explanations,
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
#: Where the service runs and whose rows it lists; ``None`` means the configured one.
DEPLOYMENT: Deployment | None = None
#: The dataset file a public demo must not find beside it; ``None`` means the configured one.
RAW_DATA_FILE: Path | None = None
#: How a public demo's customers are generated. Replaced in tests.
DEMO_GENERATOR: Callable[[], pd.DataFrame] = demo_customers

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
#: Batch uploads scored at once by this process, whoever sends them. Parsing,
#: validating and scoring 10,000 rows is seconds of CPU, and the deployed demo
#: has no authentication: per-client budgets stop one visitor, this stops many.
#: Past it a request is turned away at once with 429 - never queued, so no
#: backlog can build. Per process: the service runs one worker.
BATCH_SLOTS = 2
#: What a turned-away batch is told to wait, in seconds.
BATCH_RETRY_AFTER_SECONDS = 2


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


class DemoDataUnavailableError(Unavailable):
    """The public demo's own data could not be built. Nothing stands in for it."""

    def __init__(self) -> None:
        super().__init__("demo_data_unavailable", "The demo data is not available.")


@dataclass(eq=False)
class Dashboard:
    """The served model, its book, and the computations pages share."""

    model: Predictor
    book: Book
    reports_dir: Path
    #: The cached attributions the explainability page draws: of the book's own customers.
    shap_cache: Path
    #: What was observed of the dataset; computed from ``book`` unless given.
    evidence: Evidence | None = None
    #: Whose rows ``book`` holds: the ``dataset``'s, or ``synthetic`` demo customers'.
    records: str = "dataset"
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
        if self.evidence is None:
            self.evidence = LiveEvidence(self.book, self.model, self.shap_cache)
        columns = self.model.feature_columns
        # A book of generated customers has no training partition: an explanation
        # is then measured against the demo customers themselves, and says so.
        population = (
            self.book.frame[columns] if self.synthetic else training_features(self.book, columns)
        )
        self.background = (
            None
            if population is None
            else background_sample(population, self.model.config.random_seed)
        )

    @property
    def config(self) -> Config:
        return self.model.config

    @property
    def synthetic(self) -> bool:
        """Whether the customers listed are generated ones."""
        return self.records == "synthetic"

    @property
    def background_kind(self) -> str:
        """Who an explanation is measured against: ``training`` or ``synthetic`` customers."""
        return SYNTHETIC if self.synthetic else "training"

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
        result = self.evidence.economics(business)
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


class BatchBusyError(HTTPException):
    """Every batch slot is taken: a 429 the batch page already answers with "try again"."""

    def __init__(self) -> None:
        super().__init__(
            status_code=429,
            detail="Other uploads are being scored. Try again shortly.",
            headers={"Retry-After": str(BATCH_RETRY_AFTER_SECONDS)},
        )
        self.error = "batch_busy"


_batch_slots = threading.BoundedSemaphore(BATCH_SLOTS)
_busy_log_count = 0


@contextmanager
def batch_slot() -> Iterator[None]:
    """Hold one of ``BATCH_SLOTS`` while a batch is read and scored; 429 at once if none is free.

    Taken inside the endpoint, after the body has arrived, so a slow upload
    holds no slot; given back however the scoring ends.
    """
    global _busy_log_count
    if not _batch_slots.acquire(blocking=False):
        _busy_log_count += 1
        if _busy_log_count in (1, 10, 100, 1000) or _busy_log_count % 10_000 == 0:
            logger.warning("turned away %d batch upload(s): every slot busy", _busy_log_count)
        raise BatchBusyError
    try:
        yield
    finally:
        _batch_slots.release()


_dashboard_lock = threading.Lock()
#: The model and source the current dashboard was built for, and the dashboard.
_built: tuple[Predictor, object, Dashboard] | None = None
#: The model, source and time of the last failed build, so it is not retried
#: (and logged) on every request.
_failed: tuple[Predictor, object, float, Unavailable] | None = None


def deployment_of(model: Predictor) -> Deployment:
    """Where ``model`` is served and whose rows are listed: the configured choice."""
    return DEPLOYMENT if DEPLOYMENT is not None else model.config.deployment


def _source(model: Predictor) -> object:
    """What the book is built from: the dataset's loader, or the demo generator.

    The deployment is part of it, so a service switched from one to the other
    builds anew and can never answer from the other one's book.
    """
    deployment = deployment_of(model)
    return (deployment, DEMO_GENERATOR) if deployment.synthetic else BOOK_LOADER


def get_dashboard() -> Dashboard:
    """The dashboard for the served model, built on first use; 503 if it cannot be."""
    model = require_predictor()
    return _dashboard_for(model, _source(model))


def optional_dashboard() -> Dashboard | None:
    """``get_dashboard()``, or ``None`` when the model or the book's data is missing."""
    try:
        return get_dashboard()
    except HTTPException:
        return None


def _dashboard_for(model: Predictor, source: object) -> Dashboard:
    """One dashboard per model and source: a reloaded model, a test's loader or
    another deployment gets a fresh book, never one built for another."""
    global _built, _failed
    with _dashboard_lock:
        if _built is not None and _built[0] is model and _built[1] == source:
            return _built[2]
        if (
            _failed is not None
            and _failed[0] is model
            and _failed[1] == source
            and time.monotonic() - _failed[2] < RETRY_AFTER_FAILURE_SECONDS
        ):
            raise type(_failed[3])()
        try:
            dashboard = _build(model, source)
        except (DatasetUnavailableError, DemoDataUnavailableError) as exc:
            _failed = (model, source, time.monotonic(), exc)
            raise
        _built, _failed = (model, source, dashboard), None
        return dashboard


def _build(model: Predictor, source: object) -> Dashboard:
    reports = REPORTS_DIR if REPORTS_DIR is not None else model.config.paths.reports_dir
    if isinstance(source, tuple):
        return _build_demo(model, source[0], source[1], reports)
    try:
        book = source(model)
    except ChurnSenseError as exc:
        logger.error("dataset unavailable: %s", exc)
        raise DatasetUnavailableError from exc
    return Dashboard(
        model=model, book=book, reports_dir=reports, shap_cache=model.source / "shap_global.json"
    )


def _build_demo(
    model: Predictor, deployment: Deployment, generate: Callable[[], pd.DataFrame], reports: Path
) -> Dashboard:
    """The public demo's dashboard: generated customers and published evidence, or nothing.

    Every way this can fail ends in ``DemoDataUnavailableError``. The dataset's
    loader is not called here, and no failure falls back to it.
    """
    try:
        raw_file = RAW_DATA_FILE if RAW_DATA_FILE is not None else model.config.raw_data_file
        if deployment.public and raw_file.exists():
            raise DemoDataError(
                "a public demo was started beside a dataset file; it lists generated "
                "customers only and must not have the dataset in reach"
            )
        generated = generate()
        book = synthetic_book(generated, model)
        explanations = model.source / DEMO_EXPLANATIONS_FILE
        _check_explanations(explanations, model, book, records_sha256(generated))
        evidence = load_published(model.source / EVIDENCE_FILE, model)
        # Built here, not after: preparing the book can fail too, and must end the same way.
        return Dashboard(
            model=model,
            book=book,
            reports_dir=reports,
            shap_cache=explanations,
            evidence=evidence,
            records="synthetic",
        )
    except ChurnSenseError as exc:
        logger.error("demo data unavailable: %s", exc)
        raise DemoDataUnavailableError from exc
    except Exception as exc:
        # Whatever a generator or a damaged file can raise: the answer is the same.
        logger.exception("demo data unavailable")
        raise DemoDataUnavailableError from exc


def _check_explanations(path: Path, model: Predictor, book: Book, records: str) -> None:
    """The cached attributions are of this model and of exactly the customers being listed.

    The file names the book it explains by hash; and, whatever it says of
    itself, every row it would draw must be one of the generated customers - a
    cache of any other rows (the dataset's validation sample, say) is refused.
    """
    cached = load_cached_explanations(path, model.meta.get("model_sha256", ""))
    if cached is None:
        raise DemoDataError("no usable attributions are cached for the demo customers")
    if cached.get("partition") != SYNTHETIC or cached.get("records_sha256") != records:
        raise DemoDataError("the cached attributions are not of the demo customers being listed")
    features = cached["features"]
    if set(features) != set(model.feature_columns):
        raise DemoDataError("the cached attributions are of other features than the model's")
    listed = {
        tuple(
            str(value) if feature in schema.ALLOWED_CATEGORIES else float(value)
            for feature, value in zip(features, row, strict=True)
        )
        for row in book.frame[features].itertuples(index=False, name=None)
    }
    for row in cached["x"]:
        if tuple(row[feature] for feature in features) not in listed:
            raise DemoDataError("the cached attributions draw a row that is no demo customer's")


def warm_up() -> None:
    """Build the dashboard now, at startup, so no visitor waits for it and readiness can tell.

    Never fatal: a service that cannot build its book still answers ``/health``,
    reports itself not ready, and tries again when a page asks for the book.
    """
    try:
        get_dashboard()
    except HTTPException:
        pass  # already logged where it failed: the model, the dataset or the demo data
    except Exception:
        logger.exception("the dashboard could not be built at startup")


def readiness() -> dict[str, bool]:
    """Whether the model and the book are loaded - read from memory only.

    Nothing is loaded, read or scored here: a model that failed to load and a
    book that failed to build stay unready until a request (or a restart)
    loads them, so a health checker polling this costs nothing. ``dataset``
    reports the book, whichever customers it was built from.
    """
    model = get_predictor.cache_info().currsize > 0
    dataset = False
    if model:
        served = get_predictor()  # cached: no load
        # One reference read, no lock: a rebuild holds the lock for seconds, and
        # a health check must not wait on it.
        built = _built
        dataset = built is not None and built[0] is served and built[1] == _source(served)
    return {"model": model, "dataset": dataset}


def reset() -> None:
    """Forget the loaded model and book; the next request loads them again."""
    global _built, _failed
    get_predictor.cache_clear()
    with _dashboard_lock:
        _built = _failed = None
