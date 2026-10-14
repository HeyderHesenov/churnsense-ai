"""Smoke tests for the dashboard, on synthetic data.

The real app script runs end to end, served a synthetic model and book through
``app.data`` (the ``dashboard`` fixture in conftest.py). They used to need the
real dataset and a trained artifact, so they skipped in CI and, run locally,
scored the real test partition; now they run everywhere and read nothing real.

They exist because the defects this dashboard actually shipped were runtime
ones: a `pandas.Index` that `st.cache_data` could not hash crashed the explorer
on open, and nothing in the unit suite noticed. A section that raises on load
is the cheapest possible bug to catch and the most embarrassing one to miss.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from app import data

from churnsense.data import schema
from tests.conftest import SYNTHETIC_SHA256

SECTIONS = [
    "Executive overview",
    "Customer explorer",
    "Churn patterns",
    "Model performance",
    "Explainability",
    "Single customer",
    "Retention simulator",
    "Batch scoring",
]


@pytest.mark.parametrize("section", SECTIONS)
def test_section_renders_without_raising(dashboard, section: str):
    at = dashboard(section)
    assert not at.error, f"{section} rendered an error block: {[e.value for e in at.error]}"


def test_the_app_starts_on_the_overview(dashboard):
    at = dashboard()
    assert at.radio(key="nav").value == "Executive overview"


def test_moving_an_assumption_slider_changes_the_simulated_result(dashboard):
    """The simulator must actually be reactive, not a static picture."""
    at = dashboard("Retention simulator")
    before = at.session_state["sim_success"]

    at.slider(key="sim_success").set_value(min(0.95, before + 0.3)).run()

    assert not at.exception
    assert at.session_state["sim_success"] != before


def test_the_explorer_filters_narrow_the_selection(dashboard):
    at = dashboard("Customer explorer")
    at.multiselect(key="filter_Contract").set_value(["Two year"]).run()

    assert not at.exception
    assert at.session_state["filter_Contract"] == ["Two year"]


def test_submitting_the_customer_form_produces_a_score(dashboard):
    at = dashboard("Single customer")
    at.button[0].click().run()

    assert not at.exception
    rendered = " ".join(m.value for m in at.markdown)
    assert "estimated churn" in rendered.lower()


def test_the_form_explains_an_impossible_customer_instead_of_crashing(dashboard):
    """Each field is set on its own, so the form can describe add-ons without
    internet. That is refused with the problem shown, not scored or a traceback."""
    at = dashboard("Single customer")
    at.selectbox(key="form_InternetService").set_value("No")
    at.selectbox(key="form_OnlineSecurity").set_value("Yes")
    at.button[0].click().run()

    assert not at.exception
    assert any("cannot score" in e.value for e in at.error)
    assert any("OnlineSecurity" in c.value for c in at.code)


def test_the_performance_page_never_shows_test_metrics_it_computed(dashboard):
    """Live figures are validation; a test figure may only be the recorded one."""
    at = dashboard("Model performance")
    headers = " ".join(h.value for h in at.subheader)
    assert "Validation performance" in headers
    assert "Test-set performance" not in headers


# --- partitions that cannot be trusted ----------------------------------------


@pytest.fixture(params=["dataset differs", "no dataset hash"])
def untrusted(request, clean_frame, dashboard_predictor):
    """A book whose partitions cannot be verified: every outcome withheld.

    Either the model was trained on another file, or its metadata records no
    dataset hash at all - two states with two different reasons.
    """
    meta = dict(dashboard_predictor.meta)
    if request.param == "dataset differs":
        meta["data_sha256"] = hashlib.sha256(b"another file").hexdigest()
    else:
        meta.pop("data_sha256")
    book = data.score_book(clean_frame, SYNTHETIC_SHA256, replace(dashboard_predictor, meta=meta))
    assert book.withheld == data.WITHHELD_REASONS[request.param]
    return book


def _says_why(at, book) -> bool:
    """The page shows the book's own reason, word for word, as every page does."""
    return f"Historical outcomes are withheld: {book.withheld}" in [i.value for i in at.info]


@pytest.mark.parametrize("section", ["Executive overview", "Churn patterns", "Customer explorer"])
def test_outcome_pages_say_why_outcomes_are_withheld(dashboard, untrusted, section: str):
    at = dashboard(section, served=untrusted)
    assert _says_why(at, untrusted)


def test_the_performance_page_still_shows_the_recorded_test_result(dashboard, untrusted):
    at = dashboard("Model performance", served=untrusted)
    assert _says_why(at, untrusted)
    assert "Recorded test-set result" in " ".join(h.value for h in at.subheader)


def test_the_simulator_keeps_its_contact_list(dashboard, untrusted):
    """No economics without outcomes, but the list at the shipped threshold needs none."""
    at = dashboard("Retention simulator", served=untrusted)
    assert _says_why(at, untrusted)
    assert "Which customers would be contacted" in " ".join(h.value for h in at.subheader)
    assert schema.TARGET not in untrusted.frame.columns
