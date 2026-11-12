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

from churnsense import evidence
from churnsense.book import score_book
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
def live(book, dashboard_predictor, shap_cache) -> evidence.LiveEvidence:
    return evidence.LiveEvidence(book, dashboard_predictor, shap_cache)


@pytest.fixture(scope="module")
def body(book, dashboard_predictor, shap_cache) -> dict:
    return build.evidence_of(dashboard_predictor, book, shap_cache)


@pytest.fixture(scope="module")
def published(body, dashboard_predictor) -> evidence.PublishedEvidence:
    return evidence.parse(json.loads(evidence.dumps(body)), dashboard_predictor)


# --- what is read back is what was computed ------------------------------------------------


def test_the_published_answers_are_the_computed_ones(live, published, book):
    assert published.withheld is None is live.withheld
    assert published.dataset_customers == live.dataset_customers == len(book.frame)
    assert published.observed() == live.observed()
    assert published.band_observed() == live.band_observed()
    assert published.histogram_observed() == live.histogram_observed()
    assert published.lifecycle() == live.lifecycle()
    assert published.sweep_basis() == live.sweep_basis()
    assert published.collinearity() == live.collinearity()
    assert published.importance() == live.importance()


def test_every_breakdown_a_page_can_ask_for_is_published(live, published):
    asked = evidence.all_breakdowns()
    assert len(asked) == len(set(asked)) == 14 + 14 * 13
    for rows, columns in asked:
        assert published.breakdown(rows, columns) == live.breakdown(rows, columns)


def test_the_published_validation_figures_are_the_computed_ones(live, published):
    found, expected = published.validation_evaluation(), live.validation_evaluation()
    assert found.metrics == expected.metrics
    assert (found.churn_rate, found.no_skill_accuracy, found.ece) == (
        expected.churn_rate,
        expected.no_skill_accuracy,
        expected.ece,
    )
    assert found.reliability == expected.reliability
    assert found.segments == expected.segments


def test_the_sweep_from_the_totals_agrees_with_the_sweep_over_customers(live, published, cfg):
    """One sum in two orders: the counts are equal and the money agrees to rounding."""
    for business in (cfg.business, replace(cfg.business, offer_success_rate=0.1, gross_margin=0.9)):
        computed, read = live.economics(business), published.economics(business)
        for column in ("threshold", "flagged", "true_positives", "false_positives"):
            assert list(read.sweep[column]) == list(computed.sweep[column])
        for column in ("retained_value", "intervention_cost", "net_benefit", "precision"):
            assert list(read.sweep[column]) == pytest.approx(
                list(computed.sweep[column]), rel=1e-12, abs=1e-9
            )
        assert read.recommended.threshold == computed.recommended.threshold
        assert read.peak.threshold == computed.peak.threshold
        assert (read.customers, read.churners) == (computed.customers, computed.churners)
        assert list(read.sensitivity["threshold"]) == list(computed.sensitivity["threshold"])


def test_the_published_text_is_the_same_on_every_run(book, dashboard_predictor, shap_cache, body):
    again = build.evidence_of(dashboard_predictor, book, shap_cache)
    assert evidence.dumps(again) == evidence.dumps(body)


def test_without_a_cache_no_ranking_is_published(book, dashboard_predictor, tmp_path):
    body = build.evidence_of(dashboard_predictor, book, tmp_path / "absent.json")
    assert body["importance"] is None
    assert (
        evidence.parse(json.loads(evidence.dumps(body)), dashboard_predictor).importance() is None
    )


# --- it holds no customer --------------------------------------------------------------------


def _walk(value):
    yield value
    if isinstance(value, dict):
        for inner in value.values():
            yield from _walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk(inner)


def test_the_published_file_names_no_customer(body, clean_frame):
    text = evidence.dumps(body)
    ids = set(clean_frame[schema.ID_COLUMN])
    assert not set(re.findall(r"[A-Za-z0-9-]+", text)) & ids
    assert schema.ID_COLUMN not in text


def test_no_key_of_the_published_file_is_a_customers_column(body):
    assert not bundle.holds_a_record(body)
    keys = {key for value in _walk(body) if isinstance(value, dict) for key in value}
    assert not keys & set(schema.RAW_COLUMNS)


def test_no_list_in_the_published_file_is_as_long_as_the_dataset(body, book):
    """Nothing is per customer: the longest list is the breakdowns, by far shorter than the book."""
    longest = max(len(value) for value in _walk(body) if isinstance(value, list))
    assert longest == len(evidence.all_breakdowns()) < len(book.frame)


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
    cell = _first_cell(body, reported=True)
    cell.update(customers=MIN_CELL - 1, churned=1, rate=1 / (MIN_CELL - 1))


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


def _a_small_group_is_hidden_as_complementary(body):
    cell = next(
        cell
        for breakdown in body["breakdowns"]
        for cell in breakdown["cells"]
        if cell["reason"] == "too_few"
    )
    cell["reason"] = "complementary"


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


def _a_histogram_mean_without_an_observed_rate(body):
    bin_ = next(bin_ for bin_ in body["histogram_observed"] if bin_["mean"] is not None)
    bin_["observed"] = None


def _last_two(body, name):
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


def _a_histogram_of_another_size(body):
    body["histogram_observed"].pop()


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
        _a_small_group_is_hidden_as_complementary,
        _a_breakdown_is_missing,
        _a_breakdown_is_repeated,
        _levels_are_reordered,
        _a_ranked_group_is_a_total_not_a_cell,
        _a_ranked_group_is_hidden,
        _the_row_totals_do_not_add_up_to_the_partition,
        _an_unknown_dimension,
        _a_histogram_mean_without_an_observed_rate,
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
        _a_histogram_of_another_size,
        _a_band_that_is_not_configured,
        _an_overall_rate_for_a_small_group,
    ],
)
def test_a_damaged_file_is_refused(body, dashboard_predictor, damage):
    damaged = json.loads(evidence.dumps(body))
    damage(damaged)
    assert json.dumps(damaged) != json.dumps(json.loads(evidence.dumps(body))), "nothing changed"
    with pytest.raises(DemoDataError):
        evidence.parse(damaged, dashboard_predictor)


@pytest.mark.parametrize("key", ["model_sha256", "data_sha256", "split_digest", "threshold"])
def test_a_file_bound_to_anything_else_is_refused(body, dashboard_predictor, key):
    other = copy.deepcopy(body)
    other["identity"][key] = 0.5 if key == "threshold" else "c" * 64
    with pytest.raises(DemoDataError, match="not of the served model"):
        evidence.parse(other, dashboard_predictor)


def test_a_file_for_other_risk_bands_is_refused(body, dashboard_predictor, cfg):
    bands = (
        replace(cfg.risk_bands[0], max=0.3),
        replace(cfg.risk_bands[1], min=0.3),
        *cfg.risk_bands[2:],
    )
    moved = replace(dashboard_predictor, config=replace(cfg, risk_bands=bands))
    with pytest.raises(DemoDataError, match="risk bands"):
        evidence.parse(copy.deepcopy(body), moved)


def test_a_model_without_provenance_matches_no_file(body, dashboard_predictor):
    bare = replace(dashboard_predictor, meta={**dashboard_predictor.meta, "split_digest": None})
    with pytest.raises(DemoDataError, match="no provenance"):
        evidence.parse(copy.deepcopy(body) | {"identity": evidence.identity_of(bare)}, bare)


@pytest.mark.parametrize("text", ["", "{", "null", "[1, 2]", '{"format": 1}', "[" * 100_000])
def test_a_file_that_is_not_the_format_is_refused(tmp_path, dashboard_predictor, text):
    path = tmp_path / evidence.EVIDENCE_FILE
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DemoDataError):
        evidence.load_published(path, dashboard_predictor)


def test_a_missing_file_is_refused(tmp_path, dashboard_predictor):
    with pytest.raises(DemoDataError, match="could not be read"):
        evidence.load_published(tmp_path / "absent.json", dashboard_predictor)


def test_nan_is_never_published(body):
    with pytest.raises(ValueError):
        evidence.dumps(body | {"collinearity": float("nan")})


@pytest.mark.parametrize("figure", [float("nan"), float("inf"), float("-inf"), 10**400])
def test_a_figure_that_is_not_finite_is_refused(body, dashboard_predictor, figure):
    """``json.loads`` accepts NaN and Infinity, and an integer past a float's range."""
    with pytest.raises(DemoDataError, match="collinearity"):
        evidence.parse(
            json.loads(evidence.dumps(body)) | {"collinearity": figure}, dashboard_predictor
        )


def test_infinity_in_the_file_is_refused(body, dashboard_predictor, tmp_path):
    path = tmp_path / evidence.EVIDENCE_FILE
    text = evidence.dumps(body | {"collinearity": 0.5})
    path.write_text(text.replace('"collinearity": 0.5', '"collinearity": Infinity'), "utf-8")
    assert "Infinity" in path.read_text(encoding="utf-8")
    with pytest.raises(DemoDataError, match="collinearity"):
        evidence.load_published(path, dashboard_predictor)


def test_a_file_larger_than_any_real_one_is_refused_before_it_is_read(
    body, dashboard_predictor, tmp_path
):
    """Padding a good file past the cap is enough: the size is the refusal, not the content."""
    path = tmp_path / evidence.EVIDENCE_FILE
    text = evidence.dumps(body)
    path.write_text(text, encoding="utf-8")
    assert evidence.load_published(path, dashboard_predictor)
    path.write_text(text + " " * (evidence._MAX_BYTES + 1 - len(text.encode())), "utf-8")
    assert path.stat().st_size == evidence._MAX_BYTES + 1
    with pytest.raises(DemoDataError, match="larger than any real one"):
        evidence.load_published(path, dashboard_predictor)


# --- the builder -------------------------------------------------------------------------------


def test_nothing_is_published_for_unverified_partitions(clean_frame, dashboard_predictor, tmp_path):
    """A dataset that is not the model's has nothing observed to publish."""
    other = score_book(clean_frame, "d" * 64, dashboard_predictor)
    assert other.withheld is not None
    with pytest.raises(DemoDataError, match="nothing can be published"):
        build.evidence_of(dashboard_predictor, other, tmp_path / "absent.json")


def test_the_builder_writes_its_two_files_and_no_other(body, dashboard_predictor, tmp_path):
    explanations = build.explanations_of(dashboard_predictor, sample=5, permutations=2)
    build.write(tmp_path, body, explanations)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [evidence.EVIDENCE_FILE, evidence.DEMO_EXPLANATIONS_FILE]
    )
    assert evidence.load_published(tmp_path / evidence.EVIDENCE_FILE, dashboard_predictor)
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
