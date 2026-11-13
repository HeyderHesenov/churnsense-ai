"""What the public demo publishes, read together with what other sources already state.

An outsider holds more than the API: two immutable release reports (the validation sweep
at 0.10, ..., 0.80; the test partition's churners per Contract and InternetService
segment) and the IBM sample's own public totals. This module checks that combining them
with the published evidence solves for no small group the reports alone do not give:

* a demo cut never splits a legacy interval into a small piece, and never sits on the edge
  of a legacy interval that is already small (``score_disclosure.legacy_holds``);
* a training count never completes ``total - test - training = validation`` for a small
  validation group (``insights.CROSS_PARTITION``, ``disclosure_audit``);
* an association strength is not precise enough to be inverted into a withheld count.

The attack on the Two year segment is reproduced with invented counts in CI; the checks on
the real file run where ``make demo-data`` wrote one (``LOCAL`` below).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math

import numpy as np
import pandas as pd
import pytest

from churnsense import disclosure_audit as audit
from churnsense import evidence, insights, legacy_disclosures
from churnsense import score_disclosure as sd
from churnsense.config import PROJECT_ROOT
from churnsense.demo import migrate
from churnsense.exceptions import DemoDataError
from tests import test_public_evidence as built

# The published file of the synthetic model, and its live source, as test_public_evidence builds them.
shap_cache = built.shap_cache
live = built.live
body = built.body

# --- the Two year subtraction, with invented counts -------------------------------------------

#: Validation segments of Contract as the demo publishes them when it hides their outcomes:
#: (cells or None, customers, flagged). Invented, shaped like the real partition.
SEGMENTS = {"Contract": [(None, 780, 440), (None, 310, 9), (None, 320, 0)]}
#: (tp, fp, fn, tn) of the whole partition at the threshold, consistent with the above.
TOTALS = (262, 187, 112, 849)
ORDER = {"Contract": ["Month-to-month", "One year", "Two year"]}


def _one_way(two_year: int | None, one_year: int | None) -> dict:
    return {"Contract": [("Month-to-month", 1002), ("One year", one_year), ("Two year", two_year)]}


def _external(one_way: dict, partition_churned: int) -> dict:
    return audit.external_segment_churners(one_way, partition_churned, ORDER)


def test_a_published_two_year_training_count_gives_validation_away():
    """IBM total - test (immutable report) - training (published) = validation's Two year."""
    total = legacy_disclosures.POPULATION_MARGINALS["Contract", "Two year"][1]
    test = next(r.churners for r in legacy_disclosures.TEST_SEGMENTS if r.segment == "Two year")
    training = total - test - 12  # invented so that validation holds 12 churners
    # One year's training count, invented to agree with the partition's 374 churners.
    external = _external(_one_way(training, 92), 1002 + 92 + training)
    two_year = next(c for members, c in external["Contract"] if members == frozenset({2}))
    assert two_year == 12
    found, _ = audit.segment_violations(audit_view(), TOTALS, None, external)
    assert any("Contract (2,) whole: 12" in line for line in found), found


def test_with_two_year_and_its_complement_hidden_only_their_sum_follows():
    external = _external(_one_way(None, None), 1121)
    sets = {members for members, _ in external["Contract"]}
    assert sets == {frozenset({0}), frozenset({1, 2})}
    found, solved = audit.segment_violations(audit_view(), TOTALS, None, external)
    assert solved and not [line for line in found if "whole" in line and "(2,)" in line]


def test_hiding_two_year_alone_would_give_it_back_as_the_total_less_the_others():
    """Not just the hidden cell: the guard hides One year with it, or the total betrays it."""
    reasons = insights.guard_levels("Contract", [2326, 865, 1034], [1002, 93, 40], 1135)
    assert reasons[2] == "cross_partition"
    assert reasons[1] == "withheld" and reasons[0] is None
    external = _external(_one_way(None, 93), 1135)
    assert frozenset({2}) in {members for members, _ in external["Contract"]}


def audit_view() -> dict:
    return {key: list(rows) for key, rows in SEGMENTS.items()}


# --- the association strengths ------------------------------------------------------------------


def _v(m2m: tuple[int, int], one: tuple[int, int], two: tuple[int, int]) -> float:
    from churnsense.analysis import cramers_v

    rows = []
    for level, (n, c) in (("M", m2m), ("O", one), ("T", two)):
        rows += [(level, 1)] * c + [(level, 0)] * (n - c)
    frame = pd.DataFrame(rows, columns=["x", "y"])
    return cramers_v(frame["x"], frame["y"])


def _consistent(strength: float, decimals: int, m2m, ones: int, twos: int, both: int) -> list[int]:
    """Two year churn counts consistent with a published strength and the published totals."""
    return [
        t
        for t in range(0, both + 1)
        if both - t <= ones
        and t <= twos
        and round(_v(m2m, (ones, both - t), (twos, t)), decimals) == round(strength, decimals)
    ]


def test_a_full_precision_strength_gives_a_withheld_count_back_and_two_decimals_do_not():
    """Invented table: Month-to-month shown, One and Two year hidden together (119 churners)."""
    m2m, ones, twos, both, truth = (2300, 1000), 870, 1030, 119, 31
    exact = _v(m2m, (ones, both - truth), (twos, truth))
    assert _consistent(exact, 15, m2m, ones, twos, both) == [truth]
    published = round(exact, 2)
    assert len(_consistent(published, 2, m2m, ones, twos, both)) >= 10


def test_the_ranking_is_read_at_two_decimals_whatever_the_file_holds(tmp_path):
    from churnsense import reports

    (tmp_path / reports.ASSOCIATION_FILE).write_text(
        "feature,measure,strength,direction\n"
        "Contract,Cramer's V,0.41889026665374857,n/a (categorical)\n"
        "tenure,point-biserial |r|,0.4149,higher -> less churn\n"
    )
    ranking = reports.association_ranking(tmp_path)
    assert ranking["strength"].tolist() == [0.42, 0.41]
    assert ranking["feature"].tolist() == ["Contract", "tenure"]


def test_the_tracked_ranking_has_at_most_two_decimals():
    text = (PROJECT_ROOT / "reports" / "association_ranking.csv").read_text().splitlines()[1:]
    for line in text:
        strength = line.split(",")[2]
        assert len(strength.split(".")[-1]) <= 2, line


# --- the reader, for the released model ------------------------------------------------------


@pytest.fixture
def released(monkeypatch, public_predictor):
    """Treat the synthetic model as the released one, with a legacy sweep of its own:
    the fixture's fine sweep at 0.1, ..., 0.8, printed to two decimals as the reports print."""
    identity = evidence.identity_of(public_predictor)
    monkeypatch.setattr(
        legacy_disclosures,
        "describes",
        lambda other: (
            isinstance(other, dict) and other.get("model_sha256") == identity["model_sha256"]
        ),
    )
    return identity


def _legacy_from(fine) -> tuple:
    points = []
    churners = fine.churners
    for t in legacy_disclosures.LEGACY_THRESHOLDS:
        i = sd._index(fine.thresholds, t)
        flagged = fine.true_positives[i] + fine.false_positives[i]
        if flagged == 0:
            continue
        points.append(
            legacy_disclosures.LegacyPoint(
                t,
                flagged,
                round(fine.true_positives[i] / flagged, 2),
                round(fine.true_positives[i] / churners, 2),
            )
        )
    return tuple(points)


def test_a_released_files_cuts_must_keep_the_rule_with_the_reports_thresholds(
    released, body, live, public_predictor, monkeypatch
):
    fine = live.sweep_basis()
    monkeypatch.setattr(legacy_disclosures, "LEGACY_SWEEP", _legacy_from(fine))
    legacy = sd.published_legacy(fine.churners)
    plain = sd.safe_cuts(fine, required=(public_predictor.threshold,))
    good = sd.safe_cuts(fine, required=(public_predictor.threshold,), legacy=legacy)
    assert plain is not None and good != plain
    assert not sd.legacy_holds(sd.restrict(fine, plain), legacy)
    if good is not None:
        assert sd.legacy_holds(sd.restrict(fine, good), legacy)
    damaged = copy.deepcopy(body)
    restricted = sd.restrict(fine, plain)
    damaged["sweep_basis"] = migrate._plain_basis(restricted)
    damaged["validation"] = None
    with pytest.raises(DemoDataError, match="release reports"):
        evidence.parse(json.loads(evidence.dumps(damaged)), public_predictor)


def test_the_builder_cuts_the_released_models_sweep_with_the_reports_thresholds(
    released, live, public_predictor, monkeypatch
):
    """The legacy thresholds bind evidence of the released model, and only of it - and the
    builder reads them, so a file it writes keeps the rule with the reports."""
    fine = live.sweep_basis()
    monkeypatch.setattr(legacy_disclosures, "LEGACY_SWEEP", _legacy_from(fine))
    legacy = sd.published_legacy(fine.churners)
    assert evidence.legacy_for(released, fine.churners) == legacy
    assert evidence.legacy_for(dict(released, model_sha256="0" * 64), fine.churners) is None
    plain = sd.safe_cuts(fine, required=(public_predictor.threshold,))
    basis, _, _ = evidence._validation_on_safe_cuts(live, public_predictor)
    assert plain is not None and not sd.legacy_holds(sd.restrict(fine, plain), legacy)
    assert basis is None or sd.legacy_holds(basis, legacy)


def test_the_builder_refuses_reports_that_do_not_state_the_released_models_sweep(
    released, live, public_predictor, monkeypatch
):
    """The legacy sweep is transcribed by hand: if it is not the released model's own sweep
    (a typo, or another model under the released identity), the cut rule would judge pieces
    no outsider sees while the audit judges the real ones. The builder checks the two agree."""
    fine = live.sweep_basis()
    points = _legacy_from(fine)
    monkeypatch.setattr(legacy_disclosures, "LEGACY_SWEEP", points)
    assert evidence._stated_by_reports(fine) is not None
    first = points[0]
    for wrong in (
        dataclasses.replace(first, flagged=first.flagged + 1),
        dataclasses.replace(first, precision=round(first.precision + 0.05, 2)),
    ):
        monkeypatch.setattr(legacy_disclosures, "LEGACY_SWEEP", (wrong, *points[1:]))
        with pytest.raises(DemoDataError, match="release reports"):
            evidence._stated_by_reports(fine)


def test_a_file_of_another_model_is_judged_on_its_own_figures(body, public_predictor):
    assert not legacy_disclosures.describes(body["identity"])
    evidence.parse(json.loads(evidence.dumps(body)), public_predictor)


# --- the reader, on the training tables --------------------------------------------------------


def _first(body, rows, columns, where):
    table = next(b for b in body["breakdowns"] if b["rows"] == rows and b["columns"] == columns)
    return table, next(c for c in [*table["row_totals"], *table["cells"]] if where(c))


def test_a_shown_two_year_churn_is_refused(body, public_predictor):
    damaged = copy.deepcopy(body)
    _table, cell = _first(damaged, "Contract", None, lambda c: c["row"] == "Two year")
    cell.update(churned=10, rate=10 / cell["customers"], lift=1.0, reason=None, low=0.0, high=1.0)
    with pytest.raises(DemoDataError):
        evidence.parse(json.loads(evidence.dumps(damaged)), public_predictor)


def test_a_two_year_churn_that_keeps_the_class_rule_is_still_refused(body, public_predictor):
    """Two year is hidden for what it gives away across partitions, not for its size: a
    count with twenty churners and twenty stayers either side is refused all the same."""
    damaged = copy.deepcopy(body)
    _table, cell = _first(damaged, "Contract", None, lambda c: c["row"] == "Two year")
    churned = cell["customers"] // 2
    assert churned >= 20 and cell["customers"] - churned >= 20
    rate = churned / cell["customers"]
    cell.update(
        churned=churned, rate=rate, lift=1.0, reason=None, low=rate - 0.05, high=rate + 0.05
    )
    with pytest.raises(DemoDataError, match="Two year's churn is shown"):
        evidence.parse(json.loads(evidence.dumps(damaged)), public_predictor)


def test_the_audit_refuses_tables_the_guard_would_hide_more_of(body, public_predictor, monkeypatch):
    """Whatever the reader lets through, nothing is written whose training tables the guard,
    run again on them, would hide more of."""
    damaged = copy.deepcopy(body)
    _table, cell = _first(damaged, "Contract", None, lambda c: c["row"] == "Two year")
    churned = cell["customers"] // 2
    rate = churned / cell["customers"]
    cell.update(
        churned=churned, rate=rate, lift=1.0, reason=None, low=rate - 0.05, high=rate + 0.05
    )
    monkeypatch.setattr(evidence, "parse", lambda *_args, **_kwargs: None)
    with pytest.raises(DemoDataError, match="would hide more"):
        evidence.audited(damaged, public_predictor, None, None, False)


def test_a_shown_group_of_few_churners_is_refused(body, public_predictor):
    damaged = copy.deepcopy(body)
    _table, cell = _first(damaged, "PaymentMethod", None, lambda c: c["rate"] is not None)
    cell.update(churned=5, rate=5 / cell["customers"])
    low, high = cell["rate"] * 0.5, min(1.0, cell["rate"] * 2)
    cell.update(low=low, high=high)
    with pytest.raises(DemoDataError, match="fewer than 20 churners or stayers"):
        evidence.parse(json.loads(evidence.dumps(damaged)), public_predictor)


def test_the_builders_tables_are_already_what_the_guard_would_publish(body):
    """Re-reading the builder's output from its published figures hides nothing more."""
    again = json.loads(evidence.dumps(body))
    assert insights.guard_published(again["breakdowns"], again["lifecycle"]) is False
    assert again["breakdowns"] == json.loads(evidence.dumps(body))["breakdowns"]


# --- the data-free migration ---------------------------------------------------------------


def test_the_migration_restricts_merges_and_guards_without_any_dataset(
    body, live, public_predictor, monkeypatch
):
    from churnsense.data import loader

    def explode(*_args, **_kwargs):
        raise AssertionError("the migration read the dataset")

    monkeypatch.setattr(loader, "read_raw", explode)
    monkeypatch.setattr(loader, "load_clean", explode)
    older = copy.deepcopy(body)
    older["format"] = 2
    # An older file: a Two year count shown, as format 2 allowed.
    _, two = _first(older, "Contract", None, lambda c: c["row"] == "Two year")
    two.update(churned=60, rate=60 / two["customers"], lift=1.0, reason=None, low=0.1, high=0.2)
    result = migrate.migrated(older, public_predictor)
    assert result["format"] == evidence.FORMAT
    _, after = _first(result, "Contract", None, lambda c: c["row"] == "Two year")
    assert after["rate"] is None and after["reason"] == "cross_partition"
    evidence.parse(json.loads(evidence.dumps(result)), public_predictor)
    # Nothing new: every shown training figure was shown before.
    before = {
        (b["rows"], b["columns"], c["row"], c["column"]): c["churned"]
        for b in older["breakdowns"]
        for c in b["cells"]
    }
    for b in result["breakdowns"]:
        for c in b["cells"]:
            if c["rate"] is not None:
                assert before[(b["rows"], b["columns"], c["row"], c["column"])] == c["churned"]
    # And migrating again changes nothing.
    assert migrate.migrated(result, public_predictor) == result


def test_the_migration_cuts_an_older_file_of_the_released_model_to_the_reports_rule(
    released, body, live, public_predictor, monkeypatch
):
    """A format-2 file of the released model, cut before the reports' thresholds counted:
    the migration drops the cuts that split a legacy interval, merges the calibration bins
    to the cuts it keeps, and the result loads - nothing left at a dropped cut."""
    fine = live.sweep_basis()
    # Two upper thresholds: on this sweep they split the older file's top cut, and dropping
    # cuts (all a data-free migration can do) still leaves a sweep that keeps the rule.
    stated = tuple(p for p in _legacy_from(fine) if p.threshold in (0.6, 0.7))
    monkeypatch.setattr(legacy_disclosures, "LEGACY_SWEEP", stated)
    # The test report's segment churners are the IBM sample's, not this synthetic book's.
    monkeypatch.setattr(legacy_disclosures, "TEST_DIMENSIONS", ())
    legacy = sd.published_legacy(fine.churners)
    older = copy.deepcopy(body)  # built while nothing was released: no legacy thresholds
    older["format"] = 2
    before = tuple(older["sweep_basis"]["thresholds"])
    assert not sd.legacy_holds(migrate._basis(older["sweep_basis"]), legacy)
    result = migrate.migrated(older, public_predictor)
    after = tuple(result["sweep_basis"]["thresholds"])
    assert set(after) < set(before)
    assert public_predictor.threshold in after
    assert sd.legacy_holds(migrate._basis(result["sweep_basis"]), legacy)
    bins = result["validation"]["reliability"]
    assert [b["low"] for b in bins] == list(after)
    customers = sum(b["customers"] for b in older["validation"]["reliability"])
    assert sum(b["customers"] for b in bins) == customers
    evidence.parse(json.loads(evidence.dumps(result)), public_predictor)


def test_merged_calibration_bins_add_up_exactly():
    bins = [
        {
            "low": 0.0,
            "high": 0.3,
            "customers": 100,
            "mean_predicted": 0.1,
            "churned": 20,
            "rate": 0.2,
        },
        {
            "low": 0.3,
            "high": 0.5,
            "customers": 50,
            "mean_predicted": 0.4,
            "churned": 25,
            "rate": 0.5,
        },
        {
            "low": 0.5,
            "high": 1.0,
            "customers": 50,
            "mean_predicted": 0.7,
            "churned": 40,
            "rate": 0.8,
        },
    ]
    merged = migrate.merged_reliability(bins, (0.0, 0.5))
    assert [(b["low"], b["high"], b["customers"], b["churned"]) for b in merged] == [
        (0.0, 0.5, 150, 45),
        (0.5, 1.0, 50, 40),
    ]
    assert math.isclose(merged[0]["mean_predicted"], (100 * 0.1 + 50 * 0.4) / 150)
    with pytest.raises(DemoDataError, match="do not line up"):
        migrate.merged_reliability(bins, (0.0, 0.4))


def test_a_file_of_an_unknown_format_is_not_migrated(body, public_predictor):
    with pytest.raises(DemoDataError, match="can migrate"):
        migrate.migrated(dict(body, format=1), public_predictor)


# --- the real file, where it exists -------------------------------------------------------------

REAL = PROJECT_ROOT / "artifacts" / evidence.EVIDENCE_FILE
SWEEP = PROJECT_ROOT / "reports" / "threshold_sweep.csv"
LOCAL = pytest.mark.skipif(
    not (REAL.is_file() and SWEEP.is_file()),
    reason="needs the published evidence and the local validation sweep (make demo-data, make train)",
)


@pytest.fixture
def real(released_ledger) -> dict:
    """The tracked evidence file, read beside the repository's own release ledger: the real
    file is of the released model, and the legacy facts bind it only when the ledger says so."""
    return json.loads(REAL.read_text(encoding="utf-8"))


def _bands(body: dict) -> list:
    return [(name, low, high) for name, low, high in body["identity"]["risk_bands"]]


def _exact_legacy() -> dict[float, tuple[int, int]]:
    """The release reports' thresholds at their exact counts: the outsider's best case."""
    sweep = pd.read_csv(SWEEP)
    sweep["threshold"] = sweep["threshold"].round(2)
    at = sweep.set_index("threshold")
    return {
        t: (int(at.loc[t, "flagged"]), int(at.loc[t, "true_positives"]))
        for t in legacy_disclosures.LEGACY_THRESHOLDS
    }


@LOCAL
def test_the_real_file_is_of_the_released_model_and_keeps_the_legacy_rule(real):
    assert real["format"] == evidence.FORMAT
    assert legacy_disclosures.describes(real["identity"])
    assert tuple(real["sweep_basis"]["thresholds"]) == legacy_disclosures.RELEASE_CUTS


@LOCAL
def test_no_new_small_group_follows_from_the_real_file_and_the_reports_together(real):
    stated = _exact_legacy()
    found = audit.published_violations(real, _bands(real), legacy=stated, external=True)
    assert found == []
    # The legacy figures alone give small groups away - the ones the reports always did.
    legacy_only, _ = audit.score_violations(audit.legacy_facts(stated))
    assert legacy_only


@LOCAL
def test_the_cuts_the_demo_once_published_would_add_small_groups(real):
    """The control: the earlier eleven cuts, read with the reports, cut groups of a few."""
    sweep = pd.read_csv(SWEEP)
    sweep["threshold"] = sweep["threshold"].round(2)
    old = (0.0, 0.08, 0.18, 0.23, 0.30, 0.36, 0.39, 0.5, 0.53, 0.58, 0.64)
    rows = sweep.set_index("threshold").loc[list(old)]
    body = copy.deepcopy(real)
    body["sweep_basis"] = {
        "customers": int(rows["flagged"].iloc[0]),
        "thresholds": list(old),
        "true_positives": [int(v) for v in rows["true_positives"]],
        "false_positives": [int(v) for v in rows["false_positives"]],
        "false_negatives": [int(v) for v in rows["false_negatives"]],
        "flagged_churner_charges": [0.0] * len(old),
    }
    body["validation"] = None
    found = audit.published_violations(body, _bands(body), legacy=_exact_legacy())
    assert found


@LOCAL
def test_the_real_files_contract_strength_leaves_two_year_open(real):
    contract = next(
        b for b in real["breakdowns"] if b["rows"] == "Contract" and b["columns"] is None
    )
    cells = {c["row"]: c for c in contract["row_totals"]}
    assert cells["Two year"]["rate"] is None and cells["One year"]["rate"] is None
    m2m = (cells["Month-to-month"]["customers"], cells["Month-to-month"]["churned"])
    both = contract["partition_churned"] - m2m[1]
    from churnsense import reports

    ranking = reports.association_ranking(PROJECT_ROOT / "reports")
    strength = float(ranking.loc[ranking["feature"] == "Contract", "strength"].iloc[0])
    candidates = _consistent(
        strength, 2, m2m, cells["One year"]["customers"], cells["Two year"]["customers"], both
    )
    # Validation's Two year churners = public total - test - training: many values remain.
    total = legacy_disclosures.POPULATION_MARGINALS["Contract", "Two year"][1]
    test = next(r.churners for r in legacy_disclosures.TEST_SEGMENTS if r.segment == "Two year")
    implied = {total - test - t for t in candidates if total - test - t >= 0}
    assert len(implied) >= 10


@LOCAL
def test_train_validation_and_the_test_report_add_up_to_the_public_totals(real):
    """The transcribed population totals are the dataset's: checked without the test rows,
    from the training table, the validation segments and the immutable test report."""
    contract = next(
        b for b in real["breakdowns"] if b["rows"] == "Contract" and b["columns"] is None
    )
    training = {c["row"]: c["customers"] for c in contract["row_totals"]}
    validation = {
        r["segment"]: r["customers"]
        for r in real["validation"]["segments"]
        if r["dimension"] == "Contract"
    }
    for row in legacy_disclosures.TEST_SEGMENTS:
        if row.dimension != "Contract":
            continue
        customers, _ = legacy_disclosures.POPULATION_MARGINALS["Contract", row.segment]
        assert training[row.segment] + validation[row.segment] + row.customers == customers


def test_the_release_cuts_are_the_ones_the_rule_keeps_from_the_earlier_eleven():
    """The rule on the eleven cuts format 2 published, read with the reports as printed:
    exactly the release cuts survive (checked with the reports' worst-case counts)."""
    assert legacy_disclosures.RELEASE_CUTS == (0.0, 0.30, 0.36, 0.50, 0.53)
    assert all(np.isclose(c, round(c, 2)) for c in legacy_disclosures.RELEASE_CUTS)


# --- the reader: the tables tell one story ----------------------------------------------------


def _parse(body, public_predictor):
    return evidence.parse(json.loads(evidence.dumps(body)), public_predictor)


def _table(body, rows, columns):
    return next(b for b in body["breakdowns"] if b["rows"] == rows and b["columns"] == columns)


def _withheld(cell):
    return (
        cell
        | {"churned": None, "rate": None, "lift": None, "low": None, "high": None}
        | {"reason": "too_few" if cell["customers"] < 20 else "withheld"}
    )


def _first_shown(cells):
    return next(i for i, c in enumerate(cells) if c["rate"] is not None and c["customers"] >= 20)


def _with_shown(body, part):
    """The first two-way table with a shown group of at least twenty in ``part``."""
    return next(
        b
        for b in body["breakdowns"]
        if b["columns"] is not None
        and any(c["rate"] is not None and c["customers"] >= 20 for c in b[part])
    )


def test_a_two_way_table_whose_margins_are_not_its_dimensions_own_is_refused(
    body, public_predictor
):
    damaged = copy.deepcopy(body)
    table = _with_shown(damaged, "row_totals")
    i = _first_shown(table["row_totals"])
    table["row_totals"][i] = _withheld(table["row_totals"][i])
    with pytest.raises(DemoDataError, match="margins are not its dimensions' own"):
        _parse(damaged, public_predictor)


def test_two_orientations_of_a_pair_that_disagree_are_refused(body, public_predictor):
    damaged = copy.deepcopy(body)
    table = _with_shown(damaged, "cells")
    i = _first_shown(table["cells"])
    table["cells"][i] = _withheld(table["cells"][i])
    table["ranked"] = [c for c in table["ranked"] if c["rate"] is not None and c in table["cells"]]
    with pytest.raises(DemoDataError, match="orientations of the pair disagree"):
        _parse(damaged, public_predictor)


def test_the_same_customers_shown_in_one_table_and_hidden_in_another_are_refused(
    body, public_predictor
):
    """InternetService "No" is OnlineSecurity's "No internet service": hiding a group of it in
    one pair of tables while another shows the same customers is refused."""
    damaged = copy.deepcopy(body)
    system = insights.joint_system(
        {(b["rows"], b["columns"]): b for b in damaged["breakdowns"]}, damaged["lifecycle"]
    )
    shared = next(
        var
        for var in system.vars
        if var.published
        and not var.hidden
        and var.customers >= 20
        and len({(m[1], m[3]) for m in var.members if m[0] == "c"}) >= 2
    )
    key = next(m for m in shared.members if m[0] == "c")
    _, a, i, b, j = key
    for rows, columns, row, column in ((a, b, i, j), (b, a, j, i)):
        table = _table(damaged, rows, columns)
        at = next(
            n
            for n, c in enumerate(table["cells"])
            if c["row"] == insights.DIMENSIONS[rows].levels[row]
            and c["column"] == insights.DIMENSIONS[columns].levels[column]
        )
        table["cells"][at] = _withheld(table["cells"][at])
        table["ranked"] = [c for c in table["ranked"] if c in table["cells"]]
    with pytest.raises(DemoDataError, match="shown in one table and hidden in another"):
        _parse(damaged, public_predictor)


def test_a_lifecycle_that_is_not_the_tenure_table_is_refused(body, public_predictor):
    damaged = copy.deepcopy(body)
    buckets = damaged["lifecycle"]["buckets"]
    i = _first_shown(buckets)
    buckets[i] = _withheld(buckets[i])
    with pytest.raises(DemoDataError, match="not the tenure table"):
        _parse(damaged, public_predictor)


@pytest.mark.parametrize("reason", ["complementary", "few_of_an_outcome"])
def test_a_reason_that_bounds_the_count_is_refused(body, public_predictor, reason):
    """Format 4 states only what a group's size or the rule already says: the two reasons
    that told an outsider which side of twenty a hidden count lies are gone."""
    damaged = copy.deepcopy(body)
    _, cell = _first(damaged, "Contract", None, lambda c: c["reason"] == "withheld")
    level = cell["row"]
    # Every copy of the level's margin alike, so that only the reason itself is wrong.
    for table in damaged["breakdowns"]:
        for part in ("row_totals", "column_totals", "cells"):
            for c in table[part]:
                if (
                    c["column"] is None
                    and c["row"] == level
                    and "Contract"
                    in (
                        table["rows"],
                        table["columns"],
                    )
                ):
                    c["reason"] = reason
    with pytest.raises(DemoDataError, match=r"a hidden group|breakdown"):
        _parse(damaged, public_predictor)


@LOCAL
def test_the_external_audit_reads_what_every_table_together_determines(real, monkeypatch):
    """The test report's churners turn a training count into a validation one; the audit
    takes those training counts from all the tables at once (``training_determined``) - a
    two-way table can give a level the one-way table hides - not from the one-way table."""
    calls = []
    real_determined = audit.training_determined

    def spy(body):
        found = real_determined(body)
        calls.append(found)
        return found

    monkeypatch.setattr(audit, "training_determined", spy)
    assert (
        audit.published_violations(real, _bands(real), legacy=_exact_legacy(), external=True) == []
    )
    assert len(calls) == 1
    contract = {frozenset(names): value for names, value in calls[0]["Contract"]}
    assert frozenset({"Month-to-month"}) in contract
    assert frozenset({"One year", "Two year"}) in contract
    assert frozenset({"Two year"}) not in contract and frozenset({"One year"}) not in contract


# --- the image build's check: the baked file is what the validation accepts -------------------


def test_the_check_passes_a_file_the_validation_leaves_as_it_is(body, public_predictor, tmp_path):
    path = tmp_path / evidence.EVIDENCE_FILE
    path.write_text(evidence.dumps(body), encoding="utf-8")
    before = path.read_bytes()
    assert migrate.check_file(path, public_predictor) == []
    assert path.read_bytes() == before


def test_the_check_refuses_a_file_the_guard_would_change_and_writes_nothing(
    public_predictor, public_book, shap_cache, monkeypatch, tmp_path
):
    """The tables as each one alone would publish them - the joint pass switched off - are a
    file no builder writes now: the guard would hide more of them, and the image is not
    built from it."""
    from churnsense import joint_disclosure
    from churnsense.demo import build

    def per_table_only(self, **_):
        return {key for var in self.vars if var.published and var.hidden for key in var.members}

    with monkeypatch.context() as patch:
        patch.setattr(joint_disclosure.Joint, "guard", per_table_only)
        patch.setattr(insights, "_TABLES", {})
        patch.setattr(evidence, "audited", lambda body, *_args, **_kwargs: body)
        older = build.evidence_of(public_predictor, public_book, shap_cache)
    insights._TABLES.clear()
    path = tmp_path / evidence.EVIDENCE_FILE
    path.write_text(evidence.dumps(older), encoding="utf-8")
    before = path.read_bytes()
    assert migrate.check_file(path, public_predictor)
    assert path.read_bytes() == before


def test_the_check_refuses_an_unreadable_or_older_file(body, public_predictor, tmp_path):
    path = tmp_path / evidence.EVIDENCE_FILE
    path.write_text("{not json", encoding="utf-8")
    assert migrate.check_file(path, public_predictor)
    older = copy.deepcopy(body)
    older["format"] = 3
    path.write_text(evidence.dumps(older), encoding="utf-8")
    assert any("format" in p for p in migrate.check_file(path, public_predictor))


def test_the_check_command_writes_nothing_and_says_so_by_its_exit_status(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(migrate, "check_file", lambda path, _model: calls.append(path) or ["x"])
    monkeypatch.setattr(
        migrate, "migrate_file", lambda *_: (_ for _ in ()).throw(AssertionError("wrote"))
    )
    monkeypatch.setattr(migrate, "_served_model", lambda: type("M", (), {"source": tmp_path})())
    assert migrate.main(["--check"]) == 1
    assert calls == [tmp_path / evidence.EVIDENCE_FILE]
    monkeypatch.setattr(migrate, "check_file", lambda *_: [])
    assert migrate.main(["--check"]) == 0
