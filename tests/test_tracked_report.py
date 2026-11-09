"""When a tracked report is missing: three environments, three answers.

Only temporary files are touched here; nothing reads ``reports/``.
"""

from __future__ import annotations

import pytest

from tests import conftest
from tests.conftest import tracked_report

NAME = "model_comparison.md"
SECRET = "0.8123"

_LOCAL = [{}, {"CI": ""}, {"CI": "false"}, {"CI": "0"}, {"GITHUB_ACTIONS": "false"}]
_GENERIC_CI = [
    {"CI": "true"},
    {"CI": "1"},
    {"CI": "true", "GITHUB_ACTIONS": "false"},
    {"CI": "true", "GITHUB_ACTIONS": ""},
]
_GITHUB_ACTIONS = [{"GITHUB_ACTIONS": "true"}, {"GITHUB_ACTIONS": "true", "CI": "true"}]
_EVERYWHERE = [*_LOCAL, *_GENERIC_CI, *_GITHUB_ACTIONS]


def _ids(cases: list[dict[str, str]]) -> list[str]:
    return [",".join(f"{k}={v}" for k, v in case.items()) or "nothing-set" for case in cases]


def _outcome(name: str) -> tuple[str, str]:
    """What the helper did, as a value.

    A skip left to travel would mark the test that expected a failure as skipped, not failed,
    and the suite would stay green over the very mistake it exists to catch.
    """
    try:
        tracked_report(name)
    except pytest.fail.Exception as failure:
        return "failed", str(failure)
    except pytest.skip.Exception as skip:
        return "skipped", str(skip)
    return "returned", ""


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "TRACKED_REPORTS", tmp_path)
    return tmp_path


@pytest.fixture
def environment(monkeypatch):
    def set_to(values: dict[str, str]) -> None:
        for name in ("GITHUB_ACTIONS", "CI"):
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return set_to


@pytest.mark.parametrize("values", _LOCAL, ids=_ids(_LOCAL))
def test_a_local_copy_skips_and_says_it_is_local(reports, environment, values):
    environment(values)
    kind, said = _outcome(NAME)
    assert kind == "skipped"
    assert NAME in said
    assert "local" in said
    assert "CI" not in said


@pytest.mark.parametrize("values", _GENERIC_CI, ids=_ids(_GENERIC_CI))
def test_generic_ci_is_not_taken_for_github_actions(reports, environment, values):
    environment(values)
    kind, said = _outcome(NAME)
    assert kind == "skipped"
    assert NAME in said
    assert "generic CI, not GitHub Actions" in said


@pytest.mark.parametrize("values", _GITHUB_ACTIONS, ids=_ids(_GITHUB_ACTIONS))
def test_github_actions_fails_when_a_tracked_report_is_missing(reports, environment, values):
    environment(values)
    kind, said = _outcome(NAME)
    assert kind == "failed"
    assert NAME in said
    assert "GitHub Actions" in said


@pytest.mark.parametrize("values", _EVERYWHERE, ids=_ids(_EVERYWHERE))
def test_a_report_that_is_there_is_returned_everywhere(reports, environment, values):
    environment(values)
    (reports / NAME).write_text(SECRET)
    assert _outcome(NAME) == ("returned", "")
    assert tracked_report(NAME) == reports / NAME


@pytest.mark.parametrize(
    "name",
    [
        "final_evaluation.md",
        "final_evaluation.json",
        "held_out_release.md",
        "figures/reliability.png",
        f"../{NAME}",
    ],
)
@pytest.mark.parametrize(
    "values",
    [{}, {"CI": "true"}, {"GITHUB_ACTIONS": "true"}],
    ids=["local", "generic-ci", "github-actions"],
)
def test_no_integration_check_can_ask_for_held_out_output(reports, environment, values, name):
    """Skipping would hide the mistake; and being there does not make it allowed."""
    environment(values)
    target = reports / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SECRET)
    kind, said = _outcome(name)
    assert kind == "failed"
    assert "final evaluation" in said
    assert SECRET not in said


def test_the_reports_a_check_may_need_are_validation_and_eda_only():
    assert sorted(conftest.INTEGRATION_REPORTS) == [
        "eda_report.md",
        "model_comparison.csv",
        "model_comparison.md",
        "threshold_sweep.csv",
    ]
