"""The published evidence: the dataset's aggregates, and nothing of its customers.

``churnsense.evidence`` writes what a public demo shows of the dataset and
reads it back through a closed format. These tests hold three things:

* what is read back answers exactly as the dataset-backed source does;
* the file holds no customer - no ID, no column of a record - and no rate for
  a group of fewer than twenty;
* a file that breaks either rule, or belongs to another model, is refused.

Everything here is conftest's synthetic data; the real dataset is never read.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace

import pytest
from scripts import verify_deploy_bundle as bundle

from churnsense import evidence, insights
from churnsense import score_disclosure as sd
from churnsense.book import labelled, score_book
from churnsense.config import Config, Deployment
from churnsense.data import schema
from churnsense.demo import build
from churnsense.demo import customers as demo
from churnsense.evaluation.disclosure import MIN_CELL
from churnsense.exceptions import DemoDataError
from tests.conftest import SYNTHETIC_SHA256, edit_sidecar


@pytest.fixture(scope="module")
def shap_cache(dashboard_predictor, dashboard_parts, cfg: Config, tmp_path_factory):
    """``make explain``'s cache for the synthetic model: the ranking the evidence publishes."""
    from churnsense.explainability.shap_explain import background_sample, explanation_cache

    columns = dashboard_predictor.feature_columns
    cache = explanation_cache(
        dashboard_predictor.model,
        dashboard_predictor.meta,
        dashboard_parts.X_val[columns].head(10),
        background_sample(dashboard_parts.X_train[columns], cfg.random_seed),
        seed=cfg.random_seed,
        permutations=2,
    )
    path = tmp_path_factory.mktemp("evidence") / "shap_global.json"
    path.write_text(json.dumps(cache))
    return path


@pytest.fixture(scope="module")
def live(public_book, public_predictor, shap_cache) -> evidence.LiveEvidence:
    return evidence.LiveEvidence(public_book, public_predictor, shap_cache)


@pytest.fixture(scope="module")
def body(public_book, public_predictor, shap_cache) -> dict:
    return build.evidence_of(public_predictor, public_book, shap_cache)


@pytest.fixture(scope="module")
def published(body, public_predictor) -> evidence.PublishedEvidence:
    return evidence.parse(json.loads(evidence.dumps(body)), public_predictor)


# --- what is read back is what was computed ------------------------------------------------


def test_the_published_answers_are_the_computed_ones_with_the_score_rule_applied(
    live, published, public_book
):
    assert published.withheld is None is live.withheld
    assert published.dataset_customers == live.dataset_customers == len(public_book.frame)
    assert published.observed() == live.observed()
    assert published.band_observed() == sd.guard_bands(live.band_observed(), live.observed())
    assert published.band_observed() != live.band_observed(), "no band is hidden: nothing checked"
    # Validation customers per 0.05 of score would cut the sweep's intervals finer.
    assert published.histogram_observed() == [(None, None)] * insights.HISTOGRAM_BINS
    assert published.lifecycle() == live.lifecycle()
    assert published.collinearity() == live.collinearity()
    assert published.importance() == live.importance()
    assert published.sweep_withheld is None


def _peak(live, model) -> float:
    """The full sweep's peak under the configured assumptions: where the documents state one."""
    return insights.economics_from(live.sweep_basis(), model.config.business).peak.threshold


def _cuts(live, model):
    return sd.safe_cuts(
        live.sweep_basis(),
        required=(model.threshold,),
        preferred=(_peak(live, model), sd.AT_HALF),
    )


def test_the_published_sweep_is_the_computed_one_read_at_safe_cuts(
    live, published, public_predictor
):
    fine, cuts = live.sweep_basis(), _cuts(live, public_predictor)
    assert cuts is not None and len(cuts) > 2 and sd.AT_HALF in cuts

    assert published.sweep_basis() == sd.restrict(fine, cuts)
    assert sd.holds(published.sweep_basis())
    assert not sd.holds(fine), "the fine sweep keeps the rule anyway: nothing is checked"


def test_the_builder_asks_for_the_documents_peak_first_and_one_half_second(
    live, public_book, public_predictor, tmp_path, monkeypatch
):
    """A cut the documents state figures at must be a cut of the API, or the two together
    cut finer: the full sweep's peak (the model card's) first, then 0.5. Here the peak is
    0.13, which does not fit; on the real file it is 0.30, which does."""
    asked = []
    real = sd.safe_cuts

    def spy(basis, required=(), preferred=(), minimum=MIN_CELL, legacy=None):
        asked.append((tuple(required), tuple(preferred), legacy))
        return real(basis, required, preferred, minimum, legacy)

    monkeypatch.setattr(sd, "safe_cuts", spy)
    build.evidence_of(public_predictor, public_book, tmp_path / "absent.json")
    peak = _peak(live, public_predictor)
    # No release report describes this model: no legacy thresholds are added.
    assert asked == [((public_predictor.threshold,), (peak, sd.AT_HALF), None)]
    assert peak not in (public_predictor.threshold, sd.AT_HALF)


def _bands_of(model) -> list[tuple[str, float, float]]:
    return [(band.name, band.min, band.max) for band in model.config.risk_bands]


def test_an_outsider_can_solve_no_small_group_from_the_published_file(body, public_predictor):
    """The builder's own check, on what it wrote: nothing to find, though much is solvable."""
    from churnsense import disclosure_audit as audit

    published = json.loads(evidence.dumps(body))
    assert audit.published_violations(published, _bands_of(public_predictor)) == []
    _, solved = audit.score_violations(audit.facts_of(published, _bands_of(public_predictor)))
    assert solved >= 20


def test_the_same_check_finds_the_fine_sweep(body, live, public_predictor):
    """The control: the sweep at every 0.01, which E8 closed, gives customers away."""
    from dataclasses import asdict

    from churnsense import disclosure_audit as audit

    fine = json.loads(
        evidence.dumps(body | {"sweep_basis": asdict(live.sweep_basis()), "validation": None})
    )
    found = audit.published_violations(fine, _bands_of(public_predictor))
    assert any("validation" in line for line in found)


def test_a_file_an_outsider_could_solve_a_small_group_from_is_never_written(
    public_book, public_predictor, tmp_path, monkeypatch
):
    """Fail closed: the builder runs the outsider's arithmetic on its own file first."""
    from churnsense import disclosure_audit as audit

    seen = []

    def finds_one(body, bands, cells=None, legacy=None, external=False):
        seen.append((body, bands, cells))
        return ["['validation'] atoms from [0.3] outcome=1: 7"]

    monkeypatch.setattr(audit, "published_violations", finds_one)
    with pytest.raises(DemoDataError, match="could be solved for"):
        build.evidence_of(public_predictor, public_book, tmp_path / "absent.json")
    assert len(seen) == 1 and seen[0][2], "the segments' own counts are given to the check"


def test_a_group_hidden_under_one_name_and_shown_under_another_is_never_written(
    public_book, public_predictor, tmp_path, monkeypatch
):
    same = [(("InternetService", "No"), ("OnlineSecurity", "No internet service"))]
    monkeypatch.setattr(sd, "betrayed", lambda *_: same)
    with pytest.raises(DemoDataError, match="hidden under one name is shown under another"):
        build.evidence_of(public_predictor, public_book, tmp_path / "absent.json")


def test_every_breakdown_a_page_can_ask_for_is_published(live, published):
    asked = evidence.all_breakdowns()
    assert len(asked) == len(set(asked)) == 14 + 14 * 13
    for rows, columns in asked:
        assert published.breakdown(rows, columns) == live.breakdown(rows, columns)


def _validation_scores(book):
    rows = labelled(book.frame, "validation", shown=book.outcomes)
    return rows, rows[schema.TARGET].to_numpy(), rows["churn_probability"].to_numpy()


def test_the_published_validation_figures_are_the_computed_ones_on_the_cuts(
    live, published, public_book, public_predictor
):
    found, expected = published.validation_evaluation(), live.validation_evaluation()
    assert found.metrics == expected.metrics
    assert (found.churn_rate, found.no_skill_accuracy, found.ece) == (
        expected.churn_rate,
        expected.no_skill_accuracy,
        expected.ece,
    )
    rows, y, p = _validation_scores(public_book)
    cuts = published.sweep_basis().thresholds
    assert found.reliability == sd.reliability_on(y, p, cuts)
    assert found.reliability != expected.reliability
    cells = sd.segment_cells(y, p, rows, schema.VALIDATION_SEGMENTS, public_predictor.threshold)
    assert found.segments == sd.guard_segments(expected.segments, cells)
    hidden = [r for r in found.segments if r["churners"] is None and r["customers"] >= MIN_CELL]
    assert hidden, "no segment of twenty or more is hidden: the rule is not exercised"


def test_the_sweep_from_the_totals_is_the_sweep_over_customers_at_the_cuts(
    live, published, public_predictor, cfg
):
    """One sum in two orders: the counts are equal and the money agrees to rounding."""
    cuts = list(_cuts(live, public_predictor))
    for business in (cfg.business, replace(cfg.business, offer_success_rate=0.1, gross_margin=0.9)):
        computed, read = live.economics(business), published.economics(business)
        at = computed.sweep.set_index("threshold").loc[cuts].reset_index()
        for column in ("threshold", "flagged", "true_positives", "false_positives"):
            assert list(read.sweep[column]) == list(at[column])
        for column in ("retained_value", "intervention_cost", "net_benefit", "precision"):
            assert list(read.sweep[column]) == pytest.approx(list(at[column]), rel=1e-12, abs=1e-9)
        assert (read.customers, read.churners) == (computed.customers, computed.churners)
        for found in (read.recommended, read.peak, read.band_low, read.band_high):
            assert (found if isinstance(found, float) else found.threshold) in cuts
        assert set(read.sensitivity["threshold"]) <= set(cuts)


def test_a_book_too_small_for_the_rule_publishes_no_sweep(book, dashboard_predictor, tmp_path):
    """Eighty validation customers cannot keep twenty of each between two cuts: no sweep, no
    figure at the threshold, and the pages say why."""
    small = build.evidence_of(dashboard_predictor, book, tmp_path / "absent.json")
    assert small["sweep_basis"] is None and small["validation"] is None
    read = evidence.parse(json.loads(evidence.dumps(small)), dashboard_predictor)
    assert read.sweep_basis() is None and read.validation_evaluation() is None
    assert read.economics(dashboard_predictor.config.business) is None
    assert read.sweep_withheld == sd.SWEEP_WITHHELD


def test_the_published_text_is_the_same_on_every_run(
    public_book, public_predictor, shap_cache, body
):
    again = build.evidence_of(public_predictor, public_book, shap_cache)
    assert evidence.dumps(again) == evidence.dumps(body)


def test_without_a_cache_no_ranking_is_published(public_book, public_predictor, tmp_path):
    body = build.evidence_of(public_predictor, public_book, tmp_path / "absent.json")
    assert body["importance"] is None
    assert evidence.parse(json.loads(evidence.dumps(body)), public_predictor).importance() is None


# --- it holds no customer --------------------------------------------------------------------


def _walk(value):
    yield value
    if isinstance(value, dict):
        for inner in value.values():
            yield from _walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk(inner)


def test_the_published_file_names_no_customer(body, public_frame):
    text = evidence.dumps(body)
    ids = set(public_frame[schema.ID_COLUMN])
    assert not set(re.findall(r"[A-Za-z0-9-]+", text)) & ids
    assert schema.ID_COLUMN not in text


def test_no_key_of_the_published_file_is_a_customers_column(body):
    assert not bundle.holds_a_record(body)
    keys = {key for value in _walk(body) if isinstance(value, dict) for key in value}
    assert not keys & set(schema.RAW_COLUMNS)


def test_no_list_in_the_published_file_is_as_long_as_the_dataset(body, public_book):
    """Nothing is per customer: the longest list is the breakdowns, by far shorter than the public_book."""
    longest = max(len(value) for value in _walk(body) if isinstance(value, list))
    assert longest == len(evidence.all_breakdowns()) < len(public_book.frame)


def test_no_small_group_has_a_rate_anywhere_in_the_published_file(body):
    groups = [
        value
        for value in _walk(body)
        if isinstance(value, dict) and "customers" in value and isinstance(value["customers"], int)
    ]
    assert len(groups) > 500
    outcome_keys = {"churned", "rate", "lift", "churners", "churn_rate", "precision", "recall"}
    small = [group for group in groups if group["customers"] < MIN_CELL]
    assert small, "the fixture has small groups, or this checks nothing"
    for group in small:
        # A cell's low and high are its rate's interval; a reliability bin's are its edges.
        keys = outcome_keys | ({"low", "high"} if "row" in group else set())
        shown = {key for key in keys & group.keys() if group[key] is not None}
        assert not shown, group


# --- a file that breaks a rule is refused ----------------------------------------------------


def _first_cell(body, *, reported: bool) -> dict:
    for breakdown in body["breakdowns"]:
        for cell in breakdown["cells"]:
            if (cell["rate"] is not None) == reported and cell["customers"] > 0:
                return cell
    raise AssertionError("no such cell in the fixture")


def _hidden_cell_states_a_rate(body):
    cell = _first_cell(body, reported=False)
    cell.update(churned=1, rate=1 / max(cell["customers"], 1))


def _small_cell_is_reported(body):
    """A grid cell no ranking names, with an interval that fits its rate: only its size is wrong."""
    cell = next(
        cell
        for breakdown in body["breakdowns"]
        if breakdown["columns"] is not None
        for cell in breakdown["cells"]
        if cell["rate"] is not None and cell not in breakdown["ranked"]
    )
    cell.update(customers=MIN_CELL - 1, churned=1, rate=1 / (MIN_CELL - 1), low=0.0, high=1.0)


def _cell_gives_a_reason_that_does_not_fit(body):
    _first_cell(body, reported=False)["reason"] = "because"


def _rate_is_not_the_counts(body):
    cell = _first_cell(body, reported=True)
    cell["rate"] = min(1.0, cell["rate"] + 0.2)


# Each damage below breaks one rule and keeps every other: the rate stays inside
# its interval, a count stays a count, a cell stays well formed. A damage that
# also broke a second rule would pass even if the first rule's check were gone.


def _a_rate_inside_its_interval_is_not_the_counts(body):
    cell = next(
        cell
        for breakdown in body["breakdowns"]
        for cell in breakdown["cells"]
        if cell["rate"] is not None and cell["rate"] < cell["high"]
    )
    cell["rate"] = (cell["rate"] + cell["high"]) / 2


def _the_overall_rate_is_not_the_counts(body):
    observed = body["observed"]
    observed["rate"] += 0.05 if observed["rate"] < 0.5 else -0.05


def _a_large_group_is_hidden_as_too_few(body):
    total = next(
        total
        for breakdown in body["breakdowns"]
        for total in breakdown["row_totals"]
        if total["reason"] is None and total["customers"] >= MIN_CELL
    )
    total.update(reason="too_few", churned=None, rate=None, lift=None, low=None, high=None)


def _a_small_group_is_hidden_as_withheld(body):
    cell = next(
        cell
        for breakdown in body["breakdowns"]
        for cell in breakdown["cells"]
        if cell["reason"] == "too_few"
    )
    cell["reason"] = "withheld"


def _two_way_with_a_ranking(body):
    return next(b for b in body["breakdowns"] if b["columns"] is not None and b["ranked"])


def _a_ranked_group_is_a_total_not_a_cell(body):
    breakdown = _two_way_with_a_ranking(body)
    total = next(t for t in breakdown["row_totals"] if t["reason"] is None)
    breakdown["ranked"][0] = copy.deepcopy(total)


def _a_ranked_group_is_hidden(body):
    breakdown = _two_way_with_a_ranking(body)
    hidden = next(cell for cell in breakdown["cells"] if cell["reason"] is not None)
    breakdown["ranked"][0] = copy.deepcopy(hidden)


def _the_row_totals_do_not_add_up_to_the_partition(body):
    body["breakdowns"][0]["partition_customers"] += 1


def _a_validation_histogram_is_published(body):
    body["histogram_observed"] = [{"observed": None, "mean": None}] * insights.HISTOGRAM_BINS


def _last_two(body, name):
    """The sweep's last two cuts. The validation figures are dropped: they are read off
    the sweep, and would refuse the change for that reason instead."""
    body["validation"] = None
    values = body["sweep_basis"][name]
    return values, len(values) - 1


def _the_sweep_flags_a_churner_more_as_the_cut_rises(body):
    tp, i = _last_two(body, "true_positives")
    fn, _ = _last_two(body, "false_negatives")
    assert fn[i - 1] >= 1, "the fixture's highest cut misses no churner"
    tp[i], fn[i] = tp[i - 1] + 1, fn[i - 1] - 1


def _the_sweep_flags_a_stayer_more_as_the_cut_rises(body):
    fp, i = _last_two(body, "false_positives")
    fp[i] = fp[i - 1] + 1


def _the_sweep_charges_more_as_the_cut_rises(body):
    charges, i = _last_two(body, "flagged_churner_charges")
    charges[i] = charges[i - 1] + 1.0


def _a_breakdown_is_missing(body):
    body["breakdowns"].pop()


def _a_breakdown_is_repeated(body):
    body["breakdowns"][-1] = copy.deepcopy(body["breakdowns"][0])


def _levels_are_reordered(body):
    body["breakdowns"][0]["row_totals"].reverse()


def _an_unknown_dimension(body):
    body["breakdowns"][0]["rows"] = "customerID"


def _the_sweep_lists_are_of_different_lengths(body):
    body["sweep_basis"]["false_positives"].pop()


def _the_sweep_counts_more_than_its_customers(body):
    body["validation"] = None  # whose partition size would refuse it as well
    body["sweep_basis"]["customers"] = 1


def _a_small_segment_states_an_outcome(body):
    segment = body["validation"]["segments"][0]
    segment.update(customers=MIN_CELL - 1, churners=3)


def _a_small_reliability_bin_states_a_rate(body):
    bin_ = body["validation"]["reliability"][0]
    bin_.update(customers=3, churned=1, rate=1 / 3)


def _the_metrics_do_not_add_up(body):
    body["validation"]["metrics"]["tp"] += 1


def _a_ranking_of_an_unknown_feature(body):
    body["importance"]["ranking"][0]["feature"] = "customerID"


def _an_extra_key(body):
    body["rows"] = []


def _a_missing_key(body):
    del body["lifecycle"]


def _a_boolean_format(body):
    body["format"] = True


def _a_number_that_is_not_one(body):
    body["collinearity"] = "0.99"


def _a_sweep_step_holds_nineteen_churners(body):
    body["validation"] = None
    basis = body["sweep_basis"]
    tp, fn, charges = (
        basis["true_positives"],
        basis["false_negatives"],
        basis["flagged_churner_charges"],
    )
    moved = (tp[0] - tp[1]) - (MIN_CELL - 1)  # leave nineteen between the first two cuts
    assert moved > 0 and tp[1] + moved <= tp[0]
    tp[1] += moved
    fn[1] -= moved
    charges[1] = (charges[0] + charges[1]) / 2


def _the_sweep_does_not_start_at_everyone(body):
    body["validation"] = None
    basis = body["sweep_basis"]
    for name in (
        "thresholds",
        "true_positives",
        "false_positives",
        "false_negatives",
        "flagged_churner_charges",
    ):
        basis[name].pop(0)


def _a_reliability_bin_is_missing(body):
    body["validation"]["reliability"].pop()


def _a_reliability_bin_counts_one_more_customer(body):
    bin_ = body["validation"]["reliability"][0]
    bin_["customers"] += 1
    bin_["rate"] = bin_["churned"] / bin_["customers"]


def _a_reliability_bin_has_its_own_edges(body):
    """An edge moved between the cut and the next bin's mean: every count and mean still fits."""
    bins = body["validation"]["reliability"]
    bins[0]["high"] = bins[1]["low"] = (bins[1]["low"] + bins[1]["mean_predicted"]) / 2


def _the_metrics_are_not_the_sweep_at_the_threshold(body):
    metrics = body["validation"]["metrics"]
    metrics["tp"] += 1
    metrics["fp"] -= 1
    metrics["fn"] -= 1
    metrics["tn"] += 1


def _a_band_with_too_few_churners_is_shown(body):
    bands = body["band_observed"]
    shown = [name for name, band in bands.items() if band["rate"] is not None]
    small, large = sorted(shown, key=lambda name: bands[name]["customers"])[:2]
    moved = bands[small]["churned"] - (MIN_CELL - 1)
    for name, change in ((small, -moved), (large, moved)):
        bands[name]["churned"] += change
        bands[name]["rate"] = bands[name]["churned"] / bands[name]["customers"]


def _a_hidden_band_is_shown_leaving_one_hidden_alone(body):
    bands = body["band_observed"]
    hidden = [name for name, band in bands.items() if band["rate"] is None]
    assert len(hidden) == 2, "the fixture hides a small band and its complement"
    shown = max(hidden, key=lambda name: bands[name]["customers"])
    churned = bands[shown]["customers"] // 2
    bands[shown].update(churned=churned, rate=churned / bands[shown]["customers"])


def _validation_without_a_sweep(body):
    body["sweep_basis"] = None


def _the_metrics_are_of_another_cut(body):
    body["validation"]["metrics"]["threshold"] = 0.5


def _a_reliability_mean_outside_its_bin(body):
    bin_ = body["validation"]["reliability"][0]
    bin_["mean_predicted"] = bin_["high"] + 0.01


def _a_hidden_segment_states_a_precision(body):
    row = next(r for r in body["validation"]["segments"] if r["churners"] is None)
    row["precision"] = 0.5


def _a_segment_is_left_out(body):
    body["validation"]["segments"].pop()


def _a_band_counts_one_more_customer(body):
    band = next(b for b in body["band_observed"].values() if b["rate"] is not None)
    band["customers"] += 1
    band["rate"] = band["churned"] / band["customers"]


def _the_sweep_at_zero_flags_fewer_than_its_customers(body):
    body["validation"] = None
    body["sweep_basis"]["customers"] += 5


def _the_sweep_at_zero_misses_a_churner(body):
    """Counts still add up and every step still holds twenty: only 0 flagging all is broken."""
    body["validation"] = None
    basis = body["sweep_basis"]
    basis["true_positives"][0] -= 1
    basis["false_negatives"][0] += 1
    basis["false_positives"][0] += 1


def _the_threshold_is_not_a_cut(body):
    body["validation"] = None
    basis = body["sweep_basis"]
    threshold = body["identity"]["threshold"]
    i = next(i for i, t in enumerate(basis["thresholds"]) if abs(t - threshold) < 1e-9)
    for name in (
        "thresholds",
        "true_positives",
        "false_positives",
        "false_negatives",
        "flagged_churner_charges",
    ):
        basis[name].pop(i)


def _the_metrics_precision_is_not_its_counts(body):
    body["validation"]["metrics"]["precision"] += 0.01


def _a_float_format(body):
    body["format"] = 2.0


def _the_previous_format(body):
    body["format"] = 1


def _a_band_that_is_not_configured(body):
    body["band_observed"]["Extreme"] = body["band_observed"].pop("Critical")


def _an_overall_rate_for_a_small_group(body):
    body["observed"] = {"customers": MIN_CELL - 1, "churned": 2, "rate": 2 / (MIN_CELL - 1)}


@pytest.mark.parametrize(
    "damage",
    [
        _hidden_cell_states_a_rate,
        _small_cell_is_reported,
        _cell_gives_a_reason_that_does_not_fit,
        _rate_is_not_the_counts,
        _a_rate_inside_its_interval_is_not_the_counts,
        _the_overall_rate_is_not_the_counts,
        _a_large_group_is_hidden_as_too_few,
        _a_small_group_is_hidden_as_withheld,
        _a_breakdown_is_missing,
        _a_breakdown_is_repeated,
        _levels_are_reordered,
        _a_ranked_group_is_a_total_not_a_cell,
        _a_ranked_group_is_hidden,
        _the_row_totals_do_not_add_up_to_the_partition,
        _an_unknown_dimension,
        _a_validation_histogram_is_published,
        _the_sweep_flags_a_churner_more_as_the_cut_rises,
        _the_sweep_flags_a_stayer_more_as_the_cut_rises,
        _the_sweep_charges_more_as_the_cut_rises,
        _the_sweep_lists_are_of_different_lengths,
        _the_sweep_counts_more_than_its_customers,
        _a_small_segment_states_an_outcome,
        _a_small_reliability_bin_states_a_rate,
        _the_metrics_do_not_add_up,
        _a_ranking_of_an_unknown_feature,
        _an_extra_key,
        _a_missing_key,
        _a_boolean_format,
        _a_number_that_is_not_one,
        _a_sweep_step_holds_nineteen_churners,
        _the_sweep_does_not_start_at_everyone,
        _a_reliability_bin_is_missing,
        _a_reliability_bin_counts_one_more_customer,
        _a_reliability_bin_has_its_own_edges,
        _the_metrics_are_not_the_sweep_at_the_threshold,
        _a_band_with_too_few_churners_is_shown,
        _a_hidden_band_is_shown_leaving_one_hidden_alone,
        _the_previous_format,
        _validation_without_a_sweep,
        _the_metrics_are_of_another_cut,
        _a_reliability_mean_outside_its_bin,
        _a_hidden_segment_states_a_precision,
        _a_segment_is_left_out,
        _a_band_counts_one_more_customer,
        _the_sweep_at_zero_flags_fewer_than_its_customers,
        _the_sweep_at_zero_misses_a_churner,
        _the_threshold_is_not_a_cut,
        _the_metrics_precision_is_not_its_counts,
        _a_float_format,
        _a_band_that_is_not_configured,
        _an_overall_rate_for_a_small_group,
    ],
)
def test_a_damaged_file_is_refused(body, public_predictor, damage):
    damaged = json.loads(evidence.dumps(body))
    damage(damaged)
    assert json.dumps(damaged) != json.dumps(json.loads(evidence.dumps(body))), "nothing changed"
    with pytest.raises(DemoDataError):
        evidence.parse(damaged, public_predictor)


def _show_every_segment(segments, cells, minimum=MIN_CELL):
    return list(segments)


def _hide_only_the_failing_segments(segments, cells, minimum=MIN_CELL):
    """The rule per segment without its complement: one hidden segment is the rest's remainder."""
    return [
        row | dict.fromkeys(insights.SEGMENT_OUTCOMES)
        if not (
            sd.two_sided(*cells[row["dimension"], row["segment"]][:2])
            and sd.two_sided(*cells[row["dimension"], row["segment"]][2:])
        )
        else row
        for row in segments
    ]


def _show_every_band(bands, overall, minimum=MIN_CELL):
    return dict(bands)


def _every_grid_cut(basis, required=(), preferred=(), minimum=MIN_CELL, legacy=None):
    return basis.thresholds


@pytest.mark.parametrize(
    ("name", "bypass"),
    [
        ("guard_segments", _show_every_segment),
        ("guard_segments", _hide_only_the_failing_segments),
        ("guard_bands", _show_every_band),
        ("safe_cuts", _every_grid_cut),
    ],
)
def test_a_builder_that_skips_a_guard_writes_nothing_the_reader_accepts(
    public_book, public_predictor, tmp_path, monkeypatch, name, bypass
):
    """The reader holds the rule on its own: a builder that broke it is refused when it
    reads its own file back, before anything is written."""
    if bypass is _hide_only_the_failing_segments:
        # Precondition: some dimension hides exactly one segment, which its complement betrays.
        rows, y, p = _validation_scores(public_book)
        cells = sd.segment_cells(y, p, rows, schema.VALIDATION_SEGMENTS, public_predictor.threshold)
        failing = [
            k for k, c in cells.items() if not (sd.two_sided(*c[:2]) and sd.two_sided(*c[2:]))
        ]
        dims = [k[0] for k in failing]
        assert any(dims.count(d) == 1 for d in dims)
    monkeypatch.setattr(sd, name, bypass)
    with pytest.raises(DemoDataError):
        build.evidence_of(public_predictor, public_book, tmp_path / "absent.json")


@pytest.mark.parametrize("key", ["model_sha256", "data_sha256", "split_digest", "threshold"])
def test_a_file_bound_to_anything_else_is_refused(body, public_predictor, key):
    other = copy.deepcopy(body)
    other["identity"][key] = 0.5 if key == "threshold" else "c" * 64
    with pytest.raises(DemoDataError, match="not of the served model"):
        evidence.parse(other, public_predictor)


def test_a_file_for_other_risk_bands_is_refused(body, public_predictor, cfg):
    bands = (
        replace(cfg.risk_bands[0], max=0.3),
        replace(cfg.risk_bands[1], min=0.3),
        *cfg.risk_bands[2:],
    )
    moved = replace(public_predictor, config=replace(cfg, risk_bands=bands))
    with pytest.raises(DemoDataError, match="risk bands"):
        evidence.parse(copy.deepcopy(body), moved)


def test_a_model_without_provenance_matches_no_file(body, public_predictor):
    bare = replace(public_predictor, meta={**public_predictor.meta, "split_digest": None})
    with pytest.raises(DemoDataError, match="no provenance"):
        evidence.parse(copy.deepcopy(body) | {"identity": evidence.identity_of(bare)}, bare)


@pytest.mark.parametrize("text", ["", "{", "null", "[1, 2]", '{"format": 1}', "[" * 100_000])
def test_a_file_that_is_not_the_format_is_refused(tmp_path, public_predictor, text):
    path = tmp_path / evidence.EVIDENCE_FILE
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DemoDataError):
        evidence.load_published(path, public_predictor)


def test_a_missing_file_is_refused(tmp_path, public_predictor):
    with pytest.raises(DemoDataError, match="could not be read"):
        evidence.load_published(tmp_path / "absent.json", public_predictor)


def test_nan_is_never_published(body):
    with pytest.raises(ValueError):
        evidence.dumps(body | {"collinearity": float("nan")})


@pytest.mark.parametrize("figure", [float("nan"), float("inf"), float("-inf"), 10**400])
def test_a_figure_that_is_not_finite_is_refused(body, public_predictor, figure):
    """``json.loads`` accepts NaN and Infinity, and an integer past a float's range."""
    with pytest.raises(DemoDataError, match="collinearity"):
        evidence.parse(
            json.loads(evidence.dumps(body)) | {"collinearity": figure}, public_predictor
        )


def test_infinity_in_the_file_is_refused(body, public_predictor, tmp_path):
    path = tmp_path / evidence.EVIDENCE_FILE
    text = evidence.dumps(body | {"collinearity": 0.5})
    path.write_text(text.replace('"collinearity": 0.5', '"collinearity": Infinity'), "utf-8")
    assert "Infinity" in path.read_text(encoding="utf-8")
    with pytest.raises(DemoDataError, match="collinearity"):
        evidence.load_published(path, public_predictor)


def test_a_file_larger_than_any_real_one_is_refused_before_it_is_read(
    body, public_predictor, tmp_path
):
    """Padding a good file past the cap is enough: the size is the refusal, not the content."""
    path = tmp_path / evidence.EVIDENCE_FILE
    text = evidence.dumps(body)
    path.write_text(text, encoding="utf-8")
    assert evidence.load_published(path, public_predictor)
    path.write_text(text + " " * (evidence._MAX_BYTES + 1 - len(text.encode())), "utf-8")
    assert path.stat().st_size == evidence._MAX_BYTES + 1
    with pytest.raises(DemoDataError, match="larger than any real one"):
        evidence.load_published(path, public_predictor)


# --- the builder -------------------------------------------------------------------------------


def test_nothing_is_published_for_unverified_partitions(clean_frame, dashboard_predictor, tmp_path):
    """A dataset that is not the model's has nothing observed to publish."""
    other = score_book(clean_frame, "d" * 64, dashboard_predictor)
    assert other.withheld is not None
    with pytest.raises(DemoDataError, match="nothing can be published"):
        build.evidence_of(dashboard_predictor, other, tmp_path / "absent.json")


def test_the_builder_writes_its_two_files_and_no_other(body, public_predictor, tmp_path):
    explanations = build.explanations_of(public_predictor, sample=5, permutations=2)
    build.write(tmp_path, body, explanations)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [evidence.EVIDENCE_FILE, evidence.DEMO_EXPLANATIONS_FILE]
    )
    assert evidence.load_published(tmp_path / evidence.EVIDENCE_FILE, public_predictor)
    written = json.loads((tmp_path / evidence.DEMO_EXPLANATIONS_FILE).read_text())
    assert written["partition"] == "synthetic" and written["n_explained"] == 5
    assert written["records_sha256"] == demo.records_sha256(demo.demo_customers())
    # The bundle check holds the file to exactly these keys.
    assert set(written) == bundle.DEMO_EXPLANATION_KEYS


def test_the_demo_attributions_are_of_generated_customers_only(dashboard_predictor, clean_frame):
    features = dashboard_predictor.feature_columns
    explanations = build.explanations_of(dashboard_predictor, sample=8, permutations=2)
    generated = demo.demo_customers()
    listed = {
        tuple(
            str(v) if f in schema.ALLOWED_CATEGORIES else float(v)
            for f, v in zip(features, row, strict=True)
        )
        for row in generated[features].itertuples(index=False, name=None)
    }
    drawn = {tuple(row[f] for f in features) for row in explanations["x"]}
    assert drawn <= listed
    assert schema.ID_COLUMN not in json.dumps(explanations)


def test_generated_customers_that_copy_the_dataset_are_counted(
    dashboard_predictor, book, clean_frame
):
    """The builder refuses a demo book that repeats a dataset customer's inputs: this counts them."""
    assert build.shared_rows(dashboard_predictor, book, demo.demo_customers()) == 0
    copied = clean_frame[[schema.ID_COLUMN, *dashboard_predictor.feature_columns]].head(7)
    assert build.shared_rows(dashboard_predictor, book, copied) == 7


def _own_dataset(cfg) -> None:
    """Record ``cfg``'s pinned file and split in its model, so publishing it is allowed."""
    from churnsense.data.download import sha256_of
    from tests.conftest import pinned_split_digest

    edit_sidecar(
        cfg.paths.artifacts_dir,
        data_sha256=sha256_of(cfg.raw_data_file),
        split_digest=pinned_split_digest(cfg),
    )


def test_publishing_refuses_a_demo_book_that_repeats_a_dataset_customer(
    pipeline_cfg, dashboard_predictor, monkeypatch
):
    """Everything else is in order, so only this refusal stands between the copy and the files."""
    from churnsense.data.loader import load_clean

    _own_dataset(pipeline_cfg)
    dataset, _ = load_clean(pipeline_cfg)
    copied = demo.demo_customers()
    for feature in dashboard_predictor.feature_columns:
        copied.loc[copied.index[:2], feature] = dataset[feature].head(2).to_numpy()
    monkeypatch.setattr(build, "demo_customers", lambda: copied)
    before = {p.name: p.read_bytes() for p in pipeline_cfg.paths.artifacts_dir.iterdir()}
    with pytest.raises(DemoDataError, match="2 generated customer"):
        build.publish(pipeline_cfg, sample=6, permutations=2)
    after = {p.name: p.read_bytes() for p in pipeline_cfg.paths.artifacts_dir.iterdir()}
    assert after == before


def test_publishing_refuses_a_dataset_the_model_was_not_trained_on(pipeline_cfg):
    """``pipeline_cfg`` pins the demo file; the model records another dataset: nothing is written."""
    with pytest.raises(DemoDataError, match="nothing can be published"):
        build.publish(pipeline_cfg)
    written = {path.name for path in pipeline_cfg.paths.artifacts_dir.iterdir()}
    assert not written & {evidence.EVIDENCE_FILE, evidence.DEMO_EXPLANATIONS_FILE}


def test_publishing_from_a_public_demos_configuration_is_refused(pipeline_cfg):
    public = replace(pipeline_cfg, deployment=Deployment("public_demo", "synthetic"))
    with pytest.raises(DemoDataError, match="development configuration"):
        build.publish(public)
    written = {path.name for path in pipeline_cfg.paths.artifacts_dir.iterdir()}
    assert not written & {evidence.EVIDENCE_FILE, evidence.DEMO_EXPLANATIONS_FILE}


def test_publishing_writes_both_files_for_the_models_own_dataset(pipeline_cfg):
    """The whole command, on the pinned demo file, once the model records that file and split."""
    _own_dataset(pipeline_cfg)
    before = {p.name: p.read_bytes() for p in pipeline_cfg.paths.artifacts_dir.iterdir()}
    published, explanations = build.publish(pipeline_cfg, sample=6, permutations=2)
    after = {p.name: p.read_bytes() for p in pipeline_cfg.paths.artifacts_dir.iterdir()}
    assert set(after) - set(before) == {evidence.EVIDENCE_FILE, evidence.DEMO_EXPLANATIONS_FILE}
    assert all(after[name] == data for name, data in before.items()), "an existing file changed"
    assert published["dataset_customers"] == 120
    assert explanations["partition"] == "synthetic"
    assert SYNTHETIC_SHA256 not in after[evidence.EVIDENCE_FILE].decode()


# --- the reader's score rules on hand-made tables ---------------------------------------------


def _metrics(tp, fp, fn, tn):
    from churnsense.evaluation.metrics import ClassificationMetrics

    n = tp + fp + fn + tn
    return ClassificationMetrics(
        threshold=0.36,
        n=n,
        positives=tp + fn,
        tn=tn,
        fp=fp,
        fn=fn,
        tp=tp,
        accuracy=0.0,
        precision=0.0,
        recall=0.0,
        f1=0.0,
        roc_auc=0.5,
        average_precision=0.5,
        brier=0.2,
    )


def _seg(segment, tp, fp, fn, tn, shown=True):
    row = dict.fromkeys(insights.SEGMENT_OUTCOMES)
    row |= {
        "dimension": "InternetService",
        "segment": segment,
        "customers": tp + fp + fn + tn,
        "flagged": tp + fp,
    }
    if shown:
        churners = tp + fn
        row |= {
            "churners": churners,
            "churn_rate": churners / row["customers"],
            "flagged_rate": row["flagged"] / row["customers"],
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / churners if churners else None,
            "unscanned": tp + fp == 0 and churners > 0,
        }
    return row


_TABLE = [("DSL", 40, 50, 45, 330), ("Fiber optic", 220, 140, 45, 230), ("No", 0, 0, 24, 282)]


def test_segments_that_keep_the_rule_on_both_sides_are_read():
    rows = [_seg(*cells) for cells in _TABLE]
    assert len(evidence._segments(rows, _metrics(260, 190, 114, 842))) == 3


@pytest.mark.parametrize(
    "change",
    [
        lambda rows: rows[0].update(precision=rows[0]["precision"] + 0.01),
        # Too small to move the count it rounds to: only the recall itself is wrong.
        lambda rows: rows[0].update(recall=rows[0]["recall"] + 1e-4),
        lambda rows: rows[2].update(churners=rows[2]["customers"] + 1),
        lambda rows: rows[0].update(flagged=rows[0]["flagged"] + 1),
        lambda rows: rows[1].update(churn_rate=rows[1]["churn_rate"] + 0.01),
        lambda rows: rows[1].update(flagged_rate=rows[1]["flagged_rate"] + 0.01),
    ],
)
def test_a_segment_whose_figures_are_not_its_own_counts_is_refused(change):
    rows = [_seg(*cells) for cells in _TABLE]
    change(rows)
    with pytest.raises(DemoDataError):
        evidence._segments(rows, _metrics(260, 190, 114, 842))


def _band(customers, churned):
    return {"customers": customers, "churned": churned, "rate": churned / customers}


def test_bands_all_shown_must_add_up_to_the_churn_observed():
    names = ["Low", "High"]
    overall = insights.Observed(400, 150, 150 / 400)
    good = {"Low": _band(300, 80), "High": _band(100, 70)}
    assert evidence._bands(good, names, overall)["High"].churned == 70
    off = {"Low": _band(300, 81), "High": _band(100, 70)}
    with pytest.raises(DemoDataError, match="churn observed"):
        evidence._bands(off, names, overall)


def test_calibration_at_half_is_read_off_the_published_cut_or_not_stated():
    from churnsense.api import dashboard_schemas as out
    from churnsense.api.dashboard import _published_at_half
    from churnsense.evaluation.threshold import SweepBasis

    recorded = out.AtHalf(precision=0.9, recall=0.9, brier=0.13)
    basis = SweepBasis(
        1000, (0.0, 0.36, 0.5), (300, 200, 120), (700, 150, 60), (0, 100, 180), (1.0, 0.8, 0.5)
    )
    at_half = _published_at_half(basis, recorded)
    assert (at_half.precision, at_half.recall, at_half.brier) == (120 / 180, 120 / 300, 0.13)
    without = replace(basis, thresholds=(0.0, 0.36, 0.55))
    assert _published_at_half(without, recorded) is None
    assert _published_at_half(None, recorded) is None
    assert _published_at_half(basis, None) is None
