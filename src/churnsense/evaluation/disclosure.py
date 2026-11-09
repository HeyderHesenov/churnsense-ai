"""What a recorded test evaluation may publish about the customers it scored.

**Why an aggregate needs a rule.** The test partition's outcomes never reach a
view (``churnsense.book``), but its customers do: each is listed and exported with
its features, a flag and a score, so a reader can sort them or group them on any
combination of attributes. Every published figure is an equation over those known
people, and a reader who knows this module can also read what it withholds.

**The rule follows from that.** A decision that depends on the outcomes is itself
an output. Hiding a level's rate because it is 0 or 1, then a sibling so the total
cannot give it back, tells that reader which case it was; checking each table on
its own leaves two tables to be read together, since the reader can build any cross
of them. So what could be protected only by a decision like that is not published
at all, whatever its value:

* no breakdown of the partition: no segment, no reliability bin, no part of it;
* no calibration error, which is one more equation over those bins;
* no Brier score, ROC-AUC or PR-AUC: each is one more equation over the labels,
  weighted by scores the reader has, and with three of them at four decimals a reader
  who knows most labels can fill in the rest. No test of the scores alone was found
  that bounds this, so they are withheld whatever the scores look like;
* no counts at a second cut, nor the cut that would have been best: the difference
  between two cuts is a group's count;
* no simulated money, a sum of the listed customers' charges over who churned.

**What is left, and the decision on it.**

========================  ==========================================================
Figure                    What it says, and why it may stay
========================  ==========================================================
n, positives              The partition's size and churners. A stratified split
                          already gives the second, to within one.
tn, fp, fn, tp and the    One two-by-two table whose sides are known. Published only
rates they give           if each of its four cells holds ``MIN_CELL`` customers;
                          otherwise nothing is. Withholding then says "some cell is
                          under ``MIN_CELL``", a range rather than a value, so it does
                          not name a side in which everybody churned or nobody did.
========================  ==========================================================

Every figure is rounded to ``DIGITS`` decimals before it leaves, so no reader gets
more than another. The floor is a count, not a proof: the table still says what its
sides sum to, as any aggregate does, and it says so about one evaluation only.

**Where it applies.** ``release`` is what ``evaluation.release`` freezes: it requires
whole counts that add up and a finite threshold, recomputes every rate from the counts,
and keeps only the keys above. A table under the floor gives ``None``, which is the rule's
final answer; anything that is not a whole table raises, so a failure is never recorded as
that answer. Reading back is stricter, because
a stored record has no reason to differ: ``is_released`` accepts only exactly what
``release`` writes, so a ledger holding anything else is refused as damaged, rather than
trusted to have the extra figures hidden later.

**What this does not do.** It limits what this application says. It is not differential
privacy, anonymity or secrecy: the dataset is a public one and the split is
reproducible, so it keeps nothing from someone who fetches them, and someone who can
write to this repository is outside it. One table is safe to publish only if there is
one: two tables at different cuts or from different models, on the same customers, say
what the customers between them did. ``evaluation.release`` enforces that one in its
own checkout, the procedure in ``SECURITY.md`` makes it the project's, and neither can
reach figures published before they existed (``docs/MODEL_CARD.md``, "Evaluation
history"). It is a rule about the held-out partition; training and validation
customers' own outcomes are shown by design.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Integral, Real
from typing import Any

#: Marks a figure as written under this rule; anything without it is not read.
POLICY = 2

#: Fewer customers than this, and no churn rate or churn count is reported for a
#: group: a rate over a handful of people is noise, not a finding.
MIN_CELL = 20

#: Decimals on every published share.
DIGITS = 4

#: No partition of a telecom sample holds this many customers; a count that does is
#: a damaged record.
_MAX_COUNT = 10**9

_CELLS = ("tn", "fp", "fn", "tp")

#: Everything a released figure may hold: the table above and nothing beside it.
RELEASED_KEYS = frozenset(
    {"threshold", "n", "positives", *_CELLS, "accuracy", "precision", "recall", "f1"}
)


def _count(value: Any) -> int | None:
    """A whole count, or ``None``: neither 1000.9 nor ``True`` is one."""
    if isinstance(value, bool) or not isinstance(value, Integral):
        return None
    number = int(value)
    return number if 0 <= number <= _MAX_COUNT else None


def _share(value: Any) -> float | None:
    """A finite share, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _rounded(value: float) -> float:
    return round(value, DIGITS)


def _whole_table(metrics: Any) -> dict[str, Any] | None:
    """The threshold and whole counts that add up, or ``None``. The floor is not asked here."""
    if not isinstance(metrics, Mapping):
        return None
    threshold = _share(metrics.get("threshold"))
    counts = {key: _count(metrics.get(key)) for key in ("n", "positives", *_CELLS)}
    if threshold is None or None in counts.values():
        return None
    tn, fp, fn, tp = (counts[key] for key in _CELLS)
    if tn + fp + fn + tp != counts["n"] or tp + fn != counts["positives"]:
        return None
    return {"threshold": threshold, **counts}


def released_metrics(metrics: Any, policy: Any) -> dict[str, Any] | None:
    """``metrics`` as they may be shown, or ``None``: the only way to read such a figure.

    The rates are worked out here from the counts rather than read, so what is
    shown agrees with them whatever the record says.
    """
    if type(policy) is not int or policy != POLICY:
        return None
    table = _whole_table(metrics)
    if table is None:
        return None
    tn, fp, fn, tp = (table[key] for key in _CELLS)
    if min(tn, fp, fn, tp) < MIN_CELL:
        return None
    return {
        **table,
        "accuracy": _rounded((tp + tn) / table["n"]),
        "precision": _rounded(tp / (tp + fp)),
        "recall": _rounded(tp / (tp + fn)),
        "f1": _rounded(2 * tp / (2 * tp + fp + fn)),
    }


def release(metrics: Any) -> dict[str, Any] | None:
    """What one evaluation may publish, or ``None``, which is the rule's answer and final.

    Output that is not one whole table is no answer at all. It is a failure of whatever
    produced it, so it raises, naming no figure, and no withheld release is recorded. Whether
    the reservation can then finish depends on the failure: ``SECURITY.md``, "The held-out
    release".
    """
    if _whole_table(metrics) is None:
        raise ValueError("a scorer returned something other than one whole table")
    return released_metrics(metrics, POLICY)


def is_released(record: Any) -> bool:
    """Whether ``record`` is exactly what ``release`` writes: its keys, types and values.

    The rule drops what it does not know, which is right for a scorer's output and wrong
    for a stored record, so its answer is compared and not used. ``==`` alone would let
    ``1`` stand for ``1.0``. Whatever the record holds, this answers and does not raise.
    """
    if not isinstance(record, dict) or set(record) != RELEASED_KEYS:
        return False
    written = released_metrics(record, POLICY)
    return written is not None and all(
        type(record[key]) is type(written[key]) and record[key] == written[key]
        for key in RELEASED_KEYS
    )


def churn_rate(released: Mapping[str, Any]) -> float:
    """Churners over customers, in a released figure."""
    return _rounded(released["positives"] / released["n"])


def no_skill_accuracy(released: Mapping[str, Any]) -> float:
    """The accuracy of flagging nobody, in a released figure."""
    return _rounded(1 - released["positives"] / released["n"])
