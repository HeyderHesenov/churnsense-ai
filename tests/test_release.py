"""The frozen release of the held-out partition, in one checkout. Synthetic data throughout.

Nothing here reads the real dataset, and ``tests/conftest.py`` points the ledger at a
temporary file for every test, so none of this can touch the real one.

The questions are: can a second release be got out, by any route short of editing the
ledger; and at every moment a run can die, what does a reader see?
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import textwrap
import time
import tomllib
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from churnsense.api.dashboard_schemas import HoldoutEvaluation
from churnsense.config import DEFAULT_CONFIG_PATH, Config
from churnsense.data import download
from churnsense.data.download import sha256_of
from churnsense.evaluation import disclosure, release, report
from churnsense.evaluation.disclosure import POLICY
from churnsense.exceptions import ChurnSenseError, ReleaseRefused
from tests import ledger_payloads as payloads
from tests.conftest import edit_sidecar, pinned_split_digest, record_provenance

SHA = "a" * 64
IDENTITY = {
    "dataset_sha256": "1" * 64,
    "split_digest": "2" * 64,
    "model_sha256": SHA,
    "threshold": 0.36,
    "preprocessing_sha256": "3" * 64,
    "model_config_sha256": "4" * 64,
    "feature_schema_sha256": "5" * 64,
    "evaluation_sha256": "6" * 64,
    "data_code_sha256": "7" * 64,
    "model_code_sha256": "8" * 64,
    "runtime_sha256": "9" * 64,
}
#: Invented figures, as ``disclosure.release`` writes them.
RELEASED = {
    "threshold": 0.36,
    "n": 1000,
    "positives": 300,
    "tn": 500,
    "fp": 200,
    "fn": 150,
    "tp": 150,
    "accuracy": 0.65,
    "precision": 0.4286,
    "recall": 0.5,
    "f1": 0.4615,
}
NOW = "2026-11-06T00:00:00+00:00"


def _score(calls: list | None = None, figures: dict | None = RELEASED):
    def score(hold: release.Hold) -> dict | None:
        hold.require()
        if calls is not None:
            calls.append(1)
        return figures

    return score


def _state() -> str:
    return json.loads(release.LEDGER_FILE.read_text())["state"]


# --- one release ----------------------------------------------------------------------


def test_the_first_release_is_recorded_and_read_back():
    ledger = release.freeze(IDENTITY, _score(), lambda: NOW)
    assert (ledger.state, ledger.evaluated_at, ledger.released) == ("published", NOW, RELEASED)
    published, withheld = release.published_release(SHA, 0.36)
    assert not withheld
    assert (published.metrics, published.evaluated_at) == (RELEASED, NOW)


def test_repeating_the_same_release_scores_nothing_and_changes_nothing():
    calls: list = []
    release.freeze(IDENTITY, _score(calls), lambda: NOW)
    before = release.LEDGER_FILE.read_bytes()
    again = release.freeze(IDENTITY, _score(calls), lambda: "2030-01-01T00:00:00+00:00")
    assert calls == [1], "a frozen release must not read the partition again"
    assert again.released == RELEASED and again.evaluated_at == NOW
    assert release.LEDGER_FILE.read_bytes() == before


@pytest.mark.parametrize("field", sorted(IDENTITY))
def test_a_change_in_any_part_of_the_identity_is_denied_before_scoring(field):
    calls: list = []
    release.freeze(IDENTITY, _score(calls), lambda: NOW)
    before = release.LEDGER_FILE.read_bytes()
    other = {**IDENTITY, field: 0.37 if field == "threshold" else "f" * 64}
    with pytest.raises(ReleaseRefused) as refused:
        release.freeze(other, _score(calls, {**RELEASED, "tp": 151}), lambda: NOW)
    assert field in str(refused.value), "the refusal names what differs"
    assert "f" * 64 not in str(refused.value), "and not the fingerprint itself"
    assert calls == [1]
    assert release.LEDGER_FILE.read_bytes() == before
    assert release.published_release(SHA, 0.36)[0].metrics == RELEASED


def test_a_different_identity_is_denied_while_a_release_is_only_reserved():
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.37}, _score(), lambda: NOW)
    assert _state() == release.RESERVED


def test_a_withheld_release_is_still_the_release():
    """A partition the rule withholds does not reopen for another try."""
    release.freeze(IDENTITY, _score(figures=None), lambda: NOW)
    assert _state() == release.PUBLISHED
    assert release.published_release(SHA, 0.36) == (None, True)
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.40}, _score(), lambda: NOW)


def test_figures_are_gated_again_on_the_way_in():
    """What a scorer returns is not trusted: an under-floor table is recorded as withheld."""
    release.freeze(IDENTITY, _score(figures={**RELEASED, "fp": 19, "tn": 681}), lambda: NOW)
    assert json.loads(release.LEDGER_FILE.read_text())["released"] is None


# --- what the ledger holds ------------------------------------------------------------


def test_the_ledger_holds_fingerprints_and_released_figures_only():
    raw = {**RELEASED, "roc_auc": 0.81, "average_precision": 0.6, "brier": 0.15, "ece": 0.03}
    release.freeze(IDENTITY, _score(figures=raw), lambda: NOW)
    stored = json.loads(release.LEDGER_FILE.read_text())
    assert set(stored) == {
        "format",
        "state",
        "policy",
        "identity",
        "evaluated_at",
        "released",
        "digest",
    }
    assert set(stored["identity"]) == set(IDENTITY)
    assert set(stored["released"]) == set(RELEASED)
    for word in ("roc_auc", "average_precision", "brier", "ece", "customerID", "score", "label"):
        assert word not in release.LEDGER_FILE.read_text(), word


def test_a_reservation_holds_no_figure_at_all():
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    assert json.loads(release.LEDGER_FILE.read_text())["released"] is None


# --- a damaged or missing ledger ------------------------------------------------------


def _with(**changes) -> str:
    body = json.loads(release.LEDGER_FILE.read_text()) | changes
    return json.dumps(body)


def _resealed(**changes) -> str:
    """A well-sealed ledger with odd contents: the digest alone must not vouch for them."""
    body = json.loads(release.LEDGER_FILE.read_text()) | changes
    body.pop("digest")
    return json.dumps({**body, "digest": release._digest(body)})


_DAMAGE = {
    "not-json": lambda: "not json",
    "truncated": lambda: release.LEDGER_FILE.read_text()[:40],
    "empty": lambda: "",
    "a-list": lambda: "[1, 2]",
    "digest-does-not-match": lambda: _with(released={**RELEASED, "tp": 151}),
    "digest-of-another-date": lambda: _with(evaluated_at="2030-01-01T00:00:00+00:00"),
    "no-digest": lambda: json.dumps(
        {k: v for k, v in json.loads(release.LEDGER_FILE.read_text()).items() if k != "digest"}
    ),
    "unknown-state": lambda: _resealed(state="approved"),
    "older-policy": lambda: _resealed(policy=POLICY - 1),
    "other-format": lambda: _resealed(format=99),
    "extra-key": lambda: _resealed(note="hello"),
    "identity-missing-a-field": lambda: _resealed(identity={"model_sha256": SHA}),
    "identity-not-hex": lambda: _resealed(identity={**IDENTITY, "model_sha256": "x"}),
    "threshold-nan": lambda: release.LEDGER_FILE.read_text().replace(
        '"threshold": 0.36', '"threshold": NaN'
    ),
    "overflow": lambda: release.LEDGER_FILE.read_text().replace(
        '"threshold": 0.36', '"threshold": 1e400'
    ),
    "deep-nesting": lambda: "[" * 200_000 + "]" * 200_000,
    "enormous-integer": lambda: "[" + "9" * 5000 + "]",
    "figures-under-the-floor": lambda: _resealed(released={**RELEASED, "fp": 19, "tn": 681}),
    "scalars-in-the-figures": lambda: _resealed(released={**RELEASED, "brier": 0.15}),
    "figures-at-another-threshold": lambda: _resealed(released={**RELEASED, "threshold": 0.4}),
    "reserved-with-figures": lambda: _resealed(state="reserved"),
    "open-with-figures": lambda: _resealed(state="open"),
    "oversized": lambda: " " * (release._MAX_LEDGER_BYTES + 1),
    "state-a-list": lambda: _resealed(state=[]),
    "state-an-object": lambda: _resealed(state={"a": 1}),
    "identity-a-list": lambda: _resealed(identity=[]),
    "figures-a-list": lambda: _resealed(released=[1]),
    "figures-a-string": lambda: _resealed(released="figures"),
}


@pytest.mark.parametrize("damage", list(_DAMAGE))
def test_a_damaged_ledger_refuses_the_writer_and_withholds_from_the_reader(damage):
    calls: list = []
    release.freeze(IDENTITY, _score(), lambda: NOW)
    release.LEDGER_FILE.write_text(_DAMAGE[damage]())
    assert release.published_release(SHA, 0.36) == (None, True)
    with pytest.raises(ReleaseRefused):
        release.freeze(IDENTITY, _score(calls), lambda: NOW)
    assert not calls


@pytest.mark.parametrize("text", payloads.INVALID.values(), ids=payloads.INVALID.keys())
def test_a_ledger_outside_the_schema_is_never_read_or_acted_on(text):
    """A ledger's digest may match: it vouches for nothing but the bytes under it."""
    calls: list = []
    release.LEDGER_FILE.write_text(text)
    before = release.LEDGER_FILE.read_bytes()
    with pytest.raises(ReleaseRefused):
        release._decode(text)
    with pytest.raises(ReleaseRefused):
        release.read_ledger()
    assert release.published_release(payloads.SHA, payloads.THRESHOLD) == (None, True)
    with pytest.raises(ReleaseRefused):
        release.freeze(payloads.IDENTITY, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before


@pytest.mark.parametrize("text", payloads.VALID.values(), ids=payloads.VALID.keys())
def test_a_ledger_inside_the_schema_is_read_exactly(text):
    body = json.loads(text)
    ledger = release._decode(text)
    assert (ledger.state, ledger.identity, ledger.evaluated_at, ledger.released) == (
        body["state"],
        body["identity"],
        body["evaluated_at"],
        body["released"],
    )


def test_every_field_shown_is_one_the_schema_allows():
    release.LEDGER_FILE.write_text(payloads.VALID["tn-20"])
    published, withheld = release.published_release(payloads.SHA, payloads.THRESHOLD)
    assert not withheld
    assert published.metrics == payloads.table(tn=20)
    assert set(published.metrics) == disclosure.RELEASED_KEYS
    assert published.evaluated_at == payloads.NOW


def test_a_ledger_that_is_not_text_reads_as_withheld():
    release.LEDGER_FILE.write_bytes(b"\xff\xfe not text")
    assert release.published_release(SHA, 0.36) == (None, True)


def test_the_writer_cannot_write_what_the_reader_would_refuse():
    """Figures for another threshold leave a readable reservation, not an unreadable ledger.

    The same figures come back on a retry, because nothing about the run has changed.
    """
    calls: list = []
    for _attempt in range(2):
        with pytest.raises(ReleaseRefused):
            release.freeze(IDENTITY, _score(calls, {**RELEASED, "threshold": 0.4}), lambda: NOW)
        assert release.read_ledger().state == release.RESERVED
    assert calls == [1, 1]


@pytest.mark.parametrize(
    "figures",
    [
        pytest.param({**RELEASED, "tp": 151}, id="cells-that-do-not-add-up"),
        pytest.param({**RELEASED, "positives": 299}, id="churners-that-do-not-add-up"),
        pytest.param({k: v for k, v in RELEASED.items() if k != "tn"}, id="a-cell-missing"),
        pytest.param({**RELEASED, "threshold": float("nan")}, id="a-threshold-that-is-not-one"),
        pytest.param({**RELEASED, "tp": 150.0}, id="a-count-that-is-not-whole"),
        pytest.param({}, id="nothing"),
        pytest.param([], id="not-a-record"),
    ],
)
def test_a_scorer_s_malformed_output_is_a_failure_not_the_rule_s_answer(figures):
    """It is not recorded as a withheld release. It does stay reserved: see the retry below."""
    with pytest.raises(ValueError, match="whole table") as raised:
        release.freeze(IDENTITY, _score(figures=figures), lambda: NOW)
    assert "150" not in str(raised.value) and "0.36" not in str(raised.value), "no figure"
    assert _state() == release.RESERVED
    assert release.published_release(SHA, 0.36) == (None, True)


def test_a_deterministic_scorer_failure_stays_reserved_on_every_retry():
    """The same code gives the same failure, and there is no other way out of ``reserved``."""
    malformed = _score(figures={**RELEASED, "tp": 151})
    for _attempt in range(3):
        with pytest.raises(ValueError, match="whole table"):
            release.freeze(IDENTITY, malformed, lambda: NOW)
        assert _state() == release.RESERVED
    assert release.published_release(SHA, 0.36) == (None, True)


def test_a_transient_failure_can_finish_under_the_identical_identity():
    """One scorer, one identity: the cause was outside everything the identity covers."""
    attempts: list = []

    def fails_once(_hold):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("the disk was full")
        return RELEASED

    with pytest.raises(OSError, match="disk"):
        release.freeze(IDENTITY, fails_once, lambda: NOW)
    assert _state() == release.RESERVED
    finished = release.freeze(IDENTITY, fails_once, lambda: "2030-01-01T00:00:00+00:00")
    assert finished.released == RELEASED
    assert finished.evaluated_at == NOW, "the reservation's date stands"
    assert len(attempts) == 2


def test_a_ledger_under_a_newer_policy_is_not_this_code_s_to_read():
    """Only the separate check lets an open ledger move ahead: this code knows one rule."""
    text = payloads.seal(payloads.open_body(policy=POLICY + 1))
    with pytest.raises(ReleaseRefused, match="another policy"):
        release._decode(text)
    release.LEDGER_FILE.write_text(text)
    assert release.published_release(SHA, 0.36) == (None, True)


@pytest.mark.parametrize(
    "identity",
    [{**IDENTITY, "model_sha256": "x"}, {**IDENTITY, "threshold": True}],
    ids=["a-hash-that-is-not-one", "a-threshold-that-is-not-one"],
)
def test_an_invalid_identity_reserves_nothing(identity):
    calls: list = []
    with pytest.raises(ReleaseRefused):
        release.freeze(identity, _score(calls), lambda: NOW)
    assert not calls
    assert _state() == release.OPEN


def test_a_clock_that_gives_a_bad_date_reserves_nothing():
    with pytest.raises(ReleaseRefused):
        release.freeze(IDENTITY, _score(), lambda: f"roc_auc={payloads.SENTINEL}")
    assert _state() == release.OPEN


def test_a_deleted_ledger_never_opens_a_new_release():
    calls: list = []
    release.LEDGER_FILE.unlink()
    with pytest.raises(ReleaseRefused):
        release.freeze(IDENTITY, _score(calls), lambda: NOW)
    assert not calls and not release.LEDGER_FILE.exists()
    assert release.published_release(SHA, 0.36) == (None, True), "not 'nothing was recorded'"


def test_a_missing_ledger_directory_refuses_too(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "LEDGER_FILE", tmp_path / "gone" / "ledger.json")
    with pytest.raises(ReleaseRefused):
        release.freeze(IDENTITY, _score(), lambda: NOW)
    assert not (tmp_path / "gone").exists()


# --- what a reader sees ---------------------------------------------------------------


def test_an_open_ledger_reads_as_nothing_recorded():
    assert release.published_release(SHA, 0.36) == (None, False)


def test_a_reservation_reads_as_withheld():
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    assert release.published_release(SHA, 0.36) == (None, True)


@pytest.mark.parametrize(
    ("sha", "threshold"),
    [("b" * 64, 0.36), (SHA, 0.37), ("", 0.36), (None, 0.36)],
    ids=["another-artifact", "another-threshold", "no-hash", "null-hash"],
)
def test_another_artifact_or_threshold_gets_no_figure(sha, threshold):
    release.freeze(IDENTITY, _score(), lambda: NOW)
    assert release.published_release(sha, threshold) == (None, False)


# --- the lock -------------------------------------------------------------------------


def test_a_second_run_inside_the_first_is_refused_not_queued():
    def score(hold: release.Hold) -> dict:
        with pytest.raises(ReleaseRefused, match="another"), release.exclusive():
            pass
        return RELEASED

    release.freeze(IDENTITY, score, lambda: NOW)
    assert _state() == release.PUBLISHED


def test_a_hold_is_useless_once_released():
    with release.exclusive() as hold:
        hold.require()
    with pytest.raises(ReleaseRefused):
        hold.require()


def test_a_hold_cannot_be_made_by_hand():
    with pytest.raises(ReleaseRefused):
        release.Hold(object())


_RACER = textwrap.dedent(
    """
    import json, pathlib, sys, time
    from churnsense.evaluation import release
    from churnsense.exceptions import ReleaseRefused

    ledger, log, gate, identity, figures = sys.argv[1:6]
    release.LEDGER_FILE = pathlib.Path(ledger)
    while not pathlib.Path(gate).exists():
        time.sleep(0.001)

    def score(hold):
        hold.require()
        with open(log, "a") as out:
            out.write("scored\\n")
        time.sleep(0.2)
        return json.loads(figures)

    try:
        got = release.freeze(json.loads(identity), score, lambda: "2026-11-06T00:00:00+00:00")
        print("frozen", got.identity["threshold"])
    except ReleaseRefused:
        print("refused")
    """
)


def _racer(log: Path, gate: Path, threshold: float) -> subprocess.Popen:
    figures = {**RELEASED, "threshold": threshold}
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _RACER,
            str(release.LEDGER_FILE),
            str(log),
            str(gate),
            json.dumps({**IDENTITY, "threshold": threshold}),
            json.dumps(figures),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_racing_processes_produce_one_scoring_and_one_release(tmp_path):
    """Real processes, half of them with another threshold: one identity wins outright."""
    log, gate = tmp_path / "log", tmp_path / "gate"
    racers = [_racer(log, gate, 0.36 if i % 2 else 0.37) for i in range(8)]
    time.sleep(2.0)  # let every process finish importing and wait at the gate
    gate.write_text("go")
    outputs = [racer.communicate(timeout=120)[0].split() for racer in racers]
    assert all(racer.returncode == 0 for racer in racers)
    assert log.read_text().count("scored") == 1, "the partition was read once"

    stored = json.loads(release.LEDGER_FILE.read_text())
    assert stored["state"] == release.PUBLISHED
    winner = stored["identity"]["threshold"]
    assert stored["released"]["threshold"] == winner
    frozen = [line[1] for line in outputs if line[0] == "frozen"]
    assert frozen, "someone must win"
    assert set(frozen) == {str(winner)}, "nobody else was told they had a release"


# --- dying at every step --------------------------------------------------------------

_DIER = textwrap.dedent(
    """
    import json, os, pathlib, sys
    from churnsense.evaluation import release

    ledger, step, when, identity, figures = sys.argv[1:6]
    release.LEDGER_FILE = pathlib.Path(ledger)
    original = release._write_atomic
    seen = {"n": 0}

    def dying(path, text):
        seen["n"] += 1
        if seen["n"] == int(step) and when == "before":
            os._exit(9)
        if seen["n"] == int(step) and when == "partial":
            # What _write_atomic leaves when it dies before the replace.
            (path.parent / f".{path.name}.wreck").write_text(text[:20])
            os._exit(9)
        original(path, text)
        if seen["n"] == int(step) and when == "after":
            os._exit(9)

    release._write_atomic = dying

    def score(hold):
        if when == "scoring":
            os._exit(9)
        return json.loads(figures)

    release.freeze(json.loads(identity), score, lambda: "2026-11-06T00:00:00+00:00")
    os._exit(0)
    """
)


def _die(step: int, when: str) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            _DIER,
            str(release.LEDGER_FILE),
            str(step),
            when,
            json.dumps(IDENTITY),
            json.dumps(RELEASED),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 9, done.stderr


#: (step, when): where the run dies, what state it leaves, what a reader then gets.
_CRASHES = [
    pytest.param(1, "before", "open", (None, False), id="before-the-reservation"),
    pytest.param(1, "partial", "open", (None, False), id="half-way-through-the-reservation"),
    pytest.param(1, "after", "reserved", (None, True), id="right-after-the-reservation"),
    pytest.param(0, "scoring", "reserved", (None, True), id="while-scoring"),
    pytest.param(2, "before", "reserved", (None, True), id="before-the-publication"),
    pytest.param(2, "partial", "reserved", (None, True), id="half-way-through-the-publication"),
]


@pytest.mark.parametrize(("step", "when", "state", "seen"), _CRASHES)
def test_a_run_that_dies_leaves_no_figure_and_recovers_by_running_again(step, when, state, seen):
    _die(step, when)
    assert _state() == state
    assert release.published_release(SHA, 0.36) == seen

    # Another evaluation cannot use the wreck, unless nothing was ever reserved.
    other = {**IDENTITY, "threshold": 0.37}
    if state == release.RESERVED:
        with pytest.raises(ReleaseRefused):
            release.freeze(other, _score(figures={**RELEASED, "threshold": 0.37}), lambda: NOW)
        assert _state() == release.RESERVED

    # The same command, with no flag and no reset, finishes the job.
    calls: list = []
    finished = release.freeze(IDENTITY, _score(calls), lambda: "2030-01-01T00:00:00+00:00")
    assert finished.state == release.PUBLISHED
    assert calls == [1]
    assert release.published_release(SHA, 0.36)[0].metrics == RELEASED
    if state == release.RESERVED:
        assert finished.evaluated_at == "2026-11-06T00:00:00+00:00", "frozen at the reservation"
    assert not list(release.LEDGER_FILE.parent.glob(".*")), "no temp file left"


def test_a_run_that_dies_right_after_publishing_has_published():
    _die(2, "after")
    assert _state() == release.PUBLISHED
    assert release.published_release(SHA, 0.36)[0].metrics == RELEASED
    calls: list = []
    release.freeze(IDENTITY, _score(calls), lambda: NOW)
    assert not calls, "nothing to score: it is done"


def test_a_scorer_that_raises_leaves_the_reservation_and_no_figure():
    def boom(_hold):
        raise RuntimeError("the model broke")

    with pytest.raises(RuntimeError):
        release.freeze(IDENTITY, boom, lambda: NOW)
    assert _state() == release.RESERVED
    assert release.published_release(SHA, 0.36) == (None, True)


def test_a_failed_write_leaves_the_previous_state_whole(monkeypatch):
    """No partial ledger: the replace is the only thing that can change what is read."""
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    before = release.LEDGER_FILE.read_bytes()

    def refuse(*_):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        release.freeze(IDENTITY, _score(), lambda: NOW)
    monkeypatch.undo()
    assert release.LEDGER_FILE.read_bytes() == before
    assert not [p.name for p in release.LEDGER_FILE.parent.glob(".*")]


# --- the identity ---------------------------------------------------------------------

_META = {
    "feature_columns": ["tenure", "MonthlyCharges"],
    "dropped_columns": ["gender"],
    "model_sha256": SHA,
    "threshold": 0.36,
    "model_key": "logistic",
    "calibrated": True,
    "calibration_method": "isotonic",
    "probability_epsilon": 0.0001,
}


def _identity(cfg: Config, meta: dict | None = None) -> dict:
    return release.identity_of(cfg, _META | (meta or {}), "2" * 64, "1" * 64)


def test_the_identity_is_stable_and_valid(cfg: Config):
    identity = _identity(cfg)
    assert identity == _identity(cfg)
    assert set(identity) == set(IDENTITY)
    assert identity["threshold"] == 0.36
    release.freeze(identity, _score(), lambda: NOW)
    assert release.published_release(SHA, 0.36)[0].metrics == RELEASED


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("preprocessing_sha256", lambda c: replace(c, random_seed=c.random_seed + 1)),
        ("preprocessing_sha256", lambda c: replace(c, split=replace(c.split, test_size=0.3))),
        (
            "preprocessing_sha256",
            lambda c: replace(c, features=replace(c.features, drop_columns=("x",))),
        ),
        ("model_config_sha256", lambda c: replace(c, training=replace(c.training, cv_folds=9))),
        ("model_config_sha256", lambda c: replace(c, training=replace(c.training, scoring="f1"))),
    ],
    ids=["seed", "split-size", "dropped-columns", "cv-folds", "scoring"],
)
def test_the_configuration_that_shapes_the_figures_changes_the_identity(cfg: Config, field, change):
    assert _identity(change(cfg))[field] != _identity(cfg)[field]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: replace(c, training=replace(c.training, n_jobs=c.training.n_jobs + 3)),
        lambda c: replace(c, dataset=replace(c.dataset, url="https://elsewhere.example/x.csv")),
        lambda c: replace(c, business=replace(c.business, retention_offer_cost=99.0)),
        lambda c: replace(c, paths=replace(c.paths, reports_dir=Path("/somewhere/else"))),
        lambda c: replace(c, log_level="DEBUG"),
    ],
    ids=["jobs", "download-url", "business", "paths", "logging"],
)
def test_what_cannot_change_a_figure_does_not_change_the_identity(cfg: Config, change):
    assert _identity(change(cfg)) == _identity(cfg)


@pytest.mark.parametrize(
    ("field", "meta"),
    [
        ("model_sha256", {"model_sha256": "b" * 64}),
        ("threshold", {"threshold": 0.37}),
        ("model_config_sha256", {"model_key": "forest"}),
        ("model_config_sha256", {"calibrated": False}),
        ("model_config_sha256", {"calibration_method": "platt"}),
        ("model_config_sha256", {"probability_epsilon": 0.001}),
        ("feature_schema_sha256", {"feature_columns": ["tenure"]}),
        ("feature_schema_sha256", {"dropped_columns": []}),
    ],
)
def test_what_the_artifact_records_changes_the_identity(cfg: Config, field, meta):
    assert _identity(cfg, meta)[field] != _identity(cfg)[field]


def test_the_data_and_the_split_change_the_identity(cfg: Config):
    other = release.identity_of(cfg, _META, "9" * 64, "8" * 64)
    assert other["split_digest"] != _identity(cfg)["split_digest"]
    assert other["dataset_sha256"] != _identity(cfg)["dataset_sha256"]


def _package_copy(tmp_path, monkeypatch) -> Path:
    """The fingerprinted sources as a package of their own, so a test can edit them."""
    package = tmp_path / "package"
    for name in (*release._EVALUATION_SOURCES, *release._DATA_SOURCES, *release._MODEL_SOURCES):
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((release._PACKAGE / name).read_bytes())
    monkeypatch.setattr(release, "_PACKAGE", package)
    return package


_SOURCES = [
    *(("evaluation_sha256", name) for name in release._EVALUATION_SOURCES),
    *(("data_code_sha256", name) for name in release._DATA_SOURCES),
    *(("model_code_sha256", name) for name in release._MODEL_SOURCES),
]


@pytest.mark.parametrize(("field", "source"), _SOURCES, ids=[name for _, name in _SOURCES])
def test_a_changed_implementation_is_refused_before_anything_is_scored(
    cfg: Config, tmp_path, monkeypatch, field, source
):
    """``score_held_out``, ``clean_frame``, ``make_splits``, ``ProbabilityClamp`` and the rest."""
    package = _package_copy(tmp_path, monkeypatch)
    first = _identity(cfg)
    release.freeze(first, _score(), lambda: NOW)
    frozen = release.LEDGER_FILE.read_bytes()

    with (package / source).open("a") as out:
        out.write("\n# one more line\n")
    second = _identity(cfg)
    assert [key for key in first if first[key] != second[key]] == [field]

    calls: list = []
    with pytest.raises(ReleaseRefused, match=field):
        release.freeze(second, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == frozen


def test_a_module_the_figures_do_not_depend_on_is_not_fingerprinted():
    sources = {*release._EVALUATION_SOURCES, *release._DATA_SOURCES, *release._MODEL_SOURCES}
    assert not sources & {"api/main.py", "viz.py", "insights.py", "evaluation/figures.py"}


#: What a release runs, from the files to the published table, and the hash helpers behind the two
#: hashes the identity trusts. Each is code whose edit could change what is scored or released, so
#: each has to live in a fingerprinted file. A list, not a call graph: a new step joins it by hand.
_ON_THE_SCORING_PATH = [
    download.sha256_of,
    download.verify_dataset,
    report.load_artifact,
    report.load_clean,
    report.features_and_target,
    report.make_splits,
    report.check_provenance,
    report.positive_scores,
    report.score_held_out,
    report.evaluate,
    disclosure.release,
    release.freeze,
    release.identity_of,
]


@pytest.mark.parametrize("function", _ON_THE_SCORING_PATH, ids=lambda f: f.__qualname__)
def test_every_function_on_the_scoring_path_is_in_a_fingerprinted_file(function):
    """The loader too: it picks the bytes the identity's model hash vouches for.

    An edit to an unfingerprinted ``load_artifact`` - its hash check dropped, the model wrapped -
    would score something else under the same identity.
    """
    sources = {*release._EVALUATION_SOURCES, *release._DATA_SOURCES, *release._MODEL_SOURCES}
    defined_in = Path(inspect.getsourcefile(function)).resolve().relative_to(release._PACKAGE)
    assert defined_in.as_posix() in sources


_VERSIONS = dict.fromkeys(release.NUMERICAL_PACKAGES, "1.0.0")
_LOCK = "".join(
    f"{name}==1.0.0 ; sys_platform == 'linux' \\\n    --hash=sha256:{'0' * 64}\n"
    for name in release.NUMERICAL_PACKAGES
)


@pytest.fixture
def locked_environment(tmp_path, monkeypatch) -> Path:
    """An installed set that matches a lock of this test's own."""
    (tmp_path / release.LOCK_FILE).write_text(_LOCK)
    monkeypatch.setattr(release, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(release.importlib.metadata, "version", _VERSIONS.__getitem__)
    return tmp_path / release.LOCK_FILE


@pytest.mark.parametrize("package", release.NUMERICAL_PACKAGES)
def test_a_changed_numerical_package_is_a_different_evaluation(
    cfg: Config, locked_environment, monkeypatch, package
):
    first = _identity(cfg)
    release.freeze(first, _score(), lambda: NOW)
    newer = {**_VERSIONS, package: "1.0.1"}
    monkeypatch.setattr(release.importlib.metadata, "version", newer.__getitem__)
    second = _identity(cfg)
    assert [key for key in first if first[key] != second[key]] == ["runtime_sha256"]
    calls: list = []
    with pytest.raises(ReleaseRefused, match="runtime_sha256"):
        release.freeze(second, _score(calls), lambda: NOW)
    assert not calls


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("python_version", "3.13.99"), ("machine", "another-machine")],
)
def test_another_interpreter_or_machine_is_a_different_evaluation(
    cfg: Config, locked_environment, monkeypatch, attribute, value
):
    first = _identity(cfg)
    monkeypatch.setattr(release.platform, attribute, lambda: value)
    assert _identity(cfg)["runtime_sha256"] != first["runtime_sha256"]


def test_another_lock_is_a_different_evaluation(cfg: Config, locked_environment):
    first = _identity(cfg)
    locked_environment.write_text(_LOCK + "# resolved again\n")
    assert _identity(cfg)["runtime_sha256"] != first["runtime_sha256"]


def test_the_fingerprints_read_bytes_not_the_syntax_tree_of_a_python_version():
    source = Path(release.__file__).read_text()
    assert "import ast" not in source
    assert "ast.dump" not in source


def test_the_supported_python_is_the_one_the_project_declares():
    declared = tomllib.loads((Path(release.__file__).parents[3] / "pyproject.toml").read_text())
    low, high = release.SUPPORTED_PYTHON
    assert declared["project"]["requires-python"] == f">={low[0]}.{low[1]},<{high[0]}.{high[1]}"


def test_the_real_lock_pins_every_numerical_package():
    assert set(release._locked_versions()) == set(release.NUMERICAL_PACKAGES)


# --- what is checked before anything is reserved ----------------------------------------


def test_the_locked_environment_passes(locked_environment):
    release.preflight_environment()


@pytest.mark.parametrize("package", release.NUMERICAL_PACKAGES)
def test_a_package_other_than_the_locked_one_is_refused(locked_environment, monkeypatch, package):
    other = {**_VERSIONS, package: "9.9.9"}
    monkeypatch.setattr(release.importlib.metadata, "version", other.__getitem__)
    with pytest.raises(ReleaseRefused, match=package):
        release.preflight_environment()


def test_an_unsupported_python_is_refused(locked_environment, monkeypatch):
    monkeypatch.setattr(release.sys, "version_info", (3, 14, 0))
    with pytest.raises(ReleaseRefused, match="supported"):
        release.preflight_environment()


def test_a_missing_package_is_refused(locked_environment, monkeypatch):
    def absent(name: str) -> str:
        raise release.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(release.importlib.metadata, "version", absent)
    with pytest.raises(ReleaseRefused, match="not installed"):
        release.preflight_environment()


@pytest.mark.parametrize("lock", ["", "numpy==1.0.0\n"], ids=["empty", "some-packages-unpinned"])
def test_a_lock_without_the_pins_is_refused(locked_environment, lock):
    locked_environment.write_text(lock)
    with pytest.raises(ReleaseRefused, match="pins no version"):
        release.preflight_environment()


def test_a_missing_lock_is_refused(locked_environment):
    locked_environment.unlink()
    with pytest.raises(ReleaseRefused, match="cannot be read"):
        release.preflight_environment()


def test_an_artifact_from_another_scikit_learn_is_refused(locked_environment):
    release.preflight_artifact({"sklearn_version": "1.0.0"})
    for meta in ({"sklearn_version": "1.0.1"}, {}):
        with pytest.raises(ReleaseRefused, match="scikit-learn"):
            release.preflight_artifact(meta)


# --- the command itself, end to end on synthetic data ---------------------------------


@pytest.fixture
def evaluate(pipeline_cfg, monkeypatch):
    """``make evaluate CONFIRM=yes``'s entry point, on the synthetic artifact and dataset."""
    sha = sha256_of(
        pipeline_cfg.paths.raw_dir / pipeline_cfg.dataset.filename,
    )
    record_provenance(pipeline_cfg, data_sha256=sha, split_digest=pinned_split_digest(pipeline_cfg))
    monkeypatch.setattr(report, "load_config", lambda _path: pipeline_cfg)
    monkeypatch.setattr(report, "configure_logging", lambda: None)
    # The checks of the environment have tests of their own; CI installs from a range.
    monkeypatch.setattr(release, "preflight_environment", lambda: None)
    pipeline_cfg.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    return lambda: report.main(["--confirm-test-read"])


def _counted(monkeypatch) -> list:
    calls: list = []
    real = report.score_held_out

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(report, "score_held_out", counted)
    return calls


def test_the_command_publishes_once_and_repeats_without_scoring(
    evaluate, pipeline_cfg, monkeypatch, capsys
):
    calls = _counted(monkeypatch)
    assert evaluate() == 0
    first = capsys.readouterr().out
    ledger = release.LEDGER_FILE.read_bytes()
    report_file = pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME
    assert report_file.is_file()
    written = report_file.read_bytes()

    assert evaluate() == 0
    assert calls == [1]
    assert capsys.readouterr().out == first
    assert release.LEDGER_FILE.read_bytes() == ledger
    assert report_file.read_bytes() == written


def test_a_lost_report_comes_back_from_the_ledger_without_scoring(
    evaluate, pipeline_cfg, monkeypatch
):
    calls = _counted(monkeypatch)
    assert evaluate() == 0
    report_file = pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME
    written = report_file.read_bytes()
    report_file.unlink()
    assert evaluate() == 0
    assert calls == [1]
    assert report_file.read_bytes() == written


def test_a_report_that_cannot_be_written_does_not_undo_the_release(
    evaluate, pipeline_cfg, monkeypatch, caplog
):
    original = release._write_atomic

    def refuse_the_report(path, text):
        if path.name == report.REPORT_FILENAME:
            raise OSError("disk full")
        original(path, text)

    monkeypatch.setattr(release, "_write_atomic", refuse_the_report)
    assert evaluate() == 1
    assert _state() == release.PUBLISHED
    assert not (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).exists()
    monkeypatch.undo()


#: Each step between the publication and the printed summary. There is no JSON among them:
#: the ledger is the only JSON this command writes, and it is written before any of these.
_AFTER_PUBLICATION = [
    pytest.param(report, "describe_release", id="describing"),
    pytest.param(report, "render_final_report", id="markdown"),
    pytest.param(report, "summary", id="summary"),
    pytest.param(release, "write_report", id="file-publication"),
]


@pytest.mark.parametrize(("module", "step"), _AFTER_PUBLICATION)
def test_a_failure_after_publication_keeps_the_release_and_says_so(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog, module, step
):
    real = getattr(module, step)

    def fails(*args, **kwargs):
        """Past the rehearsal, which runs the same steps: this has to fail after publication."""
        if _state() != release.PUBLISHED:
            return real(*args, **kwargs)
        raise ValueError(f"the renderer choked on {_SECRET}")

    calls = _counted(monkeypatch)
    monkeypatch.setattr(module, step, fails)
    with caplog.at_level("INFO"):
        assert evaluate() == 1

    assert _state() == release.PUBLISHED
    published = release.LEDGER_FILE.read_bytes()
    captured = capsys.readouterr()
    assert captured.out == "", "no summary for an output that was not produced"
    for text in (caplog.text, captured.out, captured.err):
        assert _SECRET not in text
        assert "Traceback" not in text
    assert "published and unchanged" in caplog.text
    assert "ValueError" in caplog.text
    assert "nothing was published" not in caplog.text

    # The same command, with nothing fingerprinted changed, renders it without scoring.
    monkeypatch.setattr(module, step, real)
    assert evaluate() == 0
    assert calls == [1], "a report is never a reason to score again"
    assert release.LEDGER_FILE.read_bytes() == published
    assert (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).is_file()


def test_a_changed_fingerprint_is_not_rendered_under_another_identity(
    evaluate, pipeline_cfg, monkeypatch, caplog
):
    """A lost report waits for the exact evaluation that made it, or stays lost."""
    calls = _counted(monkeypatch)
    assert evaluate() == 0
    published = release.LEDGER_FILE.read_bytes()
    report_file = pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME
    report_file.unlink()
    monkeypatch.setattr(release, "_evaluation_sha", lambda: "f" * 64)
    assert evaluate() == 1
    assert "evaluation_sha256" in caplog.text
    assert not report_file.exists()
    assert calls == [1]
    assert release.LEDGER_FILE.read_bytes() == published


def test_the_failure_says_nothing_was_reserved_when_nothing_was(evaluate, monkeypatch, caplog):
    def fails(_cfg):
        raise RuntimeError("no data")

    monkeypatch.setattr(report, "load_clean", fails)
    assert evaluate() == 1
    assert _state() == release.OPEN
    assert "nothing was reserved or published" in caplog.text
    assert "readers withhold" not in caplog.text


def test_the_failure_says_when_the_release_was_published_after_all(evaluate, monkeypatch, caplog):
    """The replace happened, then the directory's fsync failed: the ledger is what counts."""
    original = release._write_ledger

    def published_then_fails(ledger):
        original(ledger)
        if ledger.state == release.PUBLISHED:
            raise OSError("fsync failed")

    monkeypatch.setattr(release, "_write_ledger", published_then_fails)
    assert evaluate() == 1
    assert _state() == release.PUBLISHED
    assert "published and final" in caplog.text
    assert "nothing was" not in caplog.text


def test_the_failure_says_when_the_ledger_cannot_be_read(evaluate, monkeypatch, caplog):
    def corrupts(*_args):
        release.LEDGER_FILE.write_text("not json")
        raise RuntimeError("the disk went bad")

    monkeypatch.setattr(report, "score_held_out", corrupts)
    assert evaluate() == 1
    assert "cannot be read" in caplog.text
    assert "readers withhold" in caplog.text


def test_the_command_leaves_every_older_file_alone(evaluate, pipeline_cfg):
    """The historical report and figures stay as they are, and the sidecar is not rewritten."""
    reports = pipeline_cfg.paths.reports_dir
    planted = {
        reports / "final_evaluation.md": b"historical report",
        reports / "final_evaluation.json": b'{"metrics": "not read"}',
        pipeline_cfg.paths.figures_dir / "reliability.png": b"old",
        pipeline_cfg.paths.figures_dir / "precision_recall.png": b"old",
    }
    for path, content in planted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    sidecar = pipeline_cfg.paths.artifacts_dir / "model_meta.json"
    before = sidecar.read_bytes()

    assert evaluate() == 0

    for path, content in planted.items():
        assert path.read_bytes() == content, path
    assert sidecar.read_bytes() == before


def test_another_threshold_on_the_same_artifact_is_denied_by_the_command(
    evaluate, pipeline_cfg, monkeypatch, capsys
):
    """The hash covers only ``model.joblib``, so the sidecar's threshold could once move."""
    calls = _counted(monkeypatch)
    assert evaluate() == 0
    published = release.LEDGER_FILE.read_bytes()
    capsys.readouterr()

    edit_sidecar(pipeline_cfg.paths.artifacts_dir, threshold=0.41)
    assert evaluate() == 1
    assert calls == [1], "denied before a test row is scored"
    assert release.LEDGER_FILE.read_bytes() == published
    assert capsys.readouterr().out == ""


def test_the_refusal_prints_no_figure(evaluate, pipeline_cfg, capsys, caplog):
    assert evaluate() == 0
    capsys.readouterr()
    edit_sidecar(pipeline_cfg.paths.artifacts_dir, threshold=0.41)
    with caplog.at_level("INFO"):
        assert evaluate() == 1
    said = capsys.readouterr().out + caplog.text
    assert "threshold" in said
    assert "precision" not in said and "recall" not in said and "0.41" not in said


def test_nothing_is_printed_or_logged_before_the_publication(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog
):
    """Figures no other line could print: any of them in the output came from the scoring."""
    original = release._write_ledger
    sentinels = {"tn": 7001, "fp": 7103, "fn": 7207, "tp": 7309}
    n, positives = sum(sentinels.values()), sentinels["fn"] + sentinels["tp"]
    figures = disclosure.release({**sentinels, "threshold": 0.36, "n": n, "positives": positives})
    monkeypatch.setattr(report, "score_held_out", lambda *_: figures)

    def refuse_to_publish(ledger):
        if ledger.state == release.PUBLISHED:
            raise OSError("disk full")
        original(ledger)

    monkeypatch.setattr(release, "_write_ledger", refuse_to_publish)
    with caplog.at_level("INFO"):
        assert evaluate() == 1
    said = capsys.readouterr().out + caplog.text
    assert "precision" not in said and "recall" not in said
    for figure in [*sentinels.values(), n, positives, *figures.values()]:
        assert str(figure) not in said.replace(",", ""), figure
    assert _state() == release.RESERVED
    assert not (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).exists()


def test_the_scorer_itself_logs_and_prints_nothing(evaluate, monkeypatch, capsys, caplog):
    """The real scorer, stopped just before publication: not one record of what it saw."""
    original = release._write_ledger

    def refuse_to_publish(ledger):
        if ledger.state == release.PUBLISHED:
            raise OSError("disk full")
        original(ledger)

    monkeypatch.setattr(release, "_write_ledger", refuse_to_publish)
    with caplog.at_level("DEBUG", logger=report.logger.name):
        assert evaluate() == 1
    said = [r.getMessage() for r in caplog.records if r.name == report.logger.name]
    assert said == [
        "final evaluation failed (OSError): the ledger stays reserved: readers withhold, "
        "and only this identity can retry"
    ]
    assert capsys.readouterr().out == ""


def test_the_report_waits_for_the_lock_like_everything_else(tmp_path):
    """Or one run would sweep up another's temporary file as wreckage."""
    with release.exclusive(), pytest.raises(ReleaseRefused, match="another"):
        release.write_report(tmp_path / "report.md", "text")
    assert not (tmp_path / "report.md").exists()


_REAL_PREFLIGHT = release.preflight_environment


def test_an_incompatible_environment_reserves_nothing(evaluate, monkeypatch, caplog):
    calls = _counted(monkeypatch)
    monkeypatch.setattr(release, "preflight_environment", _REAL_PREFLIGHT)
    monkeypatch.setattr(
        release, "_installed_versions", dict.fromkeys(release.NUMERICAL_PACKAGES, "0").copy
    )
    before = release.LEDGER_FILE.read_bytes()
    assert evaluate() == 1
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before
    assert _state() == release.OPEN
    assert "differ from" in caplog.text


def test_an_artifact_of_another_scikit_learn_reserves_nothing(
    evaluate, pipeline_cfg, monkeypatch, caplog
):
    calls = _counted(monkeypatch)
    edit_sidecar(pipeline_cfg.paths.artifacts_dir, sklearn_version="0.0.1")
    before = release.LEDGER_FILE.read_bytes()
    assert evaluate() == 1
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before
    assert "scikit-learn" in caplog.text


_SECRET = "0.123456789"


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ValueError(f"y_proba holds {_SECRET}"), id="value-error"),
        pytest.param(RuntimeError(f"solver saw {_SECRET}"), id="any-other-failure"),
    ],
)
def test_a_transient_scoring_failure_says_nothing_and_may_retry_under_the_same_identity(
    evaluate, pipeline_cfg, monkeypatch, caplog, capsys, failure
):
    """The failure comes from outside the fingerprints, here a stand-in for one that passes."""

    def fails(*_args, **_kwargs):
        raise failure

    real = report.score_held_out
    monkeypatch.setattr(report, "score_held_out", fails)
    assert evaluate() == 1

    assert _state() == release.RESERVED
    assert release.read_ledger().released is None
    assert release.published_release(SHA, 0.36) == (None, True)
    assert not (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).exists()
    captured = capsys.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        assert _SECRET not in text
    assert captured.out == ""
    assert failure.__class__.__name__ in caplog.text

    # The same identity may try again, with no flag and no reset.
    monkeypatch.setattr(report, "score_held_out", real)
    assert evaluate() == 0
    assert _state() == release.PUBLISHED


_HELD_OUT_ROWS = SimpleNamespace(X_test=np.zeros((400, 1)), y_test=np.array([0, 1] * 200))


def _not_probabilities(rows):
    return np.column_stack([np.full(len(rows), np.nan), np.full(len(rows), np.nan)])


def _probabilities(rows):
    scores = np.linspace(0.01, 0.99, len(rows))
    return np.column_stack([1 - scores, scores])


def test_a_model_that_cannot_give_probabilities_stays_reserved_on_every_retry():
    """Past the rehearsal, on rows it did not see: the same artifact gives the same NaN."""

    class Broken:
        classes_ = (0, 1)
        predict_proba = staticmethod(_not_probabilities)

    for _attempt in range(3):
        with pytest.raises(ValueError, match="probabilities"):
            release.freeze(
                IDENTITY,
                lambda h: report.score_held_out(Broken(), _HELD_OUT_ROWS, 0.36, h),
                lambda: NOW,
            )
        assert _state() == release.RESERVED
    assert release.published_release(SHA, 0.36) == (None, True)
    assert release.read_ledger().released is None, "a failure is not a withheld release"


def test_a_model_that_only_failed_once_finishes_under_the_identical_identity():
    """One artifact, one identity: whatever went wrong was outside the fingerprints."""

    class Flaky:
        classes_ = (0, 1)
        calls = 0

        def predict_proba(self, rows):
            type(self).calls += 1
            return _not_probabilities(rows) if self.calls == 1 else _probabilities(rows)

    model = Flaky()
    with pytest.raises(ValueError, match="probabilities"):
        release.freeze(
            IDENTITY, lambda h: report.score_held_out(model, _HELD_OUT_ROWS, 0.36, h), lambda: NOW
        )
    assert _state() == release.RESERVED
    finished = release.freeze(
        IDENTITY, lambda h: report.score_held_out(model, _HELD_OUT_ROWS, 0.36, h), lambda: NOW
    )
    assert finished.state == release.PUBLISHED
    assert finished.released is not None


def test_a_table_the_rule_withholds_is_still_final(evaluate):
    """The other side: a figure under the floor is an answer, and it is not asked again."""
    release.freeze(dict(IDENTITY), lambda _h: None, lambda: NOW)
    assert _state() == release.PUBLISHED
    calls: list = []
    release.freeze(dict(IDENTITY), _score(calls), lambda: NOW)
    assert not calls


def test_a_failed_reservation_refuses_another_identity(evaluate):
    def fails(_hold):
        raise ValueError("no")

    with pytest.raises(ValueError):
        release.freeze(dict(IDENTITY), fails, lambda: NOW)
    calls: list = []
    with pytest.raises(ReleaseRefused, match="runtime_sha256"):
        release.freeze({**IDENTITY, "runtime_sha256": "e" * 64}, _score(calls), lambda: NOW)
    assert not calls
    assert _state() == release.RESERVED


def test_the_command_ignores_the_config_environment_variable(
    evaluate, monkeypatch, tmp_path, pipeline_cfg
):
    """``CHURNSENSE_CONFIG`` moves every path, so it must not reach this command."""
    asked: list = []
    monkeypatch.setenv("CHURNSENSE_CONFIG", str(tmp_path / "elsewhere.yaml"))
    monkeypatch.setattr(report, "load_config", lambda path: asked.append(path) or pipeline_cfg)
    assert evaluate() == 0
    assert asked == [DEFAULT_CONFIG_PATH]


def test_the_command_has_no_way_to_ask_for_another_release(capsys):
    """No reset, no force: argparse knows one flag."""
    for flag in ("--force", "--reset", "--release-again", "--ledger=x"):
        with pytest.raises(SystemExit) as stopped:
            report.main(["--confirm-test-read", flag])
        assert stopped.value.code == 2, flag
    capsys.readouterr()
    for source in (release.__file__, report.__file__):
        assert "os.environ" not in Path(source).read_text(), source
        assert "getenv" not in Path(source).read_text(), source


# --- the rehearsal before the reservation -----------------------------------------------


class _Artifact:
    """The real artifact, with its scores or its classes replaced as a test needs."""

    def __init__(self, model, scores=None, classes=None) -> None:
        self._model, self._scores = model, scores
        self.classes_ = model.classes_ if classes is None else classes

    def predict_proba(self, rows):
        real = self._model.predict_proba(rows)
        return real if self._scores is None else self._scores(real)


def _raises(_real):
    raise ValueError(f"the model choked on {_SECRET}")


def _filled(value):
    return lambda real: np.full(real.shape, value)


_BAD_SCORES = {
    "raises": _raises,
    "one-dimensional": lambda real: real[:, 1],
    "one-column": lambda real: real[:, :1],
    "three-columns": lambda real: np.column_stack([real, real[:, 1]]),
    "too-few-rows": lambda real: real[:-1],
    "nan": _filled(np.nan),
    "infinity": _filled(np.inf),
    "above-one": _filled(1.5),
    "below-zero": _filled(-0.5),
    "not-numbers": lambda real: np.full(real.shape, "x"),
}


def _use_artifact(monkeypatch, **replaced) -> None:
    real = report.load_artifact

    def load(directory):
        model, meta = real(directory)
        return _Artifact(model, **replaced), meta

    monkeypatch.setattr(report, "load_artifact", load)


def _assert_stopped_before_the_reservation(evaluate, monkeypatch, pipeline_cfg, capsys, caplog, at):
    calls = _counted(monkeypatch)
    before = release.LEDGER_FILE.read_bytes()
    with caplog.at_level("DEBUG"):
        assert evaluate() == 1
    assert release.LEDGER_FILE.read_bytes() == before, "byte for byte"
    assert _state() == release.OPEN
    assert not calls, "the held-out path never starts"
    assert not (pipeline_cfg.paths.reports_dir / report.REPORT_FILENAME).exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    for text in (caplog.text, captured.err):
        assert _SECRET not in text
        assert "Traceback" not in text
    assert f"validation rehearsal failed at {at}" in caplog.text
    assert "nothing was reserved or published" in caplog.text


@pytest.mark.parametrize("scores", _BAD_SCORES.values(), ids=_BAD_SCORES.keys())
def test_a_model_that_cannot_score_validation_stops_the_run_before_the_reservation(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog, scores
):
    _use_artifact(monkeypatch, scores=scores)
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "scoring"
    )


@pytest.mark.parametrize("classes", [(1, 0), (0, 1, 2), ("No", "Yes")], ids=repr)
def test_a_model_whose_second_column_is_not_churn_stops_the_run(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog, classes
):
    _use_artifact(monkeypatch, classes=classes)
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "scoring"
    )


def _refuses(*_args, **_kwargs):
    raise ValueError(f"it choked on {_SECRET}")


@pytest.mark.parametrize(
    ("attribute", "replacement", "at"),
    [
        pytest.param("evaluate", _refuses, "metrics", id="metrics-raise"),
        pytest.param(
            "evaluate",
            lambda *_a, **_k: SimpleNamespace(as_dict=dict),
            "metrics",
            id="metrics-missing",
        ),
        pytest.param(
            "evaluate",
            lambda *_a, **_k: SimpleNamespace(as_dict=lambda: {"tn": _SECRET}),
            "metrics",
            id="metrics-malformed",
        ),
        pytest.param("evaluate", lambda *_a, **_k: None, "metrics", id="metrics-not-a-result"),
        pytest.param("describe_release", _refuses, "describing", id="describing"),
        pytest.param("summary", _refuses, "summary", id="summary"),
        pytest.param("render_final_report", _refuses, "rendering", id="rendering"),
    ],
)
def test_a_defect_in_what_follows_the_scoring_stops_the_run_before_the_reservation(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog, attribute, replacement, at
):
    monkeypatch.setattr(report, attribute, replacement)
    _assert_stopped_before_the_reservation(evaluate, monkeypatch, pipeline_cfg, capsys, caplog, at)


def test_a_renderer_that_fails_only_for_a_table_stops_the_run_too(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog
):
    """A withheld report alone would not reach the part of the renderer most of it is."""
    real = report.render_final_report

    def fails_on_a_table(evaluation, meta, cfg=None):
        return _refuses() if evaluation.released else real(evaluation, meta, cfg)

    monkeypatch.setattr(report, "render_final_report", fails_on_a_table)
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "rendering"
    )


def test_a_ledger_that_cannot_be_serialized_stops_the_run_before_the_reservation(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog
):
    real = release._laid_out

    def fails_for_a_table(body):
        return _refuses() if body["state"] == release.PUBLISHED else real(body)

    monkeypatch.setattr(release, "_laid_out", fails_for_a_table)
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "serialization"
    )


def test_metadata_that_cannot_be_serialized_stops_the_run_before_the_reservation(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog
):
    real = report.load_artifact
    monkeypatch.setattr(
        report,
        "load_artifact",
        lambda directory: (real(directory)[0], {**real(directory)[1], "stray": float("nan")}),
    )
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "serialization"
    )


def test_a_file_that_cannot_be_written_stops_the_run_before_the_reservation(
    evaluate, pipeline_cfg, monkeypatch, capsys, caplog
):
    real = release._write_atomic

    def fails_for_the_rehearsal(path, text):
        return _refuses() if path.name == "rehearsal.md" else real(path, text)

    monkeypatch.setattr(release, "_write_atomic", fails_for_the_rehearsal)
    _assert_stopped_before_the_reservation(
        evaluate, monkeypatch, pipeline_cfg, capsys, caplog, "serialization"
    )


def test_the_rehearsal_gives_the_model_and_the_metrics_validation_rows_only(evaluate, monkeypatch):
    """Every row the model and the metrics see, with what the ledger said at that moment."""
    made: list = []
    asked: list = []
    measured: list = []
    real_split, real_load, real_evaluate = report.make_splits, report.load_artifact, report.evaluate

    def splitting(*args, **kwargs):
        made.append(real_split(*args, **kwargs))
        return made[-1]

    class Watched(_Artifact):
        def predict_proba(self, rows):
            asked.append((_state(), set(rows.index)))
            return super().predict_proba(rows)

    def loading(directory):
        model, meta = real_load(directory)
        return Watched(model), meta

    def evaluating(y_true, y_proba, threshold=0.5):
        measured.append((_state(), set(y_true.index)))
        return real_evaluate(y_true, y_proba, threshold=threshold)

    monkeypatch.setattr(report, "make_splits", splitting)
    monkeypatch.setattr(report, "load_artifact", loading)
    monkeypatch.setattr(report, "evaluate", evaluating)
    assert evaluate() == 0

    validation, test = set(made[0].X_val.index), set(made[0].X_test.index)
    assert validation and test and not validation & test
    for seen in (asked, measured):
        before = [rows for state, rows in seen if state == release.OPEN]
        assert before, "the rehearsal ran while the ledger was still open"
        assert all(rows == validation for rows in before), "and saw validation rows only"
        reading_test = [state for state, rows in seen if rows & test]
        assert reading_test == [release.RESERVED], "the test rows come once, after the reservation"


def test_the_object_the_rehearsal_gets_holds_no_test_row():
    frame, labels = pd.DataFrame({"a": [1]}), pd.Series([0])
    rows = report.ValidationRows(frame, labels)
    assert {field.name for field in fields(rows)} == {"X_val", "y_val"}
    assert not hasattr(rows, "X_test")
    assert not hasattr(rows, "y_test")
    assert not hasattr(rows, "__dict__"), "and nowhere to put one"


def test_the_rehearsal_s_code_names_no_test_partition():
    tree = ast.parse(Path(report.__file__).read_text())
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("rehearse", "positive_scores"):
        attributes = {n.attr for n in ast.walk(functions[name]) if isinstance(n, ast.Attribute)}
        assert not attributes & {"X_test", "y_test"}, name
    calls = [
        n
        for n in ast.walk(functions["main"])
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ValidationRows"
    ]
    assert len(calls) == 1
    passed = {n.attr for n in ast.walk(calls[0]) if isinstance(n, ast.Attribute)}
    assert passed == {"X_val", "y_val"}


def test_a_passing_rehearsal_changes_nothing_and_says_nothing(
    evaluate, pipeline_cfg, monkeypatch, tmp_path, capsys, caplog
):
    """Stopped at the moment the reservation would begin: nothing has moved by then."""
    temporary = tmp_path / "system-temp"
    temporary.mkdir()
    monkeypatch.setattr(release.tempfile, "tempdir", str(temporary))
    paths = pipeline_cfg.paths
    watched = (paths.artifacts_dir, paths.reports_dir, paths.figures_dir, paths.raw_dir)

    def everything() -> dict:
        return {
            path: path.read_bytes()
            for folder in (*watched, release.LEDGER_FILE.parent)
            for path in folder.rglob("*")
            if path.is_file()
        }

    before = everything()
    reached: list = []

    def stops(*_args):
        reached.append(everything())
        raise ReleaseRefused("stopped here by the test")

    monkeypatch.setattr(release, "freeze", stops)
    with caplog.at_level("DEBUG", logger=report.logger.name):
        assert evaluate() == 1
    assert reached == [before]
    assert not [p for p in reached[0] if p.name == "rehearsal.md"], "not even from an earlier run"
    assert not list(temporary.iterdir()), "the temporary output is gone"
    assert capsys.readouterr().out == ""
    said = [r.getMessage() for r in caplog.records if r.name == report.logger.name]
    assert said == [
        "final evaluation refused: stopped here by the test; nothing was reserved or published"
    ]


@pytest.mark.parametrize(
    ("classes", "scores", "complaint"),
    [
        pytest.param((1, 0), _probabilities, "classes", id="another-class-order"),
        pytest.param(
            (0, 1), lambda rows: _probabilities(rows)[:-1], "two probabilities", id="too-few-rows"
        ),
        pytest.param(
            (0, 1), lambda rows: _probabilities(rows)[:, 1], "two probabilities", id="one-dimension"
        ),
    ],
)
def test_the_test_rows_are_scored_under_the_same_checks_as_the_rehearsal(
    classes, scores, complaint
):
    """On the real path too, or a table could be computed from the wrong column."""

    class Model:
        classes_ = classes
        predict_proba = staticmethod(scores)

    with release.exclusive() as hold, pytest.raises(ValueError, match=complaint):
        report.score_held_out(Model(), _HELD_OUT_ROWS, 0.36, hold)


def test_the_rehearsal_cannot_catch_what_only_the_test_rows_cause(evaluate, monkeypatch):
    """Its limit, in code: a model that fails only on rows it did not see still reaches them."""
    held_out: list = []
    real_split = report.make_splits

    def splitting(*args, **kwargs):
        held_out.append(real_split(*args, **kwargs))
        return held_out[-1]

    real_load = report.load_artifact

    class FailsOnTestRows(_Artifact):
        def predict_proba(self, rows):
            if set(rows.index) & set(held_out[0].X_test.index):
                raise ValueError("a value only the test rows hold")
            return super().predict_proba(rows)

    monkeypatch.setattr(report, "make_splits", splitting)
    monkeypatch.setattr(
        report,
        "load_artifact",
        lambda directory: (FailsOnTestRows(real_load(directory)[0]), real_load(directory)[1]),
    )
    assert evaluate() == 1
    assert _state() == release.RESERVED


# --- a changed identity is not a retry --------------------------------------------------


def test_a_source_fix_after_a_reservation_is_refused_as_another_evaluation(
    cfg: Config, tmp_path, monkeypatch
):
    """The reason a deterministic failure can be permanent: fixing it changes the identity.

    A real edit to a real fingerprinted file, not a different scorer under the same identity.
    """
    package = _package_copy(tmp_path, monkeypatch)
    identity = _identity(cfg)

    def defect(_hold):
        raise ZeroDivisionError("the defect")

    with pytest.raises(ZeroDivisionError):
        release.freeze(identity, defect, lambda: NOW)
    reservation = release.LEDGER_FILE.read_bytes()

    source = package / "evaluation" / "report.py"
    before = source.read_bytes()
    source.write_bytes(before + b"\n# the fix\n")
    fixed = _identity(cfg)
    assert [key for key in identity if identity[key] != fixed[key]] == ["evaluation_sha256"]

    calls: list = []
    with pytest.raises(ReleaseRefused, match="evaluation_sha256"):
        release.freeze(fixed, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == reservation

    # Only the exact bytes are the same evaluation, and they still hold the defect.
    source.write_bytes(before)
    assert _identity(cfg) == identity
    with pytest.raises(ZeroDivisionError):
        release.freeze(_identity(cfg), defect, lambda: NOW)
    assert release.LEDGER_FILE.read_bytes() == reservation


@pytest.mark.parametrize(
    "drift",
    [
        pytest.param(
            lambda mp, _lock: mp.setattr(release.platform, "python_version", lambda: "3.12.99"),
            id="a-python-patch",
        ),
        pytest.param(
            lambda _mp, lock: lock.write_text(_LOCK + "# resolved again\n"),
            id="one-line-in-the-lock",
        ),
    ],
)
def test_drift_after_a_reservation_passes_preflight_and_then_is_refused(
    cfg: Config, locked_environment, monkeypatch, drift
):
    """Preflight checks versions; the identity holds exact ones. Only the exact bytes retry."""
    identity = _identity(cfg)

    def transient(_hold):
        raise OSError("the disk was full")

    with pytest.raises(OSError, match="disk"):
        release.freeze(identity, transient, lambda: NOW)
    reservation = release.LEDGER_FILE.read_bytes()

    with monkeypatch.context() as patched:
        drift(patched, locked_environment)
        release.preflight_environment()
        calls: list = []
        with pytest.raises(ReleaseRefused, match="runtime_sha256"):
            release.freeze(_identity(cfg), _score(calls), lambda: NOW)
        assert not calls
        assert release.LEDGER_FILE.read_bytes() == reservation

    locked_environment.write_text(_LOCK)
    assert release.freeze(_identity(cfg), _score(), lambda: NOW).state == release.PUBLISHED


# --- what a valid shape does not prove ---------------------------------------------------


def test_nothing_scored_reaches_the_identity_or_the_date(evaluate, monkeypatch):
    """The command's own run cannot carry a figure outside the table: they do not depend on it."""
    seen = []
    for cells in (
        {"tn": 500, "fp": 200, "fn": 150, "tp": 150},
        {"tn": 611, "fp": 89, "fn": 97, "tp": 203},
    ):
        release.LEDGER_FILE.write_text(release.open_ledger_text())
        monkeypatch.setattr(
            report,
            "score_held_out",
            lambda _model, _rows, threshold, _hold, cells=cells: payloads.table(
                **cells, threshold=threshold
            ),
        )
        assert evaluate() == 0
        stored = json.loads(release.LEDGER_FILE.read_text())
        seen.append(stored["identity"])
        assert release._valid_timestamp(stored["evaluated_at"])
    assert seen[0] == seen[1]


def test_a_chosen_value_in_a_fingerprint_is_valid_and_reaches_no_consumer():
    """The limit in ``SECURITY.md``: whoever hand-writes a ledger can choose a hash's digits.

    It is outside what the reader can check, so it is accepted; and a reader passes on only the
    date and the table, so such digits stay in the file.
    """
    text = payloads.seal(
        payloads.published_body(identity={**payloads.IDENTITY, "dataset_sha256": "8123" + "0" * 60})
    )
    assert release._decode(text).identity["dataset_sha256"].startswith("8123")
    release.LEDGER_FILE.write_text(text)
    published, _ = release.published_release(payloads.SHA, payloads.THRESHOLD)
    assert {field.name for field in fields(published)} == {"evaluated_at", "metrics"}
    assert "8123" not in str(published)
    assert set(HoldoutEvaluation.model_fields) == {
        "evaluated_at",
        "metrics",
        "churn_rate",
        "no_skill_accuracy",
    }


# --- a reservation has no way back ------------------------------------------------------

#: Names someone might try. None exists; the point is that setting them changes nothing.
_RESET_VARIABLES = (
    "CHURNSENSE_RESET",
    "CHURNSENSE_FORCE",
    "CHURNSENSE_ALLOW_RECOVERY",
    "CHURNSENSE_LEDGER",
    "RESET_RELEASE",
    "FORCE",
)


def _reserve() -> bytes:
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    return release.LEDGER_FILE.read_bytes()


def test_a_reservation_finishes_only_for_its_own_identity():
    _reserve()
    calls: list = []
    finished = release.freeze(IDENTITY, _score(calls), lambda: "2030-01-01T00:00:00+00:00")
    assert calls == [1]
    assert finished.state == release.PUBLISHED
    assert finished.evaluated_at == NOW, "the reservation's date stands"


@pytest.mark.parametrize("field", sorted(IDENTITY))
def test_a_reservation_refuses_any_other_identity_and_does_not_move(field):
    before = _reserve()
    calls: list = []
    other = {**IDENTITY, field: 0.37 if field == "threshold" else "f" * 64}
    with pytest.raises(ReleaseRefused, match=field):
        release.freeze(other, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before


def test_nothing_in_a_run_writes_a_reservation_back_to_open(monkeypatch):
    """Every write of a failing run, a refused one and a finishing one, seen as it happens."""
    states: list = []
    original = release._write_ledger

    def watched(ledger):
        states.append(ledger.state)
        original(ledger)

    def fails(_hold):
        raise RuntimeError("the model broke")

    monkeypatch.setattr(release, "_write_ledger", watched)
    with pytest.raises(RuntimeError):
        release.freeze(IDENTITY, fails, lambda: NOW)
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.37}, _score(), lambda: NOW)
    release.freeze(IDENTITY, _score(), lambda: NOW)
    assert states == [release.RESERVED, release.PUBLISHED]


def test_no_variable_resets_a_reservation(monkeypatch):
    before = _reserve()
    for name in _RESET_VARIABLES:
        monkeypatch.setenv(name, "1")
    calls: list = []
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.37}, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before
    assert release.published_release(SHA, 0.36) == (None, True)


def test_a_recovery_record_beside_the_ledger_unlocks_nothing():
    """The word of a file, however complete, is not an approval anyone can check."""
    before = _reserve()
    record = {
        "previous_ledger_digest": json.loads(before)["digest"],
        "previous_identity": IDENTITY,
        "reason": "the scoring code needed a fix",
        "reviewed_in": "https://github.example/pull/1",
        "no_figures_published": True,
        "recorded_at": NOW,
    }
    (release.LEDGER_FILE.parent / "release_recoveries.json").write_text(json.dumps([record]))
    calls: list = []
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.37}, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before


def test_the_command_has_no_way_to_reset_a_reservation(evaluate, monkeypatch, capsys):
    """Through the real entry point, with the variables set and each flag tried."""
    release._write_ledger(release.Ledger(release.RESERVED, IDENTITY, NOW, None))
    before = release.LEDGER_FILE.read_bytes()
    for name in _RESET_VARIABLES:
        monkeypatch.setenv(name, "1")
    calls = _counted(monkeypatch)
    assert evaluate() == 1, "this artifact's identity is not the reservation's"
    assert not calls
    for flag in ("--reset", "--force", "--reopen", "--recover", "--allow-recovery"):
        with pytest.raises(SystemExit) as stopped:
            report.main(["--confirm-test-read", flag])
        assert stopped.value.code == 2, flag
    capsys.readouterr()
    assert release.LEDGER_FILE.read_bytes() == before


def test_a_published_release_stays_final_under_the_same_attempts(monkeypatch):
    release.freeze(IDENTITY, _score(), lambda: NOW)
    before = release.LEDGER_FILE.read_bytes()
    for name in _RESET_VARIABLES:
        monkeypatch.setenv(name, "1")
    calls: list = []
    assert release.freeze(IDENTITY, _score(calls), lambda: NOW).released == RELEASED
    with pytest.raises(ReleaseRefused):
        release.freeze({**IDENTITY, "threshold": 0.37}, _score(calls), lambda: NOW)
    assert not calls
    assert release.LEDGER_FILE.read_bytes() == before


def test_the_command_refuses_a_dataset_other_than_the_pinned_one(
    evaluate, pipeline_cfg, monkeypatch
):
    calls = _counted(monkeypatch)
    moved = replace(pipeline_cfg, dataset=replace(pipeline_cfg.dataset, sha256="0" * 64))
    monkeypatch.setattr(report, "load_clean", lambda _cfg: _unpinned(pipeline_cfg))
    monkeypatch.setattr(report, "load_config", lambda _path: moved)
    assert evaluate() == 1
    assert not calls
    assert _state() == release.OPEN


def _unpinned(cfg: Config):
    from churnsense.data.loader import load_clean

    return load_clean(cfg)


def test_the_command_refuses_outside_a_source_tree(evaluate, monkeypatch, tmp_path):
    """An installed copy would keep its ledger somewhere nobody commits."""
    calls = _counted(monkeypatch)
    monkeypatch.setattr(release, "PROJECT_ROOT", tmp_path)
    assert evaluate() == 1
    assert not calls
    assert _state() == release.OPEN


def test_the_scorer_refuses_without_the_lock(evaluate, pipeline_cfg):
    with release.exclusive() as hold:
        pass
    with pytest.raises(ChurnSenseError):
        report.score_held_out(None, None, 0.36, hold)
    with pytest.raises(ChurnSenseError):
        report.score_held_out(None, None, 0.36, None)


# --- the file that ships --------------------------------------------------------------


def _carried_in_a_tree(text: str) -> None:
    """What a ledger in the repository must be: valid. Which valid state is not the suite's call.

    ``reserved`` is one of them. A failed run is made durable by committing its reservation,
    and a suite that refused that state would block the one change that preserves it.
    """
    assert release._decode(text).state in (release.OPEN, release.RESERVED, release.PUBLISHED)


def test_the_repository_carries_a_readable_ledger():
    """Absence refuses, so the file must exist; its state is whatever has been released."""
    path = Path(__file__).resolve().parents[1] / "governance" / "held_out_release.json"
    assert path.is_file()
    _carried_in_a_tree(path.read_bytes().decode("utf-8"))


@pytest.mark.parametrize("state", ["open", "reserved", "published", "withheld"])
def test_a_tree_may_carry_any_valid_state(state):
    _carried_in_a_tree(payloads.VALID[state])


def test_the_ledger_is_not_where_make_clean_reaches():
    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text()
    recipe = makefile.split("\nclean:")[1].split("\n\n")[0]
    assert "governance" not in recipe
    assert release.LEDGER_FILE.name == "held_out_release.json"
    source = Path(release.__file__).read_text()
    assert 'PROJECT_ROOT / "governance"' in source
