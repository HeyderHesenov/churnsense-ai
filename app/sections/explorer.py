"""Customer explorer: filter the book, inspect it, export the result."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Config

FILTERS = [
    ("Contract", "Contract"),
    ("InternetService", "Internet service"),
    ("PaymentMethod", "Payment method"),
    ("risk_band", "Risk band"),
]

TABLE_COLUMNS = [
    "customerID",
    "churn_probability",
    "risk_band",
    "flagged",
    "partition",
    "Contract",
    "tenure",
    "MonthlyCharges",
    "TotalCharges",
    "InternetService",
    "PaymentMethod",
    "outcome",
]


def _controls(frame: pd.DataFrame) -> pd.DataFrame:
    """Filter row. A dashboard without filtering is the documented anti-pattern."""
    st.markdown("**Filters**")
    columns = st.columns(len(FILTERS) + 2)

    mask = pd.Series(True, index=frame.index)
    for column, (field, label) in zip(columns, FILTERS, strict=False):
        with column:
            options = [str(v) for v in frame[field].dropna().unique()]
            chosen = st.multiselect(label, sorted(options), key=f"filter_{field}")
            if chosen:
                mask &= frame[field].astype(str).isin(chosen)

    with columns[-2]:
        low, high = st.slider(
            "Churn probability", 0.0, 1.0, (0.0, 1.0), step=0.05, key="filter_probability"
        )
        mask &= frame["churn_probability"].between(low, high)
    with columns[-1]:
        tenure_range = st.slider(
            "Tenure (months)",
            0,
            int(frame["tenure"].max()),
            (0, int(frame["tenure"].max())),
            key="filter_tenure",
        )
        mask &= frame["tenure"].between(*tenure_range)

    return frame[mask]


def _probability_histogram(frame: pd.DataFrame, threshold: float) -> go.Figure:
    figure = go.Figure(
        go.Histogram(
            x=frame["churn_probability"],
            nbinsx=40,
            marker_color=viz.ACCENT,
            hovertemplate="probability %{x:.2f}<br>%{y:,} customers<extra></extra>",
        )
    )
    figure.add_vline(
        x=threshold,
        line_width=2,
        line_dash="dash",
        line_color=viz.ACCENT_ALT,
        annotation_text=f"threshold {threshold:.2f}",
        annotation_position="top",
        annotation_font_color=viz.ACCENT_ALT,
    )
    figure.update_layout(
        xaxis_title="Predicted churn probability",
        yaxis_title="Customers",
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
        bargap=0.04,
    )
    return figure


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Customer explorer")
    st.caption(
        "Every customer in the dataset, scored by the shipped model. The same "
        "model and the same threshold the API serves."
    )

    model = data.predictor()
    filtered = _controls(frame)

    if filtered.empty:
        ui.empty_filter_state()

    ui.kpi_row(
        [
            ("Selected", f"{len(filtered):,}", f"of {len(frame):,} customers", "accent"),
            (
                "Mean churn probability",
                ui.format_probability(filtered["churn_probability"].mean()),
                "across the selection",
                "",
            ),
            (
                "Flagged",
                f"{int(filtered['flagged'].sum()):,}",
                f"at threshold {model.threshold:.2f}",
                "warn",
            ),
            (
                "Observed churn",
                f"{filtered['Churn'].mean():.1%}",
                "historical outcome in the selection",
                "crit",
            ),
            (
                "Monthly charges",
                f"${filtered['MonthlyCharges'].sum():,.0f}",
                "in the selection",
                "",
            ),
        ]
    )

    st.markdown("")
    ui.question("How is predicted risk spread across the selected customers?")
    ui.chart(_probability_histogram(filtered, model.threshold), height=280, key="ex_hist")

    st.subheader("Customers")
    table = filtered.reindex(columns=TABLE_COLUMNS).sort_values(
        "churn_probability", ascending=False
    )
    st.dataframe(
        table,
        width="stretch",
        height=420,
        hide_index=True,
        column_config={
            "customerID": st.column_config.TextColumn("Customer", width="small"),
            # Four decimals, not three: the probability is clamped just inside
            # [0, 1], and "1.000" would claim a certainty the model does not have.
            "churn_probability": st.column_config.ProgressColumn(
                "Churn probability", format="%.4f", min_value=0.0, max_value=1.0
            ),
            "risk_band": st.column_config.TextColumn("Risk band", width="small"),
            "flagged": st.column_config.CheckboxColumn("Flagged"),
            "partition": st.column_config.TextColumn("Partition", width="small"),
            "Contract": st.column_config.TextColumn("Contract"),
            "InternetService": st.column_config.TextColumn("Internet"),
            "PaymentMethod": st.column_config.TextColumn("Payment"),
            "tenure": st.column_config.NumberColumn("Tenure", format="%d mo"),
            "MonthlyCharges": st.column_config.NumberColumn("Monthly", format="$%.2f"),
            "TotalCharges": st.column_config.NumberColumn("Total", format="$%.2f"),
            "outcome": st.column_config.TextColumn("Actual outcome", width="small"),
        },
    )

    left, right = st.columns([1, 3])
    with left:
        ui.download_button(
            table, "churnsense_selection.csv", "Download selection (CSV)", "ex_download"
        )
    with right:
        st.caption(
            "The *Partition* column says whether a customer was used for training. "
            "A confident, correct prediction on a training row is far less "
            "impressive than the same prediction on a test row, and the table "
            "shows which is which rather than letting the distinction disappear."
        )

    ui.caveat(
        "'Actual outcome' is the historical label already present in the dataset, "
        "not a future event. The probability is the model's estimate for the same "
        "customer; it is shown alongside so the two can be compared honestly."
    )
