"""The official release of the held-out partition, frozen in a single file.

**Why one.** ``evaluation.disclosure`` lets one two-by-two table out. Two tables on the
same customers, at another cut or from another model, subtract to what the customers
between them did: a single customer whose flag differs is named outright. So this is
not a limit on how often a command may run. It is a limit on how many answers this
project publishes.

**How far this file reaches.** It enforces the rule in one working tree, and no further.
The ledger and its ``flock`` belong to the checkout they are in: another clone, worktree
or fork has its own, and nothing here can stop a calculation made there on a public
dataset. What makes one release the project's is an operating rule: one operator, on one
machine, in one checkout, and a person reading every change to this governance.
``scripts/check_release_ledger.py`` helps as a best-effort consistency check. It catches
accidental and ordinary invalid ledger changes in a pull request; it is not a required check
today, and a pull request can edit its own workflow or the script, so it is not a trusted
boundary. ``SECURITY.md`` sets out all of them.

**The ledger is the release.** ``LEDGER_FILE`` holds the state, the fingerprints of
what was evaluated and the released figures, in one file replaced in one step. So there
is nothing to keep in agreement: a reader sees a release or does not, and no crash can
leave figures published without the state that allows them, or the reverse. It never
holds a customer's ID, a label, a score or a figure ``disclosure`` withheld.

**A ledger is valid or it is nothing.** ``_decode`` allows a closed set of fields per state
and accepts only the bytes the writer would lay out, and a matching digest vouches for
none of that. The writer applies the same check before it replaces the file, so it cannot
write what a reader would refuse. Every refusal reads as the one generic "withheld".

**States only move forward.**

=========  =====================================================================
open       Nothing has been evaluated. The file ships in this state, because a
           missing one must never mean "go ahead".
reserved   A run has claimed the release for one identity, and may be scoring.
           Readers get nothing. Only this identity can ever finish it.
published  Figures, or a recorded ``null`` when the rule withheld them. Final.
=========  =====================================================================

A figure the rule withheld is an answer. A run that failed is not: when scoring raises,
whatever the reason, nothing is published and the ledger stays ``reserved``.

**Concurrency.** A run holds an exclusive ``flock`` on the ledger's directory from
before it reads the state until it has written the last one, and a second run is
refused rather than queued. The kernel drops the lock when a process dies, so a crash
leaves no stale lock. Readers take no lock: they read one file that only ever changes
by an atomic replace.

**Dying at any moment** (``tests/test_release.py`` kills a real process at each step):

* before the reservation: still ``open``;
* after it, or while scoring: ``reserved``, which reads as withheld;
* half way through a write: the temporary file is not the ledger, and the next run
  removes it;
* after the publication: done.

**A retry is running the same command again, under the exact same identity.** From
``reserved`` it scores the same artifact on the same rows, and nobody has seen the figures of
the run that died, since nothing is printed or logged before the publication. That finishes a
run stopped by something outside the fingerprints. A failure that repeats does not finish, and
fixing it changes a fingerprint, which is another evaluation and is refused: such a failure can
permanently consume the release. From ``published`` it scores nothing. There is no reset, no
force flag and no environment variable, and the path comes from neither the configuration nor
the environment.

**Before anything is reserved** ``preflight_environment`` and ``preflight_artifact`` check the
Python's minor version, the locked numerical packages and the artifact's scikit-learn, and
``report.rehearse`` runs the scoring, the metrics and the rendering on validation rows. That
reduces the risk of a repeating failure and does not remove it: a value only the test rows hold,
or a system failure, can still leave the ledger reserved.

**What this cannot do.** The file is only as durable as Git makes it: commit it right
after a release. A reservation is not committed, since nothing in Git may change in that
checkout until it is published (``SECURITY.md``, "After a reservation"). Until then another
clone does not see it, ``git restore`` silently brings back the committed state, and anyone
who can write to this checkout can replace it. Nothing here can see that: the boundary is operational, not
cryptographic. It cannot reach what was published before it existed. A reservation whose
evaluation can no longer run stays reserved, and readers withhold: no command and no record
takes it back to ``open``, because nothing today could verify an independent approval
(``SECURITY.md``, "A reservation has no way back").
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from churnsense.config import PROJECT_ROOT, Config
from churnsense.data import schema
from churnsense.evaluation import disclosure
from churnsense.exceptions import ReleaseRefused

logger = logging.getLogger(__name__)

#: The one ledger. Not a setting: a second path would be a second release.
LEDGER_FILE = PROJECT_ROOT / "governance" / "held_out_release.json"

OPEN, RESERVED, PUBLISHED = "open", "reserved", "published"

#: Bump when the evaluation changes in a way the source fingerprints would miss.
EVALUATION_VERSION = 1

#: ``requires-python`` in ``pyproject.toml``, as ``[lowest, first refused)``.
SUPPORTED_PYTHON = ((3, 12), (3, 14))
#: The packages whose arithmetic reaches a score or a figure.
NUMERICAL_PACKAGES = ("scikit-learn", "numpy", "pandas", "scipy", "joblib")
LOCK_FILE = "requirements.txt"

#: Where the fingerprinted sources are: every module on the path from the files to a released
#: figure, so that a module off that path changing does not make this a different evaluation.
#: ``config.py`` is not among them: what it parses is fingerprinted by value in ``identity_of``.
_PACKAGE = Path(__file__).resolve().parents[1]
#: The rule, its arithmetic, and the command that scores and writes.
_EVALUATION_SOURCES = (
    "evaluation/disclosure.py",
    "evaluation/metrics.py",
    "evaluation/release.py",
    "evaluation/report.py",
)
#: What decides the rows and the values the model is given.
_DATA_SOURCES = (
    #: ``sha256_of`` and ``verify_dataset``: the dataset's hash and the artifact's byte check.
    "data/download.py",
    "data/loader.py",
    "data/schema.py",
    "data/split.py",
    "features/preprocess.py",
)
#: What the unpickled artifact runs, since its classes are stored by name and not by content, and
#: what loads it: ``train.load_artifact`` checks its bytes against the hash the identity records.
_MODEL_SOURCES = (
    "models/calibration_clamp.py",
    "models/predict.py",
    "models/registry.py",
    "models/train.py",
)

_FORMAT = 1
_MAX_LEDGER_BYTES = 64 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
#: What ``report.main`` writes: the date of a release in UTC, and no other text.
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", re.ASCII)
_LEDGER_KEYS = frozenset(
    {"format", "state", "policy", "identity", "evaluated_at", "released", "digest"}
)
_HASH_KEYS = frozenset(
    {
        "dataset_sha256",
        "split_digest",
        "model_sha256",
        "preprocessing_sha256",
        "model_config_sha256",
        "feature_schema_sha256",
        "evaluation_sha256",
        "data_code_sha256",
        "model_code_sha256",
        "runtime_sha256",
    }
)
_IDENTITY_KEYS = _HASH_KEYS | {"threshold"}


@dataclass(frozen=True, slots=True)
class Ledger:
    state: str
    identity: dict[str, Any] | None
    evaluated_at: str | None
    released: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class Published:
    """The released figures, for the artifact and threshold they describe."""

    evaluated_at: str
    metrics: dict[str, Any]


# --- the file -------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(body: Mapping[str, Any]) -> str:
    """Catches damage and a hand edit made in passing. Anyone can recompute it: not a seal."""
    return _sha(_canonical(body))


def _laid_out(body: Mapping[str, Any]) -> str:
    """The one way a ledger is written down, so the same contents are always the same bytes."""
    return json.dumps(body, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _encode(ledger: Ledger) -> str:
    body = {
        "format": _FORMAT,
        "state": ledger.state,
        "policy": disclosure.POLICY,
        "identity": ledger.identity,
        "evaluated_at": ledger.evaluated_at,
        "released": ledger.released,
    }
    return _laid_out({**body, "digest": _digest(body)})


def open_ledger_text() -> str:
    """The ledger as it ships, before anything has been evaluated."""
    return _encode(Ledger(OPEN, None, None, None))


def _refuse_constant(name: str) -> None:
    raise ValueError(f"a ledger holds no {name}")


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("a ledger holds only finite numbers")
    return number


def _valid_identity(identity: Any) -> bool:
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS:
        return False
    threshold = identity["threshold"]
    return (
        all(isinstance(identity[k], str) and _HEX64.fullmatch(identity[k]) for k in _HASH_KEYS)
        and type(threshold) is float
        and 0 <= threshold <= 1
    )


def _valid_timestamp(value: Any) -> bool:
    """A real moment in UTC. Any freer text here would be a way round the figures' allowlist."""
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _holds_together(state: Any, identity: Any, evaluated_at: Any, released: Any) -> bool:
    """Whether these are the fields ``state`` allows: its whole schema, field by field."""
    if state == OPEN:
        return identity is None and evaluated_at is None and released is None
    if state not in (RESERVED, PUBLISHED):
        return False
    if not _valid_identity(identity) or not _valid_timestamp(evaluated_at):
        return False
    if released is None:
        return True
    return (
        state == PUBLISHED
        and disclosure.is_released(released)
        and released["threshold"] == identity["threshold"]
    )


def _decode(text: str) -> Ledger:
    """A ledger, or ``ReleaseRefused``: nothing malformed is ever acted on or shown.

    The schema comes before the digest: a matching digest says nothing about what is under
    it, and every value that gets as far as being digested is by then known to be shallow.
    """
    if len(text) > _MAX_LEDGER_BYTES:
        raise ReleaseRefused("the release ledger is damaged: it is too large")
    try:
        body = json.loads(text, parse_constant=_refuse_constant, parse_float=_finite)
    except (ValueError, RecursionError) as exc:
        raise ReleaseRefused("the release ledger is damaged: it is not valid JSON") from exc
    if not isinstance(body, dict) or set(body) != _LEDGER_KEYS:
        raise ReleaseRefused("the release ledger is damaged: its fields are wrong")
    if type(body["format"]) is not int or body["format"] != _FORMAT:
        raise ReleaseRefused("the release ledger is damaged: unknown format")
    if type(body["policy"]) is not int or body["policy"] != disclosure.POLICY:
        raise ReleaseRefused("the release ledger was written under another policy")
    state, identity = body["state"], body["identity"]
    evaluated_at, released = body["evaluated_at"], body["released"]
    if not _holds_together(state, identity, evaluated_at, released):
        raise ReleaseRefused("the release ledger is damaged: its state does not hold together")
    sealed = {key: value for key, value in body.items() if key != "digest"}
    if body["digest"] != _digest(sealed):
        raise ReleaseRefused("the release ledger is damaged: its digest does not match")
    if text != _laid_out(body):
        raise ReleaseRefused("the release ledger is damaged: it is not as the writer lays it out")
    return Ledger(state, identity, evaluated_at, released)


def read_ledger() -> Ledger:
    """The ledger as it is on disk. Missing is a refusal: it never means "open".

    As bytes, so that text mode does not tidy up line endings the writer never wrote.
    """
    try:
        text = LEDGER_FILE.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise ReleaseRefused(f"the release ledger {LEDGER_FILE.name} is missing") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseRefused("the release ledger cannot be read") from exc
    return _decode(text)


def rehearse_writes(
    identity: Mapping[str, Any],
    evaluated_at: str,
    figures: Mapping[str, Any],
    meta: Mapping[str, Any],
    text: str,
) -> None:
    """Everything a run goes on to write, on nothing that counts.

    The ledger's states are encoded and read back and never stored; the report goes to a
    temporary directory that is removed. Raises whatever the real write would.
    """
    json.dumps(meta, allow_nan=False)
    for ledger in (
        Ledger(RESERVED, dict(identity), evaluated_at, None),
        Ledger(PUBLISHED, dict(identity), evaluated_at, dict(figures)),
        Ledger(PUBLISHED, dict(identity), evaluated_at, None),
    ):
        _decode(_encode(ledger))
    with tempfile.TemporaryDirectory() as directory:
        _write_atomic(Path(directory) / "rehearsal.md", text)


def current_state() -> str | None:
    """The state the ledger holds now, or ``None`` if it cannot be read. Never a figure."""
    try:
        return read_ledger().state
    except ReleaseRefused:
        return None


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step: a reader gets the old file or the new one, never a part."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_ledger(ledger: Ledger) -> None:
    """Replace the ledger, unless a reader would refuse the result: no one could read it again."""
    text = _encode(ledger)
    _decode(text)
    _write_atomic(LEDGER_FILE, text)


def write_report(path: Path, text: str) -> None:
    """Write a report rendered from a published ledger. The ledger stays the authority.

    Under the lock, so one run cannot sweep up another's temporary file as wreckage.
    """
    with exclusive():
        path.parent.mkdir(parents=True, exist_ok=True)
        _remove_wreckage(path)
        _write_atomic(path, text)


def _remove_wreckage(path: Path) -> None:
    """Temporary files a run that died left beside ``path``, and nothing else."""
    for leftover in path.parent.glob(f".{path.name}.*"):
        leftover.unlink(missing_ok=True)


# --- the lock -------------------------------------------------------------------------

_TOKEN = object()


class Hold:
    """Proof that this process holds the release lock right now."""

    __slots__ = ("_held",)

    def __init__(self, token: object) -> None:
        if token is not _TOKEN:
            raise ReleaseRefused("the release lock is taken with release.exclusive()")
        self._held = True

    def require(self) -> None:
        if not self._held:
            raise ReleaseRefused("the release lock is no longer held")

    def _let_go(self) -> None:
        self._held = False


@contextlib.contextmanager
def exclusive() -> Iterator[Hold]:
    """The exclusive lock on the ledger's directory, or ``ReleaseRefused`` at once."""
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - POSIX only, and this fails closed
        raise ReleaseRefused("a release needs POSIX file locks, not available here") from exc
    try:
        directory = os.open(LEDGER_FILE.parent, os.O_RDONLY)
    except OSError as exc:
        raise ReleaseRefused("the release ledger's directory is missing") from exc
    try:
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ReleaseRefused("another final evaluation is running") from exc
        hold = Hold(_TOKEN)
        try:
            yield hold
        finally:
            hold._let_go()
    finally:
        os.close(directory)  # closing the descriptor drops the lock


# --- the identity ---------------------------------------------------------------------


def _sources_sha(names: tuple[str, ...]) -> str:
    """The bytes of ``names``: nothing about them depends on the running Python."""
    try:
        return _sha(
            _canonical(
                {name: hashlib.sha256((_PACKAGE / name).read_bytes()).hexdigest() for name in names}
            )
        )
    except OSError as exc:
        raise ReleaseRefused("a source the evaluation depends on cannot be read") from exc


def _evaluation_sha() -> str:
    """The rule and the arithmetic behind a released figure, and the command around them."""
    return _sha(
        _canonical(
            {
                "version": EVALUATION_VERSION,
                "policy": disclosure.POLICY,
                "min_cell": disclosure.MIN_CELL,
                "digits": disclosure.DIGITS,
                "code": _sources_sha(_EVALUATION_SOURCES),
            }
        )
    )


def _lock_text() -> str:
    try:
        return (PROJECT_ROOT / LOCK_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseRefused(f"the dependency lock {LOCK_FILE} cannot be read") from exc


def _installed_versions() -> dict[str, str]:
    try:
        return {name: importlib.metadata.version(name) for name in NUMERICAL_PACKAGES}
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReleaseRefused("a numerical package this evaluation needs is not installed") from exc


def _locked_versions() -> dict[str, str]:
    pins = dict(
        re.findall(
            rf"(?m)^({'|'.join(re.escape(n) for n in NUMERICAL_PACKAGES)})==([^\s;\\]+)",
            _lock_text(),
        )
    )
    if missing := sorted(set(NUMERICAL_PACKAGES) - set(pins)):
        raise ReleaseRefused(f"{LOCK_FILE} pins no version of: {', '.join(missing)}")
    return pins


def _runtime_sha() -> str:
    """The interpreter, the platform and the numerical packages this run scores with."""
    return _sha(
        _canonical(
            {
                "python": platform.python_version(),
                "platform": sys.platform,
                "machine": platform.machine(),
                "packages": _installed_versions(),
                "lock": _sha(_lock_text()),
            }
        )
    )


def preflight_environment() -> None:
    """Refuse an environment that is not the locked one, before anything is reserved."""
    low, high = SUPPORTED_PYTHON
    if not low <= sys.version_info[:2] < high:
        raise ReleaseRefused(f"Python {platform.python_version()} is not a supported version")
    installed, locked = _installed_versions(), _locked_versions()
    if differ := sorted(name for name in NUMERICAL_PACKAGES if installed[name] != locked[name]):
        raise ReleaseRefused(f"these packages differ from {LOCK_FILE}: {', '.join(differ)}")


def preflight_artifact(meta: Mapping[str, Any]) -> None:
    """Refuse an artifact trained under another scikit-learn, before anything is reserved."""
    if meta.get("sklearn_version") != _installed_versions()["scikit-learn"]:
        raise ReleaseRefused("the artifact was trained with another scikit-learn version")


def identity_of(
    cfg: Config, meta: Mapping[str, Any], split_digest: str, dataset_sha256: str
) -> dict[str, Any]:
    """What makes this evaluation this one; any difference is a second release.

    Left out on purpose, because they cannot move a released figure: paths, the API and
    logging settings, the business assumptions (the report's validation economics only),
    the dataset's download URL and ``training.n_jobs``. A change to any fingerprinted
    source or to the lock also refuses a run that would only read a release back: what
    is on the books stays there and readers are not affected, but this run cannot say it
    is the evaluation that made it.
    """
    training = {k: v for k, v in asdict(cfg.training).items() if k != "n_jobs"}
    return {
        "dataset_sha256": dataset_sha256,
        "split_digest": split_digest,
        "model_sha256": meta["model_sha256"],
        "threshold": float(meta["threshold"]),
        "preprocessing_sha256": _sha(
            _canonical(
                {
                    "features": asdict(cfg.features),
                    "split": asdict(cfg.split),
                    "seed": cfg.random_seed,
                    "rows": cfg.dataset.expected_rows,
                }
            )
        ),
        "model_config_sha256": _sha(
            _canonical(
                {
                    "training": training,
                    "model_key": meta["model_key"],
                    "calibrated": meta["calibrated"],
                    "calibration_method": meta.get("calibration_method"),
                    "probability_epsilon": meta.get("probability_epsilon"),
                }
            )
        ),
        "feature_schema_sha256": _sha(
            _canonical(
                {
                    "feature_columns": meta["feature_columns"],
                    "dropped_columns": meta["dropped_columns"],
                    "raw_columns": schema.RAW_COLUMNS,
                    "numeric_columns": schema.NUMERIC_COLUMNS,
                    "integer_columns": schema.INTEGER_COLUMNS,
                    "numeric_bounds": schema.NUMERIC_BOUNDS,
                    "categories": schema.ALLOWED_CATEGORIES,
                    "target": schema.TARGET,
                    "target_encoding": schema.TARGET_ENCODING,
                }
            )
        ),
        "evaluation_sha256": _evaluation_sha(),
        "data_code_sha256": _sources_sha(_DATA_SOURCES),
        "model_code_sha256": _sources_sha(_MODEL_SOURCES),
        "runtime_sha256": _runtime_sha(),
    }


def require_source_tree() -> None:
    """An installed copy would keep its ledger somewhere nobody commits."""
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        raise ReleaseRefused("a release runs only from the project's source tree")


# --- the one writer -------------------------------------------------------------------


def freeze(
    identity: Mapping[str, Any],
    score: Callable[[Hold], Mapping[str, Any] | None],
    now: Callable[[], str],
) -> Ledger:
    """This checkout's release: reserved and scored once, or read back.

    ``score`` is called only from ``reserved``, under the lock, and only after the
    identity has been checked. So a run that would be a second release reads nothing,
    and a run repeating the first reads nothing either. If ``score`` raises, the ledger
    stays ``reserved``: a failure is not an answer, and the same identity may try again.
    """
    with exclusive() as hold:
        _remove_wreckage(LEDGER_FILE)
        ledger = read_ledger()
        if ledger.state == OPEN:
            ledger = Ledger(RESERVED, dict(identity), now(), None)
            _write_ledger(ledger)
        elif differs := sorted(k for k in _IDENTITY_KEYS if ledger.identity[k] != identity[k]):
            # Names only: a fingerprint is no secret, but a threshold printed beside a
            # refusal starts a conversation about a second cut.
            raise ReleaseRefused(
                "this checkout's ledger already holds a release; this evaluation "
                f"differs in: {', '.join(differs)}"
            )
        if ledger.state == PUBLISHED:
            return ledger
        figures = score(hold)
        released = None if figures is None else disclosure.release(figures)
        published = Ledger(PUBLISHED, ledger.identity, ledger.evaluated_at, released)
        _write_ledger(published)
        return published


# --- the one reader -------------------------------------------------------------------


def published_release(model_sha256: Any, threshold: float) -> tuple[Published | None, bool]:
    """The release of this artifact at this threshold, and whether one is being withheld.

    ``(None, False)``: nothing is recorded for what is being served. ``(None, True)``: a
    release exists or was begun, or the ledger that would say cannot be read, and no figure
    may be shown; every reason gives the same answer. Reads one file, with no lock.
    """
    try:
        ledger = read_ledger()
    except ReleaseRefused:
        return None, True
    if ledger.state == OPEN:
        return None, False
    if ledger.state == RESERVED:
        return None, True
    if ledger.identity["model_sha256"] != model_sha256 or ledger.identity["threshold"] != threshold:
        return None, False
    if ledger.released is None:
        return None, True
    return Published(ledger.evaluated_at, ledger.released), False
