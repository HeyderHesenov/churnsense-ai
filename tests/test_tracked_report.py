"""When a tracked report is missing: three environments, three answers.

Only temporary files are touched here; nothing reads ``reports/``.
"""

from __future__ import annotations

import pytest

from tests import conftest
from tests.conftest import tracked_report

NAME = "model_comparison.csv"
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
    assert {"model_comparison.md", "threshold_sweep.csv"} == conftest.LOCAL_REPORTS
    assert conftest.LOCAL_REPORTS < conftest.INTEGRATION_REPORTS


# --- reports kept out of git (E8): written by `make train`, checked where they exist -----------

_LOCAL_ONLY = sorted(conftest.LOCAL_REPORTS)


@pytest.mark.parametrize("name", _LOCAL_ONLY)
@pytest.mark.parametrize("values", _EVERYWHERE, ids=_ids(_EVERYWHERE))
def test_a_report_kept_out_of_git_is_skipped_everywhere_when_absent(
    reports, environment, values, name
):
    """GitHub Actions cannot have it: its absence there is expected, not a failure."""
    environment(values)
    kind, said = _outcome(name)
    assert kind == "skipped"
    assert name in said
    assert "not tracked" in said and "make train" in said


@pytest.mark.parametrize("name", _LOCAL_ONLY)
@pytest.mark.parametrize("values", _EVERYWHERE, ids=_ids(_EVERYWHERE))
def test_a_report_kept_out_of_git_is_checked_where_it_exists(reports, environment, values, name):
    environment(values)
    (reports / name).write_text(SECRET)
    assert _outcome(name) == ("returned", "")


def test_the_reports_kept_out_of_git_are_ignored_and_untracked():
    """They state validation figures at every 0.01 and segment cells of a few customers."""
    import subprocess

    from churnsense.config import PROJECT_ROOT

    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    tracked = subprocess.run(
        ["git", "ls-files", "reports"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert tracked, "git lists no report: this checks nothing"
    for name in conftest.LOCAL_REPORTS:
        assert f"reports/{name}" in ignored
        assert f"reports/{name}" not in tracked
    for name in conftest.INTEGRATION_REPORTS - conftest.LOCAL_REPORTS:
        assert f"reports/{name}" in tracked
