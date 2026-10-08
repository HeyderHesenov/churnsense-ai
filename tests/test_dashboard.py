"""Smoke tests for the dashboard.

These need a trained artifact and the real dataset, so they are marked `slow`
and skip cleanly when either is absent — CI runs the rest of the suite on
synthetic fixtures alone.

They exist because the defects this dashboard actually shipped were runtime
ones: a `pandas.Index` that `st.cache_data` could not hash crashed the explorer
on open, and nothing in the unit suite noticed. A section that raises on load
is the cheapest possible bug to catch and the most embarrassing one to miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from churnsense.config import load_config

pytestmark = pytest.mark.slow

SECTIONS = [
    "Executive overview",
    "Customer explorer",
    "Drivers and segments",
    "Model performance",
    "Explainability",
    "Single customer",
    "Retention simulator",
    "Batch scoring",
]

# Absolute: AppTest resolves a relative path against the *calling file*, which
# is this module, not the repository root.
APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def _requires_artifacts() -> None:
    cfg = load_config()
    if not cfg.paths.model_file.is_file() or not cfg.raw_data_file.is_file():
        pytest.skip("needs `make data && make train`; not available in a clean checkout")


@pytest.fixture(scope="module")
def app_test():
    _requires_artifacts()
    from streamlit.testing.v1 import AppTest

    return AppTest


def _run(AppTest, section: str):
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    at.radio(key="nav").set_value(section).run()
    return at


@pytest.mark.parametrize("section", SECTIONS)
def test_section_renders_without_raising(app_test, section: str):
    at = _run(app_test, section)
    assert not at.exception, f"{section}: {at.exception}"
    assert not at.error, f"{section} rendered an error block: {[e.value for e in at.error]}"


def test_the_app_starts_on_the_overview(app_test):
    at = app_test.from_file(APP, default_timeout=240)
    at.run()
    assert not at.exception
    assert at.radio(key="nav").value == "Executive overview"


def test_moving_an_assumption_slider_changes_the_simulated_result(app_test):
    """The simulator must actually be reactive, not a static picture."""
    at = _run(app_test, "Retention simulator")
    before = at.session_state["sim_success"]

    at.slider(key="sim_success").set_value(min(0.95, before + 0.3)).run()

    assert not at.exception
    assert at.session_state["sim_success"] != before


def test_the_explorer_filters_narrow_the_selection(app_test):
    at = _run(app_test, "Customer explorer")
    at.multiselect(key="filter_Contract").set_value(["Two year"]).run()

    assert not at.exception
    assert at.session_state["filter_Contract"] == ["Two year"]


def test_submitting_the_customer_form_produces_a_score(app_test):
    at = _run(app_test, "Single customer")
    at.button[0].click().run()

    assert not at.exception
    rendered = " ".join(m.value for m in at.markdown)
    assert "estimated churn" in rendered.lower()
