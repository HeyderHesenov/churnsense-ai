"""The shipped 0.36 follows from tracked figures and the repository's own rule - in CI.

``reports/threshold_sweep.csv`` is no longer tracked (E8), so the full-grid check
(``tests/test_threshold.py``) runs only where ``make train`` wrote it. What stays tracked is
enough to check the recommendation itself:

* the README's operating-point table states the simulated net benefit at the published
  cuts 0.30, 0.36 and 0.50, at the configured assumptions;
* the immutable release reports state it at 0.10, 0.20, ..., 0.80, to the cent.

``recommend_threshold_from`` on every threshold those documents state must give the
threshold the release ledger records. And the rule itself is checked on a sweep built so
that the answer is known by construction - peak, band and tie-break each tested.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pandas as pd
import pytest

from churnsense.config import PROJECT_ROOT, load_config
from churnsense.evaluation.threshold import indifference_band, recommend_threshold_from
from tests.conftest import LEGACY_REPORTS

BUSINESS = load_config().business


def _money(text: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", text))


def _readme_points() -> dict[float, tuple[int, float]]:
    """(flagged, net benefit) per threshold of the README's "Measured sweep on validation"."""
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    table = text.split("**Measured sweep on validation:**", 1)[1].split("\n\n", 2)[1]
    points = {}
    for line in table.splitlines()[2:]:
        cells = [c.strip().strip("*") for c in line.strip().strip("|").split("|")]
        points[float(cells[0])] = (int(cells[1]), _money(cells[4]))
    return points


def _legacy_points() -> dict[float, tuple[int, float]]:
    """(flagged, net benefit) per threshold of the release reports' validation sweep."""
    text = (PROJECT_ROOT / LEGACY_REPORTS[1]).read_text(encoding="utf-8")
    table = text.split("### Sweep on the validation partition", 1)[1].split("\n\n", 2)[1]
    points = {}
    for line in table.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        points[float(cells[0])] = (int(cells[1]), _money(cells[6]))
    return points


def _sweep(points: dict[float, tuple[int, float]]) -> pd.DataFrame:
    rows = sorted(points.items())
    return pd.DataFrame(
        {
            "threshold": [t for t, _ in rows],
            "flagged": [f for _, (f, _) in rows],
            "net_benefit": [nb for _, (_, nb) in rows],
            # What ``_scenario_from_row`` reads; not used to choose, and not stated here.
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "intervention_cost": [f * BUSINESS.retention_offer_cost for _, (f, _) in rows],
            "retained_value": [nb + f * BUSINESS.retention_offer_cost for _, (f, nb) in rows],
        }
    )


def test_the_documented_sweep_recommends_the_released_threshold():
    readme, reports = _readme_points(), _legacy_points()
    assert set(readme) == {0.30, 0.36, 0.50}
    # Where both state a threshold they agree, to the dollar the README rounds to.
    for t in set(readme) & set(reports):
        assert readme[t][0] == reports[t][0]
        assert abs(readme[t][1] - reports[t][1]) < 1.0
    released = json.loads((PROJECT_ROOT / "governance/held_out_release.json").read_text())
    point = recommend_threshold_from(_sweep(reports | readme), BUSINESS)
    assert point.threshold == pytest.approx(released["identity"]["threshold"]) == 0.36


def test_the_documented_figures_are_priced_at_the_configured_assumptions():
    """Every stated net benefit is retained value less one offer per flagged customer."""
    for flagged, net in _readme_points().values():
        assert net > 0 and flagged > 0
    text = (PROJECT_ROOT / LEGACY_REPORTS[1]).read_text(encoding="utf-8")
    table = text.split("### Sweep on the validation partition", 1)[1].split("\n\n", 2)[1]
    for line in table.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        flagged, cost, retained, net = (_money(c) for c in (cells[1], cells[4], cells[5], cells[6]))
        assert cost == pytest.approx(flagged * BUSINESS.retention_offer_cost)
        assert net == pytest.approx(retained - cost)


# --- the rule, on a sweep whose answer is known by construction ---------------------------------

#: Net benefit by threshold: the peak at 0.30; 0.36 within one $50 offer of it; 0.40 not.
CONSTRUCTED = {0.10: 18_000.0, 0.20: 23_000.0, 0.30: 26_600.0, 0.36: 26_560.0, 0.40: 26_540.0}


def _constructed(net: dict[float, float]) -> pd.DataFrame:
    return _sweep({t: (1000 - int(t * 1000), nb) for t, nb in net.items()})


def test_the_highest_threshold_within_one_offer_of_the_peak_is_recommended():
    assert recommend_threshold_from(_constructed(CONSTRUCTED), BUSINESS).threshold == 0.36


def test_the_band_is_one_offers_cost_wide_and_moves_with_it():
    band = indifference_band(_constructed(CONSTRUCTED), BUSINESS)
    assert band["threshold"].tolist() == [0.30, 0.36]
    cheaper = replace(BUSINESS, retention_offer_cost=30.0)
    assert recommend_threshold_from(_constructed(CONSTRUCTED), cheaper).threshold == 0.30
    dearer = replace(BUSINESS, retention_offer_cost=70.0)
    assert recommend_threshold_from(_constructed(CONSTRUCTED), dearer).threshold == 0.40


def test_the_peak_itself_is_recommended_when_nothing_above_it_is_close():
    net = CONSTRUCTED | {0.36: 26_000.0, 0.40: 25_000.0}
    assert recommend_threshold_from(_constructed(net), BUSINESS).threshold == 0.30
