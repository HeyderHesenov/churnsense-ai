"""ChurnSense AI dashboard.

Run with ``make app``, which is
``streamlit run app/streamlit_app.py --server.address 127.0.0.1``. Keep the
address flag when launching it any other way: without it Streamlit listens on
every network interface, and the dashboard has no authentication.

This file does three things and nothing else: configure the page, resolve the
shared state every section needs, and route to a section. All analysis lives
in ``churnsense``; all rendering lives in ``app/sections``. The dashboard is a
view over the library, never a second implementation of it - which is what
keeps a number here identical to the same number from the API.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Running `streamlit run app/streamlit_app.py` puts app/ on sys.path but not the
# repository root, so `import app.sections...` would fail on a fresh checkout.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import components as ui  # noqa: E402
from app import data, theme  # noqa: E402
from app.sections import (  # noqa: E402
    batch,
    customer,
    drivers,
    explain,
    explorer,
    overview,
    performance,
    simulator,
)
from churnsense.logging_setup import configure_logging  # noqa: E402

SECTIONS = {
    "Executive overview": overview.render,
    "Customer explorer": explorer.render,
    "Drivers and segments": drivers.render,
    "Model performance": performance.render,
    "Explainability": explain.render,
    "Single customer": customer.render,
    "Retention simulator": simulator.render,
    "Batch scoring": batch.render,
}


def _sidebar() -> str:
    model = data.predictor()
    st.sidebar.markdown("## ChurnSense AI")
    st.sidebar.caption("Customer Retention Intelligence")

    choice = st.sidebar.radio("Section", list(SECTIONS), key="nav", label_visibility="collapsed")

    st.sidebar.divider()
    if model is None:
        st.sidebar.error("No model loaded")
    else:
        meta = model.meta
        st.sidebar.markdown(
            f"**Model**  \n{meta['display_name']}  \n"
            f"**Threshold**  \n{meta['threshold']:.2f}  \n"
            f"**Trained**  \n{meta['trained_at'][:10]}"
        )
        if meta.get("test_metrics"):
            test = meta["test_metrics"]
            st.sidebar.caption(
                f"Test ROC-AUC {test['roc_auc']:.3f} · "
                f"precision {test['precision']:.3f} · recall {test['recall']:.3f}"
            )

    st.sidebar.divider()
    st.sidebar.caption(
        "Decision support, not a decision system. Probabilities express similarity "
        "to customers who churned in a historical snapshot; they carry no causal "
        "claim. Every monetary figure in this app is a simulation under stated "
        "assumptions."
    )
    return choice


def main() -> None:
    st.set_page_config(
        page_title="ChurnSense AI",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.apply()
    configure_logging()

    cfg = data.config()
    choice = _sidebar()

    model, frame = data.predictor(), data.scored()
    if model is None or frame is None:
        ui.model_missing(
            "The dashboard reads a trained artifact from `artifacts/` and the raw "
            "dataset from `data/raw/`. One of them is missing."
        )

    SECTIONS[choice](frame, cfg)


main()
