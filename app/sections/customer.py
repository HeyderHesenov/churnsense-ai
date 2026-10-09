"""Score a single customer from a form."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Config
from churnsense.data import schema
from churnsense.explainability.shap_explain import explain_customer, narrate

#: Form defaults, from the single definition in the column contract.
DEFAULTS = schema.EXAMPLE_HIGH_RISK

GROUPS: dict[str, list[str]] = {
    "Account": ["Contract", "tenure", "PaperlessBilling", "PaymentMethod"],
    "Household": ["SeniorCitizen", "Partner", "Dependents"],
    "Services": [
        "PhoneService",
        "MultipleLines",
        "InternetService",
        "OnlineSecurity",
        "OnlineBackup",
        "DeviceProtection",
        "TechSupport",
        "StreamingTV",
        "StreamingMovies",
    ],
    "Billing": ["MonthlyCharges", "TotalCharges"],
}


def _gauge(probability: float, threshold: float, band_name: str, cfg: Config) -> go.Figure:
    figure = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=probability * 100,
            number={"suffix": "%", "font": {"size": 42, "color": viz.INK}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": viz.INK_MUTED, "tickfont": {"size": 10}},
                "bar": {
                    "color": viz.RISK_BAND_COLORS.get(band_name, viz.ACCENT),
                    "thickness": 0.72,
                },
                "bgcolor": viz.SURFACE_RAISED,
                "borderwidth": 0,
                # Derived from the risk-band palette rather than written out,
                # so a band colour cannot mean one thing on this gauge and
                # another everywhere else in the app.
                "steps": [
                    {
                        "range": [band.min * 100, min(band.max, 1.0) * 100],
                        "color": viz.rgba(viz.RISK_BAND_COLORS[band.name], 0.14),
                    }
                    for band in cfg.risk_bands
                ],
                "threshold": {
                    "line": {"color": viz.ACCENT_ALT, "width": 3},
                    "thickness": 0.85,
                    "value": threshold * 100,
                },
            },
        )
    )
    figure.update_layout(margin={"l": 20, "r": 20, "t": 10, "b": 10})
    return figure


def _field(name: str, key_prefix: str):
    """Render the right widget for a feature, driven by the column contract."""
    label = schema.FEATURE_LABELS.get(name, name)
    key = f"{key_prefix}_{name}"

    if name in schema.ALLOWED_CATEGORIES:
        options = list(schema.ALLOWED_CATEGORIES[name])
        default = str(DEFAULTS[name])
        display = {"0": "No", "1": "Yes"} if name == "SeniorCitizen" else None
        return st.selectbox(
            label,
            options,
            index=options.index(default),
            key=key,
            format_func=(lambda v: display[v]) if display else str,
        )
    if name == "tenure":
        return st.number_input(label, 0, 120, int(DEFAULTS[name]), step=1, key=key)
    low, high = schema.NUMERIC_BOUNDS[name]
    return st.number_input(
        label,
        float(low),
        float(high),
        float(DEFAULTS[name]),
        step=1.0,
        key=key,
        help="Total charges to date; set 0 for a customer who has not been billed yet."
        if name == "TotalCharges"
        else None,
    )


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Single customer risk")
    st.caption(
        "The same prediction path the API serves. Submitting this form and "
        "POSTing the same values to `/predict` return the same probability."
    )

    model = data.predictor()
    columns = model.feature_columns

    with st.form("customer_form"):
        tabs = st.tabs(list(GROUPS))
        record: dict[str, object] = {}
        for tab, fields in zip(tabs, GROUPS.values(), strict=True):
            with tab:
                grid = st.columns(min(3, len(fields)))
                for position, name in enumerate(f for f in fields if f in columns):
                    with grid[position % len(grid)]:
                        record[name] = _field(name, "form")
        submitted = st.form_submit_button("Score this customer", type="primary")

    if not submitted:
        st.info("Fill in the customer's details and submit to see a risk estimate.")
        return

    result = model.predict_one(record)

    left, right = st.columns([1, 1.3])
    with left:
        ui.question("How likely is this customer to churn?")
        ui.chart(
            _gauge(result.churn_probability, result.threshold, result.risk_band, cfg),
            height=300,
            key="cu_gauge",
        )
        st.markdown(
            f"**{ui.format_probability(result.churn_probability)}** estimated churn "
            f"probability &nbsp;·&nbsp; risk band {ui.band_chip(result.risk_band)} &nbsp;·&nbsp; "
            f"<span style='color:{viz.INK_MUTED}'>threshold {result.threshold:.2f}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"**{'Flagged for intervention' if result.flagged else 'Not flagged'}** "
            f"at the current threshold."
        )

    with right:
        ui.question("What is driving this particular estimate?")
        # The shared training-partition background, so "relative to an average
        # customer" means the same here as on the Explainability page.
        single = pd.DataFrame([record])[columns]
        with st.spinner("Computing contributions..."):
            explanation = explain_customer(
                model.model,
                single,
                row=0,
                top_k=6,
                background=data.shap_background(),
                seed=cfg.random_seed,
            )
        for sentence in narrate(explanation):
            st.markdown(f"- {sentence}")

    ui.caveat(
        "A probability is an estimate of similarity to customers who churned in a "
        "historical snapshot. It is not a statement about this person's intent, and "
        "the contributions above are model attributions rather than causes."
    )

    scored = pd.DataFrame([{**record, **result.as_dict()}])
    ui.download_button(
        scored, "churnsense_single_prediction.csv", "Download this prediction (CSV)", "cu_download"
    )
