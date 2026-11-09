#!/usr/bin/env python3
"""Refuse a change to the held-out release ledger that the release rules do not allow.

A best-effort consistency check. ``held-out-release-governance`` runs this on pull requests
and merge-queue groups for ``main``, and compares the ledger on the target branch with the
ledger the change would leave there. That catches what a local ledger cannot see: a release
made in another clone replacing the first, an old ``open`` ledger coming back through a
conflict resolution, a malformed publication on its way to becoming permanent.
It never runs an evaluation and never writes.

**What it is not.** It is not a required check today, and not a trusted boundary even if it
becomes one: on ``pull_request`` GitHub takes the workflow from the change itself, and a merged
change can replace this file. It catches accidents and ordinary mistakes, not a change
written to get past it. The maintainer has to read every change to this governance, and
nothing enforces that (``SECURITY.md``).

Nothing is imported from the project, so the check needs no dependency and no data, and
nothing outside the standard library has to be trusted to run it. So the schema below is a
second copy of ``churnsense.evaluation.release``'s, and ``tests/test_check_release_ledger.py``
holds the two together. Only rules and states are printed: never a figure, nor the name of an
unknown field, which could hold one.

=================  ===========================================================
target             what the change may leave
=================  ===========================================================
(no ledger)        ``open``: the file arriving for the first time
``open``           itself, ``reserved`` or ``published``; ``open`` again under the
                   next policy, with ``POLICY`` moved in this script and in
                   ``evaluation/disclosure.py`` by the same change
``reserved``       itself, or ``published`` for the same identity and date
``published``      itself, byte for byte
=================  ===========================================================

``open`` to ``published`` is the ordinary release: one run moves through ``reserved``
without committing it.

**No way back.** ``reserved`` never returns to ``open``. Undoing a reservation would need an
independent approval, and nothing here could verify one: a file saying a review took place
is only a file. So ``governance/release_recoveries.json`` is refused outright, in any change,
however complete it looks. There is no flag or variable for this.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

LEDGER = "governance/held_out_release.json"
RECOVERIES = "governance/release_recoveries.json"
#: The two files that state the policy in code. A new one has to move in both.
SCRIPT_PATH = "scripts/check_release_ledger.py"
RULE_PATH = "src/churnsense/evaluation/disclosure.py"
POLICY_LINE = re.compile(r"^POLICY = (\d+)$", re.MULTILINE)

OPEN, RESERVED, PUBLISHED = "open", "reserved", "published"

#: ``churnsense.evaluation.disclosure``'s rule, repeated because nothing here imports it.
FORMAT = 1
POLICY = 2
MIN_CELL = 20
DIGITS = 4
MAX_COUNT = 10**9
MAX_BYTES = 64 * 1024

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
#: The fields of a release's identity, as ``churnsense.evaluation.release`` writes them.
IDENTITY_KEYS = _HASH_KEYS | {"threshold"}
_CELLS = ("tn", "fp", "fn", "tp")
_COUNTS = ("n", "positives", *_CELLS)
#: Everything a published figure may hold: one whole-partition table and the rates it gives.
RELEASED_KEYS = frozenset({"threshold", *_COUNTS, "accuracy", "precision", "recall", "f1"})
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", re.ASCII)


class Refused(Exception):
    """The change is not allowed; the message names the rule and no figure."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _refuse_constant(_name: str) -> None:
    raise ValueError("no constants")


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


def _parse(text: str, what: str) -> Any:
    if len(text) > MAX_BYTES:
        raise Refused(f"{what} is too large")
    try:
        return json.loads(text, parse_constant=_refuse_constant, parse_float=_finite)
    except (ValueError, RecursionError) as exc:
        raise Refused(f"{what} is not valid JSON") from exc


def _is_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT


def _is_share(value: Any) -> bool:
    return type(value) is float and 0 <= value <= 1


def _valid_identity(identity: Any) -> bool:
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS:
        return False
    return _is_share(identity["threshold"]) and all(
        isinstance(identity[key], str) and _HEX64.fullmatch(identity[key]) for key in _HASH_KEYS
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


def _rates(tn: int, fp: int, fn: int, tp: int) -> dict[str, float]:
    """What the counts give, as ``disclosure.released_metrics`` works it out."""
    return {
        "accuracy": round((tp + tn) / (tn + fp + fn + tp), DIGITS),
        "precision": round(tp / (tp + fp), DIGITS),
        "recall": round(tp / (tp + fn), DIGITS),
        "f1": round(2 * tp / (2 * tp + fp + fn), DIGITS),
    }


def _table_problem(released: Any, threshold: float) -> str | None:
    """What is wrong with a published table for this threshold, or ``None``."""
    if not isinstance(released, dict) or set(released) != RELEASED_KEYS:
        return "holds figures other than the approved table"
    if not all(_is_count(released[key]) for key in _COUNTS):
        return "has a count that is not a whole number"
    tn, fp, fn, tp = (released[key] for key in _CELLS)
    if min(tn, fp, fn, tp) < MIN_CELL:
        return f"has a cell of fewer than {MIN_CELL} customers"
    if tn + fp + fn + tp != released["n"]:
        return "has cells that do not add up to its total"
    if tp + fn != released["positives"]:
        return "has a positive count other than its true positives and false negatives"
    if not _is_share(released["threshold"]) or released["threshold"] != threshold:
        return "has a table at a threshold other than its identity's"
    # The type too: a rate can round to exactly 1.0 or 0.0, and ``True == 1.0``.
    if any(
        type(released[key]) is not float or released[key] != rate
        for key, rate in _rates(tn, fp, fn, tp).items()
    ):
        return "has a rate its counts do not give"
    return None


def _state_problem(body: dict[str, Any]) -> str | None:
    """What is wrong with these fields for this state, or ``None``: the state's whole schema."""
    state, identity = body["state"], body["identity"]
    evaluated_at, released = body["evaluated_at"], body["released"]
    if state == OPEN:
        coherent = identity is None and evaluated_at is None and released is None
    elif state == RESERVED:
        coherent = _valid_identity(identity) and _valid_timestamp(evaluated_at) and released is None
    elif state == PUBLISHED:
        coherent = _valid_identity(identity) and _valid_timestamp(evaluated_at)
        if coherent and released is not None:
            return _table_problem(released, identity["threshold"])
    else:
        coherent = False
    return None if coherent else "does not hold together as a state"


def _policy_problem(state: Any, policy: Any) -> str | None:
    """A release is exactly this policy's. An ``open`` ledger may be one version ahead.

    This script runs from the target, so the change that brings the next policy is judged by
    the one before it. ``check_transition`` requires that change to carry its code as well.
    The project's own reader knows one policy, and refuses this until that code has landed.
    """
    if type(policy) is not int:
        return "has a policy that is not a version"
    if policy == POLICY or (state == OPEN and policy == POLICY + 1):
        return None
    return "was written under another policy"


def decode_ledger(text: str, where: str) -> dict[str, Any]:
    """A ledger valid in every field, or ``Refused``; ``where`` says which side it is.

    The schema comes before the digest: a matching digest says nothing about what is under
    it, and every value that gets as far as being digested is by then known to be shallow.
    """
    what = f"the ledger on {where}"
    body = _parse(text, what)
    if not isinstance(body, dict) or set(body) != _LEDGER_KEYS:
        raise Refused(f"{what} has the wrong fields")
    if type(body["format"]) is not int or body["format"] != FORMAT:
        raise Refused(f"{what} has an unknown format")
    problem = _policy_problem(body["state"], body["policy"]) or _state_problem(body)
    if problem:
        raise Refused(f"{what} {problem}")
    sealed = {key: value for key, value in body.items() if key != "digest"}
    if body["digest"] != hashlib.sha256(_canonical(sealed).encode("utf-8")).hexdigest():
        raise Refused(f"{what} does not match its digest")
    if text != json.dumps(body, indent=2, sort_keys=True, allow_nan=False) + "\n":
        raise Refused(f"{what} is not as the writer lays it out")
    return body


def check_transition(
    target_text: str | None,
    proposed_text: str | None,
    recovery_record: str | None = None,
    declared_policies: tuple[int | None, ...] = (),
) -> str:
    """What the change does, or ``Refused``. The rules are in this module's docstring.

    ``declared_policies`` is what the change's own files say ``POLICY`` is. It is compared and
    not trusted any further: a change can set them as it likes, so this catches a bump made by
    mistake or on its own, not one written to get past the check.
    """
    if recovery_record is not None:
        raise Refused(
            f"{RECOVERIES} is here, but there is no recovery protocol: it unlocks nothing"
        )
    if proposed_text is None:
        raise Refused("the ledger may not be deleted")
    proposed = decode_ledger(proposed_text, "the proposal")
    if proposed["policy"] != POLICY and set(declared_policies) != {proposed["policy"]}:
        raise Refused("the next policy needs its code in the same change")
    if target_text is None:
        if proposed["state"] != OPEN:
            raise Refused("the ledger can only arrive as open")
        return "the ledger arrives, open"

    target = decode_ledger(target_text, "the target")
    before, after = target["state"], proposed["state"]
    transition = f"{before} -> {after}"
    if target_text == proposed_text:
        return f"unchanged, {after}"
    if before == PUBLISHED:
        raise Refused("a published release is immutable")
    if before == OPEN:
        if proposed["policy"] < target["policy"]:
            raise Refused("a ledger never moves to an older policy")
        # Two open ledgers under one policy are the same bytes, and that was answered above.
        return "open under a newer policy" if after == OPEN else transition
    # before == RESERVED
    if after == OPEN:
        raise Refused("a reservation never goes back to open: it finishes for its own identity")
    if after != PUBLISHED:
        raise Refused("a reservation is not rewritten: it finishes for its own identity")
    if proposed["identity"] != target["identity"]:
        raise Refused("a reservation is published only for its own identity")
    if proposed["evaluated_at"] != target["evaluated_at"]:
        raise Refused("a reservation keeps its date")
    return transition


def _show(rev: str, path: str) -> str | None:
    """A file at ``rev`` in the current repository, or ``None`` if it is not there.

    Absent and unreadable are different answers: read as absent, a damaged target would let a
    ledger "arrive". As bytes, since text mode would tidy up line endings and fail with a
    traceback on the rest.
    """
    listed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}:{path}"],
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        return None
    done = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, check=False)
    if done.returncode != 0:
        raise Refused(f"{path} cannot be read")
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{path} is not text") from exc


def _declared_policies(rev: str) -> tuple[int | None, ...]:
    """``POLICY`` as each file in the change states it, or ``None`` where it states no one value."""
    found = (POLICY_LINE.findall(_show(rev, path) or "") for path in (SCRIPT_PATH, RULE_PATH))
    return tuple(int(lines[0]) if len(lines) == 1 else None for lines in found)


def _resolves(rev: str) -> bool:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{tree}}"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, help="the target branch as it is now")
    parser.add_argument("--proposed", required=True, help="the commit or tree the change leaves")
    args = parser.parse_args(argv)
    for rev in (args.target, args.proposed):
        if not _resolves(rev):
            print(f"refused: {rev} does not resolve", file=sys.stderr)
            return 1
    try:
        said = check_transition(
            _show(args.target, LEDGER),
            _show(args.proposed, LEDGER),
            _show(args.proposed, RECOVERIES),
            _declared_policies(args.proposed),
        )
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"allowed: {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
