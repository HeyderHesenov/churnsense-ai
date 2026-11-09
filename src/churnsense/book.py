"""The scored customer book, and the one rule about which outcomes may be shown.

Every consumer that shows customers - the Streamlit dashboard (``app/data.py``
caches what this module builds) and the API (``churnsense.api``) - takes the
book from here, so the rule below has exactly one implementation.

**The test partition's outcomes never reach a view.** This docstring is the one
statement of the rule; the code below is its only implementation.

* ``Book.frame`` - every customer's features, display buckets, score and
  partition, and *no outcome column at all*.
* ``Book.outcomes`` - the target for training and validation rows only, keyed
  by customerID. Test-partition labels are never copied into it.
* ``labelled(frame, shown=...)`` joins the two for exactly those rows. It is the
  only way to get an outcome to aggregate, so code that forgot it fails with a
  ``KeyError`` - it cannot quietly count withheld rows as retained ones.
* ``outcome_codes(frame, shown)`` is the one per-row label: ``"churned"``,
  ``"stayed"`` or ``"withheld"``. A row whose outcome is not in ``shown`` is
  withheld by construction, never defaulted to a class.
* Partitions are rebuilt from the data and configuration, and trusted only
  when the data is the file the served model was trained on (its SHA-256) and
  the rows fall where the model's recorded split digest says. Otherwise -
  missing, malformed or different provenance alike - every row is "unknown",
  no outcome is shown, and ``Book.withheld`` says why.

The final evaluation (``make evaluate CONFIRM=yes``) is the one place a figure
is computed from the test labels. Rebuilding the stratified split here reads
every label, as training does, only to put each row where training put it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, NamedTuple

import pandas as pd

from churnsense import analysis as an
from churnsense.data import schema
from churnsense.data.loader import features_and_target, load_clean
from churnsense.data.split import DataSplits, check_split, make_splits
from churnsense.exceptions import DataError

if TYPE_CHECKING:
    from churnsense.models.predict import Predictor

logger = logging.getLogger(__name__)

#: Partitions whose historical outcome may be shown.
SHOWN_PARTITIONS: tuple[str, ...] = ("train", "validation")

#: The per-row outcome labels. Fixed strings: a client must never have to read
#: meaning into a null.
CHURNED, STAYED, WITHHELD = "churned", "stayed", "withheld"
OUTCOME_CODES: dict[int, str] = {1: CHURNED, 0: STAYED}

#: What is known of a customer today. ``UNKNOWN`` is neither of the others: a test
#: customer, or any customer while the partitions are unverified, may have left or may
#: not, and nothing here may read which.
ACTIVE, UNKNOWN = "active", "unknown"
STATUS_CODES: dict[str, str] = {STAYED: ACTIVE, CHURNED: CHURNED, WITHHELD: UNKNOWN}

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
    """Every customer, scored, with the outcomes that may be shown kept apart."""

    #: Every customer: features, display buckets, score and partition. No outcome.
    frame: pd.DataFrame
    #: The target of training and validation customers only, keyed by customerID.
    outcomes: pd.Series
    #: The training partition in split order, for the SHAP background; empty if untrusted.
    train_index: pd.Index
    #: Why no partition could be trusted, or ``None``.
    withheld: str | None


def load_book(model: Predictor) -> Book:
    """Read the configured dataset and score it with ``model``.

    Raises ``ChurnSenseError`` (a ``DataError`` or ``ConfigError``) when the
    dataset is missing or fails its pinned checks; callers turn that into
    their own "no data" state rather than a partial book.
    """
    customers, cleaning = load_clean(model.config)
    return score_book(customers, cleaning.source_sha256, model)


def score_book(customers: pd.DataFrame, data_sha256: str | None, model: Predictor) -> Book:
    """Score every customer and separate the outcomes that may be shown.

    Scored through the same ``Predictor`` the API uses, so a number read off a
    page and a number returned by ``POST /predict`` for the same customer are
    the same number. The partitions are rebuilt from ``customers`` itself, so
    they cannot be paired with another frame.
    """
    parts, withheld = _verified_split(customers, data_sha256, model)
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
    customers: pd.DataFrame, data_sha256: str | None, model: Predictor
) -> tuple[DataSplits | None, str | None]:
    """The partitions the served model was trained on, or why they cannot be trusted.

    Fails closed: provenance that is missing, malformed or different withholds
    every outcome, each state with its own ``WITHHELD_REASONS`` entry.
    """
    meta = model.meta
    recorded = meta.get("data_sha256")
    if recorded is None:
        return None, WITHHELD_REASONS["no dataset hash"]
    if not is_sha256(recorded):
        return None, WITHHELD_REASONS["invalid dataset hash"]
    if data_sha256 is None:
        return None, WITHHELD_REASONS["dataset unhashed"]
    if data_sha256 != recorded:
        return None, WITHHELD_REASONS["dataset differs"]
    digest = meta.get("split_digest")
    if digest is None:
        return None, WITHHELD_REASONS["no split digest"]
    if not is_sha256(digest):
        return None, WITHHELD_REASONS["invalid split digest"]
    try:
        X, y = features_and_target(customers, model.config)
        parts = make_splits(X, y, model.config, log=False)
    except (DataError, ValueError) as exc:
        # The detail (a column, a repeated ID) is for the server log, not the page.
        logger.warning("partitions could not be rebuilt: %s", exc)
        return None, WITHHELD_REASONS["split failed"]
    try:
        check_split(parts, meta)  # the digest is present and well formed: only a mismatch
    except DataError:
        return None, WITHHELD_REASONS["split differs"]
    return parts, None


def is_sha256(value: object) -> bool:
    """A hex SHA-256 as ``hashlib`` writes it: 64 lower-case hex digits."""
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def labelled(frame: pd.DataFrame, *partitions: str, shown: pd.Series) -> pd.DataFrame:
    """The rows of ``frame`` whose outcome may be read, with the outcome attached.

    Training and validation by default; name one to narrow it. Every outcome
    aggregate is computed on this, so withheld rows count neither in a rate
    nor in its denominator.
    """
    chosen = partitions or SHOWN_PARTITIONS
    if not set(chosen) <= set(SHOWN_PARTITIONS):
        raise ValueError(f"outcomes are shown only for {SHOWN_PARTITIONS}, not {chosen}")
    rows = frame[frame["partition"].isin(chosen)]
    target = rows[schema.ID_COLUMN].map(shown)
    if target.isna().any():
        raise KeyError("rows with no shown outcome: the frame is not from this book")
    return rows.assign(**{schema.TARGET: target.astype(int)})


def outcome_codes(frame: pd.DataFrame, shown: pd.Series) -> pd.Series:
    """Each row's outcome as ``"churned"``, ``"stayed"`` or ``"withheld"``.

    A row is withheld unless ``shown`` - which holds training and validation
    customers only - carries its outcome, so a test customer, or any customer
    while the partitions are unverified, can never read as a class.
    """
    codes = frame[schema.ID_COLUMN].map(shown).map(OUTCOME_CODES)
    return codes.fillna(WITHHELD).rename("outcome")


def status_codes(frame: pd.DataFrame, shown: pd.Series) -> pd.Series:
    """Each row's status as ``"active"``, ``"churned"`` or ``"unknown"``.

    Read from ``outcome_codes``, so it depends only on what may be shown: every test
    customer is ``"unknown"``, whatever became of them.
    """
    return outcome_codes(frame, shown).map(STATUS_CODES).rename("status")


def training_features(book: Book, columns: list[str]) -> pd.DataFrame | None:
    """The training partition's model inputs in split order, or ``None`` if untrusted.

    The population "an average customer" is drawn from for every SHAP
    explanation; without verified partitions there is no training set to draw
    it from, and explaining against anything else would be a different claim.
    """
    if book.train_index.empty:
        return None
    return book.frame.loc[book.train_index, columns]
