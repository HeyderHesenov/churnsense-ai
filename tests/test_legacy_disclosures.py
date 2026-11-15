"""The legacy facts are exactly what the immutable reports print - no more, no less.

``churnsense.legacy_disclosures`` transcribes the two release reports so that what the
public demo adds can be judged together with them. A transcription that drifted from the
reports would judge against the wrong facts, so it is read back from the reports here,
in CI: both are tracked.
"""

from __future__ import annotations

import json
import re

import pytest

from churnsense import legacy_disclosures as legacy
from churnsense.config import PROJECT_ROOT
from churnsense.evaluation import release
from churnsense.exceptions import DemoDataError, ReleaseRefused
from tests import ledger_payloads as payloads
from tests.conftest import LEGACY_REPORTS


def _rows(text: str, heading: str) -> list[list[str]]:
    """The body rows of the first markdown table after ``heading``."""
    after = text.split(heading, 1)[1]
    rows = []
    for line in after.splitlines()[1:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows[1:]  # without the header


def _number(text: str) -> float:
    return float(text.replace(",", ""))


@pytest.mark.parametrize("report", LEGACY_REPORTS)
def test_the_legacy_sweep_is_the_reports_own_table(report):
    text = (PROJECT_ROOT / report).read_text(encoding="utf-8")
    rows = _rows(text, "### Sweep on the validation partition")
    printed = [
        legacy.LegacyPoint(_number(r[0]), int(_number(r[1])), _number(r[2]), _number(r[3]))
        for r in rows
    ]
    assert tuple(printed) == legacy.LEGACY_SWEEP


def test_the_test_segments_are_the_final_reports_own_table():
    text = (PROJECT_ROOT / "reports/final_evaluation.md").read_text(encoding="utf-8")
    rows = _rows(text, "### Performance by segment at threshold 0.36")
    printed = tuple(
        legacy.HeldOutSegment(
            r[0], r[1], int(_number(r[2])), int(_number(r[3])), int(_number(r[5]))
        )
        for r in rows
    )
    assert printed == legacy.TEST_SEGMENTS
    # No other report states the test partition per segment.
    other = (PROJECT_ROOT / "reports/held_out_release.md").read_text(encoding="utf-8")
    assert "Performance by segment" not in other


def test_every_population_marginal_is_a_level_the_test_report_states():
    assert set(legacy.POPULATION_MARGINALS) == {
        (row.dimension, row.segment) for row in legacy.TEST_SEGMENTS
    }
    for (_dimension, _segment), (customers, churners) in legacy.POPULATION_MARGINALS.items():
        assert 0 < churners < customers
    # The IBM sample: 7,043 customers, 1,869 churners, whichever dimension adds them up.
    for dimension in legacy.TEST_DIMENSIONS:
        levels = [v for (d, _), v in legacy.POPULATION_MARGINALS.items() if d == dimension]
        assert (sum(n for n, _ in levels), sum(c for _, c in levels)) == (7043, 1869)


@pytest.mark.parametrize("point", legacy.LEGACY_SWEEP, ids=lambda p: f"{p.threshold:.2f}")
def test_the_churner_range_is_every_count_the_rounded_row_allows(point):
    low, high = legacy.churner_range(point, positives=374)
    assert low <= high
    for tp in range(0, point.flagged + 1):
        fits = abs(tp / point.flagged - point.precision) <= 0.005 + 1e-9 and (
            abs(tp / 374 - point.recall) <= 0.005 + 1e-9
        )
        assert fits == (low <= tp <= high), tp


def test_the_narrowest_rows_pin_their_counts():
    """The top of the legacy sweep is known exactly from the reports alone: 37 of 49."""
    assert legacy.churner_range(legacy.legacy_point(0.80), positives=374) == (37, 37)
    assert legacy.churner_range(legacy.legacy_point(0.70), positives=374) == (79, 79)


def test_a_row_no_count_fits_is_refused():
    with pytest.raises(ValueError, match="no churner count"):
        legacy.churner_range(legacy.LegacyPoint(0.5, 10, 0.99, 0.01), positives=374)


def _released_artifact() -> dict:
    """The released artifact as published evidence names it, from the repository's ledger."""
    ledger = json.loads((PROJECT_ROOT / "governance" / "held_out_release.json").read_text())
    return {
        "model_sha256": ledger["identity"]["model_sha256"],
        "threshold": ledger["identity"]["threshold"],
    }


def test_the_legacy_facts_describe_the_released_artifact_only(released_ledger):
    released = _released_artifact()
    assert re.fullmatch(r"[0-9a-f]{64}", released["model_sha256"])
    assert legacy.describes(dict(released, data_sha256="a" * 64, min_cell=20))
    assert not legacy.describes(dict(released, model_sha256="0" * 64))
    assert not legacy.describes(dict(released, threshold=None))
    assert not legacy.describes(None)


def test_an_open_ledger_names_no_release_and_binds_nothing():
    """Every test starts open (``tests/conftest.py``): nothing is released yet, so no report
    describes any evidence - the one state in which the legacy facts rightly bind nothing."""
    assert not legacy.describes(_released_artifact())


def test_a_ledger_that_cannot_be_read_is_a_refusal_not_an_open_release(monkeypatch):
    """Missing is never "open": evidence would otherwise be built or read without the reports'
    thresholds while the reports still state them. Refused, as the release workflow refuses."""
    monkeypatch.setattr(release, "LEDGER_FILE", PROJECT_ROOT / "governance" / "missing.json")
    with pytest.raises(DemoDataError, match="release ledger"):
        legacy.describes(_released_artifact())


def test_an_edited_or_reserved_ledger_is_a_refusal(released_ledger, _synthetic_ledger):
    """The ledger is read only through the release workflow's validating reader: an identity
    edited by hand fails its digest, and a reserved release says nothing yet - neither is
    taken for "nothing was released"."""
    released = _released_artifact()
    text = _synthetic_ledger.read_text(encoding="utf-8")
    _synthetic_ledger.write_text(text.replace(released["model_sha256"], "0" * 64))
    with pytest.raises(ReleaseRefused):
        release.read_ledger()
    with pytest.raises(DemoDataError, match="release ledger"):
        legacy.describes(released)
    _synthetic_ledger.write_text(payloads.seal(payloads.reserved_body()))
    with pytest.raises(DemoDataError, match="release ledger"):
        legacy.describes(released)
