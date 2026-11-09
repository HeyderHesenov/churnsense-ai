"""Invented release ledgers, good and bad, for the two programs that read a real one.

``churnsense.evaluation.release`` and ``scripts/check_release_ledger.py`` share no code: the
script has to run alone. So the same payloads go through both, and they are built here with
nothing from either, or a bug in one could write the very ledger it then accepts.

Every figure is invented. ``SENTINEL`` is a number no valid ledger holds, so a refusal that
quotes a payload can be caught.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SENTINEL = 0.8123
NOW = "2026-11-07T00:00:00+00:00"
THRESHOLD = 0.36
SHA = "a" * 64

_HASH_KEYS = (
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
)
IDENTITY: dict[str, Any] = {**dict.fromkeys(_HASH_KEYS, SHA), "threshold": THRESHOLD}


def table(
    tn: int = 500, fp: int = 200, fn: int = 150, tp: int = 150, threshold: float = THRESHOLD
) -> dict[str, Any]:
    """One two-by-two table and the rates it gives, worked out here and not by the project."""
    n = tn + fp + fn + tp
    return {
        "threshold": threshold,
        "n": n,
        "positives": tp + fn,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
        "accuracy": round((tp + tn) / n, 4),
        "precision": round(tp / (tp + fp), 4),
        "recall": round(tp / (tp + fn), 4),
        "f1": round(2 * tp / (2 * tp + fp + fn), 4),
    }


def seal(body: dict[str, Any]) -> str:
    """``body`` as the writer lays a ledger out, with a digest that matches whatever it holds."""
    fields = {key: value for key, value in body.items() if key != "digest"}
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return json.dumps({**fields, "digest": digest}, indent=2, sort_keys=True) + "\n"


def open_body(**changes: Any) -> dict[str, Any]:
    return {
        "format": 1,
        "state": "open",
        "policy": 2,
        "identity": None,
        "evaluated_at": None,
        "released": None,
        **changes,
    }


def reserved_body(**changes: Any) -> dict[str, Any]:
    return open_body(state="reserved", identity=IDENTITY, evaluated_at=NOW) | changes


def published_body(**changes: Any) -> dict[str, Any]:
    return open_body(state="published", identity=IDENTITY, evaluated_at=NOW, released=table()) | (
        changes
    )


def _figures(**changes: Any) -> str:
    return seal(published_body(released={**table(), **changes}))


def _without(key: str) -> str:
    return seal(published_body(released={k: v for k, v in table().items() if k != key}))


def _identity(**changes: Any) -> str:
    return seal(published_body(identity={**IDENTITY, **changes}))


def _replaced(old: str, new: str) -> str:
    """A published ledger edited as text, for what ``json.dumps`` would never lay out."""
    text = seal(published_body())
    assert old in text, old
    return text.replace(old, new)


#: What may never sit beside the table, each holding a plausible value.
FORBIDDEN_FIGURES: dict[str, Any] = {
    "roc_auc": SENTINEL,
    "pr_auc": SENTINEL,
    "average_precision": SENTINEL,
    "brier": SENTINEL,
    "ece": SENTINEL,
    "segments": {"Contract": {"Two year": {"recall": SENTINEL}}},
    "segment_metrics": [{"segment": "Senior", "precision": SENTINEL}],
    "reliability_bins": [{"low": 0.0, "high": 0.1, "observed": SENTINEL}],
    "reliability_figure": "reports/figures/reliability.png",
    "pr_curve": [[SENTINEL, SENTINEL]],
    "best_threshold": SENTINEL,
    "threshold_sweep": [{"threshold": SENTINEL, "f1": SENTINEL}],
    "net_value": 26547.0,
    "simulated_money": 26547.0,
    "note": "an unknown field",
}

_CELL_UNDER_THE_FLOOR = {
    f"{cell}-19": seal(published_body(released=table(**{cell: 19})))
    for cell in ("tn", "fp", "fn", "tp")
}

_SOME_KEY_MISSING = {f"no-{key}": _without(key) for key in table()}

_NOT_A_WHOLE_COUNT = {
    f"{key}-{name}": _figures(**{key: value})
    for key in ("tn", "fp", "fn", "tp", "n", "positives")
    for name, value in (
        ("bool", True),
        ("float", 150.0),
        ("text", "150"),
        ("null", None),
        ("negative", -150),
    )
}

_WRONG_POLICY = {
    f"{state}-policy-{name}": seal(body(policy=value))
    for state, body in (("published", published_body), ("reserved", reserved_body))
    for name, value in (
        ("1", 1),
        ("3", 3),
        ("text", "2"),
        ("float", 2.0),
        ("bool", True),
        ("null", None),
    )
}

_WRONG_FORMAT = {
    f"{state}-format-{name}": seal(body(format=value))
    for state, body in (
        ("open", open_body),
        ("reserved", reserved_body),
        ("published", published_body),
    )
    for name, value in (
        ("0", 0),
        ("2", 2),
        ("text", "1"),
        ("float", 1.0),
        ("bool", True),
        ("null", None),
    )
}

_BAD_DATE = {
    f"date-{name}": seal(published_body(evaluated_at=value))
    for name, value in (
        ("a-word", "now"),
        ("empty", ""),
        ("null", None),
        ("a-number", 2026),
        ("a-figure", f"roc_auc={SENTINEL}"),
        ("a-figure-after-the-date", f"{NOW} roc_auc={SENTINEL}"),
        ("no-such-month", "2026-13-01T00:00:00+00:00"),
        ("no-such-day", "2026-02-30T00:00:00+00:00"),
        ("no-time", "2026-11-07"),
        ("not-utc", "2026-11-07T00:00:00+04:00"),
        ("zulu", "2026-11-07T00:00:00Z"),
        ("a-newline", f"{NOW}\n"),
    )
}

_BAD_IDENTITY = {
    "identity-uppercase": _identity(model_sha256="A" * 64),
    "identity-short": _identity(model_sha256="a" * 63),
    "identity-long": _identity(model_sha256="a" * 65),
    "identity-not-hex": _identity(model_sha256="z" * 64),
    "identity-a-number": _identity(model_sha256=7),
    "identity-null-hash": _identity(dataset_sha256=None),
    "identity-unknown-key": _identity(note="an unknown field"),
    "identity-missing-key": seal(
        published_body(identity={k: v for k, v in IDENTITY.items() if k != "runtime_sha256"})
    ),
    "identity-null": seal(published_body(identity=None)),
    "identity-a-list": seal(published_body(identity=[SHA])),
    "identity-threshold-text": _identity(threshold="0.36"),
    "identity-threshold-bool": _identity(threshold=True),
    "identity-threshold-null": _identity(threshold=None),
    "identity-threshold-below": _identity(threshold=-0.1),
    "identity-threshold-above": _identity(threshold=1.5),
    # With no table to disagree with, only the identity's own rule can catch these.
    "reserved-threshold-below": seal(reserved_body(identity={**IDENTITY, "threshold": -0.1})),
    "reserved-threshold-above": seal(reserved_body(identity={**IDENTITY, "threshold": 1.5})),
    "withheld-threshold-above": seal(
        published_body(identity={**IDENTITY, "threshold": 1.5}, released=None)
    ),
}

_ROUNDS_TO_ONE = table(10**6, 20, 20, 10**6)
_ROUNDS_TO_ZERO = table(tn=500, fp=10**8, fn=500, tp=20)

_DEEP = [[]]
for _ in range(500):
    _DEEP = [_DEEP]

#: Every ledger neither reader may accept, whichever way it got its digest.
INVALID: dict[str, str] = {
    # Figures that are not the approved table.
    **{f"forbidden-{key}": _figures(**{key: value}) for key, value in FORBIDDEN_FIGURES.items()},
    **_SOME_KEY_MISSING,
    # The table itself.
    **_CELL_UNDER_THE_FLOOR,
    **_NOT_A_WHOLE_COUNT,
    "n-not-the-sum": _figures(n=1001),
    "positives-not-tp-plus-fn": _figures(positives=301),
    "cells-beyond-any-partition": seal(published_body(released=table(tn=10**9 + 1))),
    "accuracy-wrong": _figures(accuracy=0.66),
    "precision-wrong": _figures(precision=0.4287),
    "recall-wrong": _figures(recall=0.51),
    "f1-wrong": _figures(f1=0.47),
    "precision-unrounded": _figures(precision=150 / 350),
    "precision-text": _figures(precision="0.4286"),
    "recall-null": _figures(recall=None),
    # Rates really do round to 1.0 and 0.0, so a bool or a whole number can equal one.
    **{
        f"{key}-as-{name}": seal(published_body(released={**_ROUNDS_TO_ONE, key: value}))
        for key in ("accuracy", "precision", "recall", "f1")
        for name, value in (("true", True), ("one", 1))
    },
    "precision-as-false": seal(published_body(released={**_ROUNDS_TO_ZERO, "precision": False})),
    "precision-as-zero": seal(published_body(released={**_ROUNDS_TO_ZERO, "precision": 0})),
    "figures-empty": seal(published_body(released={})),
    "figures-a-list": seal(published_body(released=[table()])),
    "figures-a-string": seal(published_body(released="figures")),
    # The threshold.
    "threshold-other-than-the-identity": _figures(threshold=0.4),
    "threshold-whole-number": seal(
        published_body(identity={**IDENTITY, "threshold": 1.0}, released=table(threshold=1))
    ),
    "threshold-out-of-range": seal(
        published_body(identity={**IDENTITY, "threshold": 1.5}, released=table(threshold=1.5))
    ),
    **_WRONG_POLICY,
    **_WRONG_FORMAT,
    **_BAD_DATE,
    **_BAD_IDENTITY,
    # Numbers JSON does not have. Sealed anyway: a matching digest vouches for nothing.
    "nan-figure": _figures(accuracy=float("nan")),
    "infinite-figure": _figures(accuracy=float("inf")),
    "negative-infinite-figure": _figures(accuracy=float("-inf")),
    "nan-threshold": _identity(threshold=float("nan")),
    "overflowing-figure": _replaced('"accuracy": 0.65', '"accuracy": 1e999'),
    # A state that does not hold together.
    "state-unknown": seal(published_body(state="approved")),
    "state-a-list": seal(published_body(state=["published"])),
    "state-null": seal(published_body(state=None)),
    "reserved-with-figures": seal(reserved_body(released=table())),
    "reserved-no-identity": seal(reserved_body(identity=None)),
    "reserved-bad-identity": seal(reserved_body(identity={**IDENTITY, "model_sha256": "x"})),
    "reserved-no-date": seal(reserved_body(evaluated_at=None)),
    "open-with-identity": seal(open_body(identity=IDENTITY)),
    "open-with-date": seal(open_body(evaluated_at=NOW)),
    "open-with-figures": seal(open_body(released=table())),
    "open-policy-1": seal(open_body(policy=1)),
    "open-policy-text": seal(open_body(policy="2")),
    "open-policy-bool": seal(open_body(policy=True)),
    "open-policy-two-ahead": seal(open_body(policy=4)),
    "open-policy-99": seal(open_body(policy=99)),
    "open-policy-enormous": seal(open_body(policy=10**30)),
    "open-policy-negative": seal(open_body(policy=-1)),
    "open-policy-float": seal(open_body(policy=3.0)),
    "open-policy-null": seal(open_body(policy=None)),
    # The outer shape.
    "unknown-field": seal(published_body(note="an unknown field")),
    "missing-state": seal({k: v for k, v in published_body().items() if k != "state"}),
    "missing-policy": seal({k: v for k, v in published_body().items() if k != "policy"}),
    "no-digest": json.dumps(published_body(), indent=2, sort_keys=True) + "\n",
    "digest-of-another-date": _replaced(NOW, "2026-11-08T00:00:00+00:00"),
    "digest-malformed": seal(published_body()).replace(
        json.loads(seal(published_body()))["digest"], "x"
    ),
    "deep-inside-a-field": seal(published_body(released=_DEEP)),
    # Not the bytes the writer lays out: each of these parses to a sealed, valid ledger.
    "a-key-given-twice": _replaced(
        '"state": "published"', '"state": "open",\n  "state": "published"'
    ),
    "compact-json": json.dumps(json.loads(seal(published_body()))),
    "unsorted-keys": json.dumps(
        dict(reversed(json.loads(seal(published_body())).items())), indent=2
    )
    + "\n",
    "trailing-space": seal(published_body()) + " ",
    "no-final-newline": seal(published_body()).rstrip("\n"),
    "windows-line-ends": seal(published_body()).replace("\n", "\r\n"),
    "another-spelling-of-a-number": _replaced('"accuracy": 0.65', '"accuracy": 0.650'),
    # Not a ledger at all.
    "not-json": "not json",
    "truncated": seal(published_body())[:80],
    "empty": "",
    "a-list": "[1, 2]",
    "a-number": "1",
    "null": "null",
    "deeply-nested": "[" * 30_000 + "]" * 30_000,
    "enormous-integer": "[" + "9" * 5000 + "]",
    "oversized": seal(published_body()) + " " * 65_536,
}

#: The ledgers both readers must accept, the floor's own boundary among them.
VALID: dict[str, str] = {
    "open": seal(open_body()),
    "reserved": seal(reserved_body()),
    "published": seal(published_body()),
    "withheld": seal(published_body(released=None)),
    **{
        f"{cell}-{size}": seal(published_body(released=table(**{cell: size})))
        for cell in ("tn", "fp", "fn", "tp")
        for size in (20, 21)
    },
    "every-cell-at-the-floor": seal(published_body(released=table(20, 20, 20, 20))),
    "rates-that-round-to-one": seal(published_body(released=_ROUNDS_TO_ONE)),
    "a-rate-that-rounds-to-zero": seal(published_body(released=_ROUNDS_TO_ZERO)),
    "another-threshold-on-both-sides": seal(
        published_body(identity={**IDENTITY, "threshold": 0.4}, released=table(threshold=0.4))
    ),
}
