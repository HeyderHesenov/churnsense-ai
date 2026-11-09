"""What a recorded test evaluation may publish, read by someone who knows the rule.

Synthetic data throughout, and the model is a stub that returns hand-made scores:
nothing here trains on, reads or scores the real dataset.

The reader these tests model holds every listed customer's attributes, flag and
score, can cross them as they like, and has read ``evaluation.disclosure``. The
question is never "is this cell hidden?" but "what can be worked out, given all of
this at once?".
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from churnsense.config import Config
from churnsense.data.split import DataSplits
from churnsense.evaluation import disclosure
from churnsense.evaluation import release as ledger
from churnsense.evaluation.disclosure import (
    MIN_CELL,
    POLICY,
    release,
    released_metrics,
)
from churnsense.evaluation.metrics import evaluate
from churnsense.evaluation.report import (
    FinalEvaluation,
    describe_release,
    render_final_report,
    score_held_out,
    summary,
)
from tests.disclosure import CUT, collision, group_ranges

_METRICS = {
    "threshold": CUT,
    "n": 1000,
    "positives": 300,
    "tn": 500,
    "fp": 200,
    "fn": 150,
    "tp": 150,
    "accuracy": 0.65,
    "precision": 0.42857142857,
    "recall": 0.5,
    "f1": 0.46153846153,
    "roc_auc": 0.78123456,
    "average_precision": 0.60111111,
    "brier": 0.15234567,
}
_KEYS = {
    "threshold",
    "n",
    "positives",
    "tn",
    "fp",
    "fn",
    "tp",
    "accuracy",
    "precision",
    "recall",
    "f1",
}
#: Each one more equation over the labels: no value of theirs is ever published.
_WITHHELD_SCALARS = ("roc_auc", "average_precision", "brier")


def _cells(tn: int, fp: int, fn: int, tp: int) -> dict[str, Any]:
    return {
        **_METRICS,
        "n": tn + fp + fn + tp,
        "positives": fn + tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
    }


# --- a partition, level by level -----------------------------------------------------


class _Stub:
    """A model that returns hand-made scores: the validation ones for validation's frame."""

    classes_ = (0, 1)

    def __init__(self, validation: np.ndarray, test: np.ndarray) -> None:
        self.validation, self.test = validation, test

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        p = self.validation if frame.attrs.get("validation") else self.test
        return np.c_[1 - p, p]


def _levels(*rows: tuple[str, str, int, int, int, int], seed: int = 1) -> pd.DataFrame:
    """Customers from ``(contract, internet, flagged, caught, unflagged, missed)`` rows."""
    rng = np.random.default_rng(seed)
    parts = []
    for contract, internet, flagged, caught, unflagged, missed in rows:
        for size, churned, low, high in (
            (flagged, caught, CUT, 0.95),
            (unflagged, missed, 0.02, CUT - 1e-6),
        ):
            parts.append(
                pd.DataFrame(
                    {
                        "Contract": contract,
                        "InternetService": internet,
                        "SeniorCitizen": rng.choice(["0", "1"], size),
                        "MonthlyCharges": rng.uniform(20, 110, size).round(2),
                        "score": rng.uniform(low, high, size),
                        "churned": np.r_[np.ones(churned), np.zeros(size - churned)].astype(int),
                    }
                )
            )
    return pd.concat(parts, ignore_index=True)


def _splits(frame: pd.DataFrame, seed: int = 2) -> tuple[DataSplits, _Stub]:
    rng = np.random.default_rng(seed)
    test = frame[["Contract", "InternetService", "SeniorCitizen", "MonthlyCharges"]]
    validation = test.copy()
    validation.attrs["validation"] = True
    scores = rng.uniform(0, 1, len(frame))
    labels = pd.Series((rng.random(len(frame)) < scores).astype(int))
    splits = DataSplits(
        validation, labels, validation, labels, test, frame["churned"].reset_index(drop=True)
    )
    return splits, _Stub(scores, frame["score"].to_numpy())


def _evaluated(frame: pd.DataFrame, cfg: Config) -> FinalEvaluation:
    splits, model = _splits(frame)
    with ledger.exclusive() as hold:
        released = score_held_out(model, splits, CUT, hold)
    return describe_release(model, splits, CUT, released, cfg)


def _published(frame_y: np.ndarray, p: np.ndarray) -> dict[str, Any] | None:
    return release(evaluate(frame_y, p, threshold=CUT).as_dict())


#: One year 1 flagged, Two year 1 flagged, beside a large Month-to-month: subtracting
#: its count from the total said "1 churner among these 2 named customers".
_ONE_AND_ONE = (
    ("M", "F", 480, 200, 320, 40),
    ("O", "D", 1, 1, 299, 29),
    ("T", "N", 1, 0, 299, 20),
)
_EIGHT_AND_ONE = (
    ("M", "F", 480, 200, 320, 40),
    ("O", "D", 8, 4, 292, 26),
    ("T", "N", 1, 0, 299, 20),
)
#: Two dimensions read together: every unflagged Month-to-month customer has no
#: internet, and that level's unflagged churners are all of theirs, so 189 others in
#: the level would be named as having stayed.
_TWO_DIMENSIONS = (
    ("M", "F", 429, 308, 0, 0),
    ("M", "D", 208, 95, 0, 0),
    ("M", "N", 102, 30, 61, 10),
    ("O", "F", 76, 19, 18, 2),
    ("O", "D", 22, 4, 95, 11),
    ("O", "N", 0, 0, 59, 0),
    ("T", "F", 8, 0, 88, 5),
    ("T", "D", 0, 0, 113, 5),
    ("T", "N", 0, 0, 130, 0),
)
_SCENARIOS = {
    "480-1-1": _ONE_AND_ONE,
    "480-8-1": _EIGHT_AND_ONE,
    "two-dimensions": _TWO_DIMENSIONS,
    "several-small-levels": (
        ("M", "F", 400, 200, 300, 40),
        ("O", "D", 3, 1, 200, 20),
        ("T", "N", 4, 4, 200, 20),
        ("X", "F", 2, 0, 200, 20),
    ),
    "pure-levels": (
        ("M", "F", 300, 150, 300, 60),
        ("O", "D", 100, 100, 100, 0),
        ("T", "N", 100, 0, 100, 100),
    ),
    "precision-near-one": (("M", "F", 300, 280, 700, 100),),
    "exactly-twenty-in-a-cell": (("M", "F", 300, 280, 700, 20),),
}


# --- the rule -------------------------------------------------------------------------


def test_figures_that_pass_keep_exactly_the_published_keys():
    assert set(release(_METRICS)) == _KEYS


@pytest.mark.parametrize("scalar", _WITHHELD_SCALARS)
def test_a_score_metric_never_gets_through_whatever_its_value(scalar):
    """Not a null to be filled in later: the key does not exist in anything released."""
    for value in (0.0, 0.5, 0.123456, 1.0):
        assert scalar not in released_metrics({**_METRICS, scalar: value}, POLICY)
        assert scalar not in release({**_METRICS, scalar: value})


@pytest.mark.parametrize(
    "scores",
    [
        pytest.param(np.linspace(0.01, 0.99, 1000), id="spread"),
        pytest.param(np.full(1000, 0.4), id="all-tied"),
        pytest.param(np.r_[np.full(500, 0.2), np.full(500, 0.7)], id="two-values"),
        pytest.param(np.random.default_rng(0).beta(1.2, 2.5, 1000), id="calibrated-looking"),
    ],
)
def test_no_shape_of_scores_lets_a_score_metric_out(scores):
    """The withholding reads no score: there is no distribution that passes."""
    y = (np.random.default_rng(1).random(1000) < scores).astype(int)
    shown = release(evaluate(y, scores, threshold=0.5).as_dict())
    for scalar in _WITHHELD_SCALARS:
        assert shown is None or scalar not in shown


def test_the_rule_reads_nothing_but_the_table():
    from inspect import signature

    assert list(signature(release).parameters) == ["metrics"]
    import churnsense.evaluation.disclosure as module

    assert not hasattr(module, "tie_bits")
    assert not hasattr(module, "MIN_TIE_BITS")


def test_nothing_else_a_record_holds_gets_through():
    extra = {"precision_low": 0.1, "segments": [1], "reliability": [1], "ece": 0.1, "secret": 1}
    assert set(released_metrics({**_METRICS, **extra}, POLICY)) == _KEYS


def test_the_rates_come_from_the_counts_not_from_the_record():
    shown = released_metrics({**_METRICS, "precision": 1.0, "recall": 0.0, "f1": 0.0}, POLICY)
    assert shown["precision"] == 0.4286
    assert shown["recall"] == 0.5
    assert shown["f1"] == 0.4615


def test_every_share_is_rounded_to_what_the_report_prints():
    shown = release(_cells(tn=700, fp=11 * 20, fn=30, tp=50))
    assert shown["accuracy"] == 0.75
    assert shown["precision"] == 0.1852
    assert shown["recall"] == 0.625
    assert shown["f1"] == 0.2857


@pytest.mark.parametrize("cell", ["tn", "fp", "fn", "tp"])
@pytest.mark.parametrize(
    ("size", "shown"), [(0, False), (1, False), (19, False), (20, True), (21, True)]
)
def test_every_cell_of_the_table_must_hold_twenty(cell, size, shown):
    counts = {"tn": 100, "fp": 100, "fn": 100, "tp": 100} | {cell: size}
    assert (released_metrics(_cells(**counts), POLICY) is not None) is shown


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"tp": 150.0}, id="a-float-count"),
        pytest.param({"tp": 150.5}, id="a-fractional-count"),
        pytest.param({"n": 1000.9}, id="a-fractional-size"),
        pytest.param({"tp": True}, id="a-bool"),
        pytest.param({"tp": "150"}, id="a-string"),
        pytest.param({"tp": None}, id="a-null-count"),
        pytest.param({"tp": -150}, id="a-negative-count"),
        pytest.param({"n": 999}, id="cells-do-not-add-to-the-size"),
        pytest.param({"positives": 299}, id="churners-do-not-add-up"),
        pytest.param({"n": 10**400}, id="an-enormous-size"),
        pytest.param({"threshold": None}, id="no-threshold"),
        pytest.param({"threshold": 1.5}, id="threshold-above-one"),
        pytest.param({"threshold": float("nan")}, id="threshold-nan"),
        pytest.param({"threshold": float("inf")}, id="threshold-infinite"),
        pytest.param({"threshold": 10**400}, id="threshold-too-large-for-a-float"),
    ],
)
def test_a_figure_that_is_not_what_it_should_be_withholds_everything(changes):
    assert released_metrics({**_METRICS, **changes}, POLICY) is None


def test_a_table_of_exactly_twenty_in_each_cell_is_released_whole():
    shown = released_metrics(_cells(20, 20, 20, 20), POLICY)
    assert {k: shown[k] for k in ("n", "positives", "tn", "fp", "fn", "tp")} == {
        "n": 80,
        "positives": 40,
        "tn": 20,
        "fp": 20,
        "fn": 20,
        "tp": 20,
    }
    assert (shown["precision"], shown["recall"], shown["f1"], shown["accuracy"]) == (0.5,) * 4


@pytest.mark.parametrize(
    ("counts", "reason"),
    [
        ((500, 0, 100, 300), "precision would be exactly 1"),
        ((500, 100, 0, 300), "recall would be exactly 1"),
        ((500, 100, 300, 0), "precision and recall would be exactly 0"),
        ((0, 100, 100, 300), "no true negatives"),
        ((1000, 19, 1000, 20), "one small cell among large ones"),
        ((19, 1000, 20, 1000), "one small cell among large ones"),
    ],
)
def test_no_related_figure_survives_a_small_cell(counts, reason):
    """Nothing partial: not the size, not the churners, not one rate."""
    assert released_metrics(_cells(*counts), POLICY) is None, reason


def test_counts_too_large_for_any_partition_read_as_damage_though_they_add_up():
    huge = 10**10
    assert released_metrics(_cells(huge, huge, huge, huge), POLICY) is None
    assert released_metrics(_cells(10**8, 10**8, 10**8, 10**8), POLICY) is not None


@pytest.mark.parametrize(
    "metrics", [None, [], "metrics", 7, {}], ids=["null", "list", "text", "number", "empty"]
)
def test_a_record_that_is_not_a_table_withholds_everything(metrics):
    assert released_metrics(metrics, POLICY) is None


@pytest.mark.parametrize(
    "policy",
    [None, 1, POLICY + 1, str(POLICY), float(POLICY), True, [POLICY]],
    ids=["missing", "older", "newer", "text", "float", "bool", "list"],
)
def test_figures_without_this_rules_mark_are_not_read(policy):
    """Older metadata and records were written under rules since found wanting."""
    assert released_metrics(_METRICS, policy) is None


# --- what a withheld answer says -------------------------------------------------------


def test_withheld_covers_a_range_so_a_pure_side_is_not_told_from_a_nearly_pure_one():
    """Everyone flagged churned, or 19 of them stayed: a reader cannot tell which."""
    covered = [fp for fp in range(0, 40) if released_metrics(_cells(500, fp, 100, 300), POLICY)]
    assert covered[0] == MIN_CELL, "every count below the floor reads the same"
    assert not any(released_metrics(_cells(500, fp, 100, 300), POLICY) for fp in range(0, MIN_CELL))


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_the_reviewed_scenarios_publish_only_the_partition_table(name, cfg: Config):
    shown = _evaluated(_levels(*_SCENARIOS[name]), cfg).released
    assert set(shown) == _KEYS


# --- reading the released figures against the listing, every group at once -------------


def _crossed(frame: pd.DataFrame) -> pd.DataFrame:
    """The finest groups the listing lets a reader build from these attributes."""
    flagged = frame["score"] >= CUT
    return (
        frame.assign(flagged=flagged)
        .groupby(["Contract", "InternetService", "SeniorCitizen"], observed=True)["flagged"]
        .agg(flagged="sum", customers="size")
        .assign(unflagged=lambda t: t["customers"] - t["flagged"])
    )


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_the_totals_pin_no_group_a_reader_can_build(name, cfg: Config):
    """A joint integer program over every cross of the attributes, the two-dimension
    overlap included: what is published says only what the sides sum to."""
    frame = _levels(*_SCENARIOS[name])
    shown = _evaluated(frame, cfg).released
    assert shown is not None, "this scenario is meant to reach the release"
    table = _crossed(frame)
    flagged, unflagged = table["flagged"].tolist(), table["unflagged"].tolist()
    ranges = group_ranges(flagged, unflagged, shown["tp"], shown["fn"])
    for size, other, (span_t, span_u) in zip(flagged, unflagged, ranges, strict=True):
        if size:
            assert span_t[0] < span_t[1], "a group's flagged churners are fixed"
        if other:
            assert span_u[0] < span_u[1], "a group's unflagged churners are fixed"


@pytest.mark.parametrize("name", list(_SCENARIOS))
@pytest.mark.parametrize("attribute", ["Contract", "InternetService"])
def test_no_level_has_a_count_the_rule_itself_gives_away(name, attribute):
    """The reader replays the rule: other labels must give the very same output."""
    frame = _levels(*_SCENARIOS[name])
    y, p = frame["churned"].to_numpy(), frame["score"].to_numpy()
    wanted = _published(y, p)
    for level in frame[attribute].unique():
        group = (frame[attribute] == level).to_numpy()
        if group.all():
            continue
        other = collision(lambda labels: _published(labels, p), y, p, group)
        assert other is not None, f"{level} has no other labelling with this output"
        assert other[group].sum() != y[group].sum()
        assert _published(other, p) == wanted


# --- is this exactly what the rule writes? -------------------------------------------


_WRITTEN = disclosure.release(
    {"threshold": 0.36, "n": 1000, "positives": 300, "tn": 500, "fp": 200, "fn": 150, "tp": 150}
)


def test_what_the_rule_writes_is_what_it_recognises():
    assert disclosure.is_released(_WRITTEN)
    assert set(_WRITTEN) == disclosure.RELEASED_KEYS


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"brier": 0.15}, id="an-extra-figure"),
        pytest.param({"threshold": 1}, id="a-threshold-of-another-type"),
        pytest.param({"tn": 500.0}, id="a-count-of-another-type"),
        pytest.param({"precision": 0.4287}, id="a-rate-the-counts-do-not-give"),
        pytest.param({"precision": 150 / 350}, id="a-rate-not-rounded"),
        pytest.param({"tn": 19, "n": 519}, id="a-cell-under-the-floor"),
    ],
)
def test_anything_else_is_not_recognised(changed):
    assert not disclosure.is_released({**_WRITTEN, **changed})


@pytest.mark.parametrize("record", [None, [], "figures", 1, {}], ids=repr)
def test_something_that_is_not_a_record_is_not_recognised(record):
    assert not disclosure.is_released(record)


def test_a_missing_figure_is_not_recognised():
    for key in _WRITTEN:
        assert not disclosure.is_released({k: v for k, v in _WRITTEN.items() if k != key}), key


def test_a_bool_is_not_a_count_even_above_the_floor(monkeypatch):
    """Not saved by the floor: ``True`` would pass as 1 if the floor were ever lowered."""
    monkeypatch.setattr(disclosure, "MIN_CELL", 0)
    counts = {"threshold": 0.36, "tn": 500, "fp": 200, "fn": 150, "n": 851, "positives": 151}
    assert released_metrics({**counts, "tp": 1}, POLICY) is not None
    assert released_metrics({**counts, "tp": True}, POLICY) is None


def test_the_floor_is_the_rule_s_answer_and_anything_else_a_failure():
    """``None`` is final, so only the rule itself may give it."""
    assert release(_cells(tn=19, fp=200, fn=150, tp=150)) is None
    for malformed in ({**_WRITTEN, "tp": 151}, {}, [], None, {**_WRITTEN, "threshold": None}):
        with pytest.raises(ValueError, match="whole table") as raised:
            release(malformed)
        assert "150" not in str(raised.value), "the message names no figure"


def test_a_stored_record_is_judged_not_raised_on():
    """A reader gets a verdict from ``is_released``, whatever the file holds."""
    assert not disclosure.is_released({**_WRITTEN, "tp": 151})


# --- every output, end to end ---------------------------------------------------------


def _channels(evaluation: FinalEvaluation, cfg: Config, tmp: Path, caplog) -> dict[str, str]:
    """Everything an evaluation leaves behind, as text, the way a reader would find it."""
    published = ledger.Ledger(
        ledger.PUBLISHED,
        {
            **dict.fromkeys(ledger._HASH_KEYS, "a" * 64),
            "threshold": evaluation.threshold,
        },
        "now",
        evaluation.released,
    )
    return {
        "report": render_final_report(evaluation, {"display_name": "M"}, cfg),
        "ledger": ledger._encode(published),
        "terminal": summary(evaluation),
        "log": caplog.text,
    }


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_no_channel_says_more_than_the_partition_table(name, cfg: Config, tmp_path, caplog):
    frame = _levels(*_SCENARIOS[name])
    with caplog.at_level(logging.INFO):
        evaluation = _evaluated(frame, cfg)
        channels = _channels(evaluation, cfg, tmp_path, caplog)
    for label, text in channels.items():
        for word in ("One year", "reliability", "ece", "metrics_at_half", "segments"):
            assert f'"{word}"' not in text, (label, word)
        assert "| Observed rate |" not in text, label
        assert "test_optimal_threshold" not in text, label
        assert "Simulated net benefit" not in text, label
        assert "What tuning the threshold bought" not in text, label
        assert "Expected calibration error" not in text, label
        assert "Performance by segment" not in text, label
        assert "| ROC-AUC |" not in text, label
        assert "| PR-AUC" not in text, label
        assert "| Brier" not in text, label
        for scalar in _WITHHELD_SCALARS:
            assert scalar not in text, (label, scalar)


def test_the_raw_score_metrics_appear_in_no_channel(cfg: Config, tmp_path, caplog):
    """The values themselves, measured on the same customers, stay out of everything."""
    frame = _levels(("M", "F", 300, 180, 700, 120))
    raw = evaluate(frame["churned"], frame["score"], threshold=CUT).as_dict()
    with caplog.at_level(logging.INFO):
        channels = _channels(_evaluated(frame, cfg), cfg, tmp_path, caplog)
    for label, text in channels.items():
        for scalar in _WITHHELD_SCALARS:
            for rendered in (f"{raw[scalar]:.4f}", f"{raw[scalar]:.3f}", str(raw[scalar])):
                assert rendered not in text, (label, scalar)


def test_a_figure_of_a_level_appears_in_no_channel(cfg: Config, tmp_path, caplog):
    """A level with its own precision and churn rate: the values themselves stay out."""
    frame = _levels(
        ("M", "F", 480, 300, 320, 40), ("O", "D", 30, 8, 270, 26), ("T", "N", 5, 0, 295, 20)
    )
    level = frame[frame["Contract"] == "O"]
    secrets = {
        f"{level.loc[level['score'] >= CUT, 'churned'].mean():.4f}",
        f"{level['churned'].mean():.4f}",
    }
    assert secrets == {"0.2667", "0.1133"}
    with caplog.at_level(logging.INFO):
        channels = _channels(_evaluated(frame, cfg), cfg, tmp_path, caplog)
    for label, text in channels.items():
        for secret in secrets:
            assert secret not in text, (label, secret)


def test_the_withheld_channels_are_the_same_for_every_cause(cfg: Config, tmp_path, caplog):
    """Too few flagged, everybody churned: one answer."""
    causes = {
        "nobody-flagged-stayed": (("M", "F", 100, 100, 900, 100),),
        "nineteen-in-a-cell": (("M", "F", 100, 19, 900, 100),),
        "few-flagged": (("M", "F", 10, 5, 990, 100),),
    }
    answers = set()
    for name, rows in causes.items():
        evaluation = _evaluated(_levels(*rows), cfg)
        assert evaluation.released is None, name
        channels = _channels(evaluation, cfg, tmp_path, caplog)
        report = channels["report"]
        answers.add((channels["ledger"], report, channels["terminal"]))
    assert len(answers) == 1
    # Nor may that one answer name a cause some of them do not share.
    for word in ("too few", "fewer", "single outcome", "churned", "nineteen", "cell"):
        assert word not in report.lower(), word


@pytest.mark.parametrize(
    "scores",
    [
        pytest.param(np.full(1000, np.nan), id="nan"),
        pytest.param(np.full(1000, 1.5), id="above-one"),
    ],
)
def test_scores_that_are_not_probabilities_are_a_failure_and_not_an_answer(scores, cfg: Config):
    """``evaluation.release`` keeps the reservation; ``test_release`` follows it through."""
    frame = _levels(("M", "F", 300, 150, 700, 150))
    frame["score"] = scores
    with pytest.raises(ValueError, match="probabilities"):
        _evaluated(frame, cfg)


def test_a_partition_with_a_single_outcome_is_a_failure_and_not_an_answer(cfg: Config):
    """It cannot happen on the pinned dataset, so it says something is wrong with the run."""
    with pytest.raises(ValueError):
        _evaluated(_levels(("M", "F", 100, 0, 900, 0)), cfg)


def test_the_evaluation_object_cannot_carry_a_raw_figure():
    assert {f.name for f in fields(FinalEvaluation)} == {
        "threshold",
        "validation_sweep",
        "released",
    }


def test_what_the_writer_published_reads_back_unchanged(cfg: Config):
    """The API and Streamlit read through one gate, and it must not change a released figure."""
    evaluation = _evaluated(_levels(("M", "F", 300, 150, 700, 150)), cfg)
    assert evaluation.released is not None
    stored = json.loads(json.dumps(evaluation.released))
    assert released_metrics(stored, POLICY) == evaluation.released


def test_writer_and_reader_agree_on_every_scenario(cfg: Config):
    for rows in _SCENARIOS.values():
        shown = _evaluated(_levels(*rows), cfg).released
        if shown is not None:
            assert released_metrics(json.loads(json.dumps(shown)), POLICY) == shown


# --- the readers ---------------------------------------------------------------------


def _source(relative: str) -> str:
    return (Path(__file__).resolve().parents[1] / relative).read_text()


@pytest.mark.parametrize(
    "path",
    [
        "app/streamlit_app.py",
        "app/sections/performance.py",
        "src/churnsense/api/main.py",
        "src/churnsense/api/dashboard.py",
    ],
)
def test_no_reader_goes_round_the_ledger(path):
    """A sidecar's ``test_metrics`` and a stray record file are not sources any more."""
    source = _source(path)
    for stale in ('test_metrics")', 'meta["test_metrics"]', "test_disclosure_policy", "test_ece"):
        assert stale not in source, stale
    for stale in ("final_evaluation.json", "reports.final_evaluation", "roc_auc=record"):
        assert stale not in source, stale
