"""What earlier, immutable documents already say about the partitions - as an outsider reads them.

Two release reports, ``reports/final_evaluation.md`` and ``reports/held_out_release.md``,
are part of the published record and are never edited. Both state the validation sweep at
0.10, 0.20, ..., 0.80 (flagged customers exactly, precision and recall to two decimals),
and the first also states the *test* partition's churners per Contract and
InternetService segment. Separately, the IBM sample's own per-level totals are public.

None of that can be withdrawn, so what the public demo publishes is judged *together*
with it: a cut of the demo's sweep must not split one of those intervals into a group
smaller than the rule allows (``churnsense.score_disclosure.legacy_holds``), and a
training count must not complete a subtraction that gives a small validation group
(``churnsense.insights`` hides One year and Two year churn for that reason).

Everything here is transcribed from those public sources - nothing is read from the
dataset - and ``tests/test_legacy_disclosures.py`` checks the transcription against the
reports themselves. The two smallest legacy intervals (0.70 to 0.80, and 0.80 up) hold
fewer than ``MIN_CELL`` stayers: that disclosure predates the rule and stays published.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from churnsense.evaluation import release
from churnsense.exceptions import DemoDataError

#: The decimals the reports print precision and recall to.
_DECIMALS = 2


@dataclass(frozen=True, slots=True)
class LegacyPoint:
    """One row of the reports' "Sweep on the validation partition" table."""

    threshold: float
    flagged: int
    precision: float
    recall: float


#: The validation sweep as both release reports print it.
LEGACY_SWEEP: tuple[LegacyPoint, ...] = (
    LegacyPoint(0.10, 857, 0.40, 0.92),
    LegacyPoint(0.20, 710, 0.45, 0.86),
    LegacyPoint(0.30, 511, 0.55, 0.75),
    LegacyPoint(0.40, 371, 0.61, 0.61),
    LegacyPoint(0.50, 296, 0.67, 0.53),
    LegacyPoint(0.60, 171, 0.74, 0.34),
    LegacyPoint(0.70, 102, 0.77, 0.21),
    LegacyPoint(0.80, 49, 0.76, 0.10),
)

#: The thresholds the legacy sweep is stated at.
LEGACY_THRESHOLDS: tuple[float, ...] = tuple(point.threshold for point in LEGACY_SWEEP)

#: The cuts the public demo publishes the released model's validation sweep at: what the rule,
#: read with the thresholds above, keeps of the eleven cuts format 2 published
#: (``churnsense.demo.migrate``). The documents quote validation figures only at these, and at
#: the legacy thresholds (``tests/test_documentation_cuts.py``).
RELEASE_CUTS: tuple[float, ...] = (0.0, 0.30, 0.36, 0.50, 0.53)


@dataclass(frozen=True, slots=True)
class HeldOutSegment:
    """One row of ``final_evaluation.md``'s "Performance by segment" table (test partition)."""

    dimension: str
    segment: str
    customers: int
    churners: int
    flagged: int


TEST_SEGMENTS: tuple[HeldOutSegment, ...] = (
    HeldOutSegment("Contract", "Month-to-month", 773, 329, 467),
    HeldOutSegment("Contract", "One year", 300, 36, 9),
    HeldOutSegment("Contract", "Two year", 336, 9, 0),
    HeldOutSegment("InternetService", "DSL", 484, 97, 92),
    HeldOutSegment("InternetService", "Fiber optic", 613, 252, 384),
    HeldOutSegment("InternetService", "No", 312, 25, 0),
)

#: The public IBM Telco sample's own totals per level, (customers, churners): the
#: figures every description of that dataset quotes. Train + validation + test add up
#: to them (``tests/test_legacy_disclosures.py``, where the dataset is present).
POPULATION_MARGINALS: dict[tuple[str, str], tuple[int, int]] = {
    ("Contract", "Month-to-month"): (3875, 1655),
    ("Contract", "One year"): (1473, 166),
    ("Contract", "Two year"): (1695, 48),
    ("InternetService", "DSL"): (2421, 459),
    ("InternetService", "Fiber optic"): (3096, 1297),
    ("InternetService", "No"): (1526, 113),
}

#: The dimensions whose test-partition churners an immutable report states per segment.
TEST_DIMENSIONS: tuple[str, ...] = tuple(dict.fromkeys(row.dimension for row in TEST_SEGMENTS))


def churner_range(point: LegacyPoint, positives: int) -> tuple[int, int]:
    """The flagged churners ``point`` allows: every whole number whose precision and recall
    both round to what the report prints, given the partition's ``positives`` churners.

    The outsider's reading, so the true count is always inside; a check that holds for
    every count in the range holds for the true one too.
    """
    half = 0.5 * 10.0**-_DECIMALS + 1e-9
    found = [
        tp
        for tp in range(0, min(point.flagged, positives) + 1)
        if abs(tp / point.flagged - point.precision) <= half
        and abs(tp / positives - point.recall) <= half
    ]
    if not found:
        raise ValueError(f"no churner count fits the report's row at {point.threshold}")
    return found[0], found[-1]


def legacy_point(threshold: float) -> LegacyPoint | None:
    for point in LEGACY_SWEEP:
        if math.isclose(point.threshold, threshold, abs_tol=1e-9):
            return point
    return None


# --- which published figures the legacy facts describe ---------------------------------------


def describes(identity: Any) -> bool:
    """Whether published evidence bound to ``identity`` is of the released artifact - the one
    whose scores the legacy facts are about. Evidence of anything else (a test fixture, a
    retrained model) is judged on its own figures alone.

    Asked of the release workflow's one reader (``release.published_release``) with the
    artifact's hash and its operating point, both fixed by the artifact itself. Where that
    reader withholds - the ledger cannot be read, or a release is reserved or withheld - it
    is unknown whether the reports bind this evidence, and that is refused
    (``DemoDataError``): never taken for "nothing was released".
    """
    if not isinstance(identity, dict):
        return False
    model, threshold = identity.get("model_sha256"), identity.get("threshold")
    if not isinstance(model, str) or isinstance(threshold, bool):
        return False
    if not isinstance(threshold, int | float):
        return False
    published, withheld = release.published_release(model, float(threshold))
    if withheld:
        raise DemoDataError(
            "the release ledger cannot say whether the release reports describe this evidence "
            "(it cannot be read, or a release is reserved or withheld)"
        )
    return published is not None
