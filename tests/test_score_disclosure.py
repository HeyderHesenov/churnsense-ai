"""What a public copy may say about the validation partition's scores (E8).

Two cumulative counts at neighbouring cuts difference into the customers between
them: their number, how many churned, and - through the simulated money - the
sum of those churners' monthly charges. ``churnsense.score_disclosure`` keeps
every such interval to at least ``MIN_CELL`` churners and ``MIN_CELL`` stayers,
merging cuts where it must, and hides segment and band outcomes (with their
complements) that would break the same rule.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from churnsense import insights
from churnsense import score_disclosure as sd
from churnsense.config import load_config
from churnsense.evaluation.disclosure import MIN_CELL
from churnsense.evaluation.threshold import sweep_basis, sweep_from_basis
from churnsense.insights import Observed

BUSINESS = load_config().business


def _scores(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    p = rng.random(n).round(2)
    y = (rng.random(n) < p).astype(int)
    charges = rng.uniform(18.0, 120.0, n).round(2)
    return y, p, charges


@pytest.fixture(scope="module")
def fine():
    y, p, charges = _scores(1400, 3)
    return sweep_basis(y, p, charges), y, p


# --- the rule ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("churners", "stayers", "fine_"),
    [
        (0, 0, True),
        (20, 20, True),
        (25, 300, True),
        (19, 40, False),
        (40, 19, False),
        (0, 1, False),
        (1, 0, False),
    ],
)
def test_two_sided_means_none_or_twenty_of_each(churners, stayers, fine_):
    assert sd.two_sided(churners, stayers) is fine_


def test_the_fine_grid_breaks_the_rule_and_the_cuts_keep_it(fine):
    basis, _, _ = fine
    assert not sd.holds(basis), "the fixture's 0.01 grid has small steps, or this checks nothing"
    cuts = sd.safe_cuts(basis, required=(0.36,))
    assert cuts is not None and cuts[0] == 0.0 and 0.36 in cuts
    kept = sd.restrict(basis, cuts)
    assert sd.holds(kept)
    for _low, _high, churners, stayers in sd.intervals(kept):
        assert churners >= MIN_CELL and stayers >= MIN_CELL


def test_a_sweep_that_does_not_start_with_everyone_breaks_the_rule(fine):
    basis, _, _ = fine
    cuts = sd.safe_cuts(basis, required=(0.36,))
    assert sd.holds(sd.restrict(basis, cuts))
    assert not sd.holds(sd.restrict(basis, cuts[1:])), "what lies below the first cut is lost"


@pytest.mark.parametrize("cuts", [(0.36, 0.0), (0.0, 0.0, 0.36), (0.0, 0.365)])
def test_cuts_that_do_not_rise_along_the_grid_are_refused(fine, cuts):
    with pytest.raises(ValueError, match="rising thresholds"):
        sd.restrict(fine[0], cuts)


def test_the_cuts_are_the_fine_sweep_read_at_fewer_thresholds(fine):
    basis, _, _ = fine
    kept = sd.restrict(basis, sd.safe_cuts(basis, required=(0.36,)))
    full = sweep_from_basis(basis, BUSINESS).set_index("threshold")
    coarse = sweep_from_basis(kept, BUSINESS).set_index("threshold")
    pd.testing.assert_frame_equal(coarse, full.loc[list(kept.thresholds)], check_exact=True)


def test_a_preferred_cut_is_kept_where_it_fits_and_dropped_where_it_does_not(fine):
    basis, _, _ = fine
    assert 0.5 in sd.safe_cuts(basis, required=(0.36,), preferred=(0.5,))
    # 0.37 leaves fewer than twenty of someone between it and 0.36: it cannot be kept.
    tight = sd.safe_cuts(basis, required=(0.36,), preferred=(0.37,))
    assert tight is not None and 0.37 not in tight and 0.36 in tight


def test_a_preferred_cut_that_does_not_fit_drops_alone_and_the_others_stay(fine):
    basis, _, _ = fine
    cuts = sd.safe_cuts(basis, required=(0.36,), preferred=(0.37, 0.5))
    assert 0.5 in cuts and 0.37 not in cuts


def test_of_two_preferred_cuts_that_cannot_both_be_kept_the_first_named_wins(fine):
    basis, _, _ = fine
    alone = {c: sd.safe_cuts(basis, required=(0.36,), preferred=(c,)) for c in (0.45, 0.46)}
    assert all(c in cuts for c, cuts in alone.items()), "each fits alone, or this checks nothing"
    together = sd.safe_cuts(basis, required=(0.36,), preferred=(0.45, 0.46))
    assert not ({0.45, 0.46} <= set(together)), "both fit together: pick two closer cuts"
    assert 0.45 in together and 0.46 not in together
    flipped = sd.safe_cuts(basis, required=(0.36,), preferred=(0.46, 0.45))
    assert 0.46 in flipped and 0.45 not in flipped


def test_no_cuts_when_even_one_interval_cannot_hold_twenty_of_each():
    y, p, charges = _scores(1400, 3)
    few = np.where(p > 0.5, 0, y)  # every churner above 0.5 removed: nobody to fill the tail
    basis = sweep_basis(few, p, charges)
    assert sd.safe_cuts(basis, required=(0.5,)) is None
    assert sd.safe_cuts(sweep_basis(y[:30], p[:30], charges[:30])) is None


def test_a_required_cut_off_the_grid_cannot_be_kept(fine):
    assert sd.safe_cuts(fine[0], required=(0.365,)) is None


def test_the_cuts_are_maximal_no_further_grid_cut_can_be_added(fine):
    """Greedy from the left is optimal for a lower bound on every interval: check it."""
    basis, _, _ = fine
    cuts = sd.safe_cuts(basis, required=(0.36,))
    for extra in basis.thresholds:
        if extra in cuts:
            continue
        assert not sd.holds(sd.restrict(basis, tuple(sorted({*cuts, extra}))))


# --- reliability on the cuts -------------------------------------------------------------------


def test_reliability_bins_are_the_intervals_between_the_cuts(fine):
    basis, y, p = fine
    cuts = sd.safe_cuts(basis, required=(0.36,))
    bins = sd.reliability_on(y, p, cuts)
    assert [(b["low"], b["high"]) for b in bins] == list(zip(cuts, (*cuts[1:], 1.0), strict=True))
    for b, (_low, _high, churners, stayers) in zip(
        bins, sd.intervals(sd.restrict(basis, cuts)), strict=True
    ):
        assert b["customers"] == churners + stayers and b["churned"] == churners
        assert b["rate"] == pytest.approx(churners / b["customers"])
        assert b["low"] <= b["mean_predicted"] <= b["high"]
    assert sum(b["customers"] for b in bins) == len(y)


# --- segments at the threshold -----------------------------------------------------------------


def _segment_frame(n: int = 1400, seed: int = 5):
    y, p, _ = _scores(n, seed)
    rng = np.random.default_rng(seed + 1)
    frame = pd.DataFrame(
        {
            "Contract": rng.choice(
                ["Month-to-month", "One year", "Two year"], n, p=[0.6, 0.3, 0.1]
            ),
            "SeniorCitizen": rng.choice([0, 1], n, p=[0.8, 0.2]),
        }
    )
    return y, p, frame


def test_segment_cells_count_each_side_of_the_cut():
    y, p, frame = _segment_frame()
    cells = sd.segment_cells(y, p, frame, ("Contract",), 0.36)
    flagged = p >= 0.36
    for (dimension, segment), (tp, fp, fn, tn) in cells.items():
        rows = frame[dimension].astype(str).to_numpy() == segment
        assert (tp, fp) == (
            int((rows & flagged & (y == 1)).sum()),
            int((rows & flagged & (y == 0)).sum()),
        )
        assert (fn, tn) == (
            int((rows & ~flagged & (y == 1)).sum()),
            int((rows & ~flagged & (y == 0)).sum()),
        )


def _row(dimension, segment, cells):
    tp, fp, fn, tn = cells
    flagged, churners = tp + fp, tp + fn
    return {
        "dimension": dimension,
        "segment": segment,
        "customers": tp + fp + fn + tn,
        "flagged": flagged,
        "churners": churners,
        "churn_rate": churners / (tp + fp + fn + tn),
        "flagged_rate": flagged / (tp + fp + fn + tn),
        "precision": tp / flagged if flagged else None,
        "precision_low": None,
        "precision_high": None,
        "recall": tp / churners if churners else None,
        "recall_low": None,
        "recall_high": None,
        "roc_auc": 0.7,
        "unscanned": flagged == 0 and churners > 0,
    }


def _guard(cells):
    rows = [_row(d, s, c) for (d, s), c in cells.items()]
    return {(r["dimension"], r["segment"]): r for r in sd.guard_segments(rows, cells)}


def _shown(row) -> bool:
    return row["churners"] is not None


def test_a_segment_with_a_small_side_hides_its_outcomes_and_one_more_hides_with_it():
    cells = {
        ("Contract", "Month-to-month"): (200, 120, 60, 300),
        ("Contract", "One year"): (25, 40, 30, 200),
        ("Contract", "Two year"): (2, 6, 20, 100),  # 2 churners and 6 stayers flagged
    }
    guarded = _guard(cells)
    assert not _shown(guarded["Contract", "Two year"])
    # Alone it would be the totals minus the others: One year (the smaller) hides too.
    assert not _shown(guarded["Contract", "One year"])
    assert _shown(guarded["Contract", "Month-to-month"])
    for row in guarded.values():
        assert row["customers"] is not None and row["flagged"] is not None  # no outcome in them
        if not _shown(row):
            assert all(row[key] is None for key in insights.SEGMENT_OUTCOMES)


def test_an_empty_side_is_no_disclosure():
    cells = {
        ("Contract", "Month-to-month"): (200, 120, 60, 300),
        ("Contract", "Two year"): (
            0,
            0,
            25,
            280,
        ),  # nobody flagged; both stayers and churners >= 20
    }
    assert all(_shown(row) for row in _guard(cells).values())


def test_hiding_grows_until_the_hidden_segments_together_keep_the_rule():
    cells = {
        ("Contract", "a"): (300, 300, 300, 300),
        ("Contract", "b"): (5, 5, 30, 30),
        ("Contract", "c"): (6, 6, 30, 30),
        ("Contract", "d"): (25, 25, 30, 30),
    }
    guarded = _guard(cells)
    # b and c fail; together 11 flagged churners still fail; d (the smallest left) joins them.
    assert [s for (_, s), row in sorted(guarded.items()) if not _shown(row)] == ["b", "c", "d"]


def _hides_safely(seed: int) -> bool:
    """Brute force: every union of a dimension's hidden segments, read off the totals.
    Whether anything was hidden is the return value."""
    rng = np.random.default_rng(seed)
    cells = {("Contract", f"s{i}"): tuple(int(v) for v in rng.integers(0, 60, 4)) for i in range(5)}
    guarded = _guard(cells)
    hidden = [key for key, row in guarded.items() if not _shown(row)]
    totals = np.sum(list(cells.values()), axis=0)
    shown = np.sum([cells[k] for k, row in guarded.items() if _shown(row)] or [[0] * 4], axis=0)
    tp, fp, fn, tn = totals - shown  # all an outsider can solve for: the hidden set together
    assert sd.two_sided(tp, fp) and sd.two_sided(fn, tn)
    for key, row in guarded.items():
        if _shown(row):
            tp_, fp_, fn_, tn_ = cells[key]
            assert sd.two_sided(tp_, fp_) and sd.two_sided(fn_, tn_)
    assert len(hidden) != 1, "a single hidden segment is the totals minus the rest"
    return bool(hidden)


@pytest.mark.parametrize("seed", range(40))
def test_no_set_of_hidden_segments_can_be_solved_for_from_what_is_shown(seed):
    _hides_safely(seed)


def test_the_brute_force_seeds_do_hide_something():
    """Otherwise the search above would pass by showing everything."""
    hid = [_hides_safely(seed) for seed in range(40)]
    assert sum(hid) >= 30


# --- bands -----------------------------------------------------------------------------------------


def _band_guard(bands):
    overall = Observed(
        sum(b.customers for b in bands.values()), sum(b.churned for b in bands.values()), None
    )
    overall = Observed(overall.customers, overall.churned, overall.churned / overall.customers)
    return sd.guard_bands(bands, overall)


def _seen(customers, churned):
    return Observed(customers, churned, churned / customers if customers else None)


def test_bands_that_keep_the_rule_are_shown_as_they_are():
    bands = {
        "Low": _seen(3209, 256),
        "Moderate": _seen(1209, 429),
        "High": _seen(883, 537),
        "Critical": _seen(333, 273),
    }
    assert _band_guard(bands) == bands


def test_a_band_of_almost_only_churners_is_hidden_with_the_smallest_other():
    bands = {
        "Low": _seen(3000, 200),
        "Moderate": _seen(1200, 400),
        "High": _seen(500, 300),
        "Critical": _seen(130, 125),
    }  # 5 stayers
    guarded = _band_guard(bands)
    assert guarded["Critical"].rate is None and guarded["Critical"].churned is None
    assert guarded["Critical"].customers == 130
    assert guarded["High"].rate is None  # the smallest band left hides with it
    assert guarded["Low"] == bands["Low"] and guarded["Moderate"] == bands["Moderate"]


@pytest.mark.parametrize("seed", range(30))
def test_hidden_bands_cannot_be_solved_for(seed):
    rng = np.random.default_rng(seed)
    bands = {}
    for name in ("Low", "Moderate", "High", "Critical"):
        customers = int(rng.integers(20, 200))
        bands[name] = _seen(customers, int(rng.integers(0, customers + 1)))
    guarded = _band_guard(bands)
    hidden = [n for n, b in guarded.items() if b.rate is None]
    assert len(hidden) != 1
    churned = sum(bands[n].churned for n in hidden)
    customers = sum(bands[n].customers for n in hidden)
    assert sd.two_sided(churned, customers - churned)
    for band in guarded.values():
        if band.rate is not None:
            assert sd.two_sided(band.churned, band.customers - band.churned)


# --- every interval an outsider can form ----------------------------------------------------------


def test_every_pair_of_kept_cuts_differences_into_twenty_of_each(fine):
    """The subtraction attack on the kept cuts: any two, not only neighbours, and the money too."""
    basis, _, _ = fine
    kept = sd.restrict(basis, sd.safe_cuts(basis, required=(0.36,), preferred=(0.5,)))
    sweep = sweep_from_basis(kept, BUSINESS)
    rows = list(sweep.itertuples(index=False))
    for a, b in itertools.combinations(rows, 2):
        churners = a.true_positives - b.true_positives
        stayers = a.false_positives - b.false_positives
        assert churners >= MIN_CELL and stayers >= MIN_CELL
        # The money between two cuts is a sum over those same churners' charges.
        assert a.retained_value - b.retained_value > 0


# --- one group under two names ---------------------------------------------------------------


def test_segments_of_two_dimensions_that_are_the_same_customers_are_found():
    """Without internet is every customer whose add-ons say "No internet service"."""
    frame = pd.DataFrame(
        {
            "InternetService": ["No", "DSL", "No", "Fiber optic", "DSL"],
            "OnlineSecurity": ["No internet service", "Yes", "No internet service", "No", "No"],
            "gender": ["Male", "Female", "Female", "Male", "Male"],
        }
    )
    found = sd.identical_segments(frame, ("InternetService", "OnlineSecurity", "gender"))
    assert found == [(("InternetService", "No"), ("OnlineSecurity", "No internet service"))]


def test_a_hidden_segment_shown_under_another_name_is_refused():
    def row(dimension, segment, shown):
        outcomes = {"churners": 30 if shown else None}
        return {"dimension": dimension, "segment": segment} | outcomes

    same = [(("InternetService", "No"), ("OnlineSecurity", "No internet service"))]
    rows = [row("InternetService", "No", False), row("OnlineSecurity", "No internet service", True)]
    assert sd.betrayed(rows, same) == same
    both_hidden = [
        row("InternetService", "No", False),
        row("OnlineSecurity", "No internet service", False),
    ]
    assert sd.betrayed(both_hidden, same) == []
