"""Contract, tenure, payment and pricing analysis."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from churnsense import analysis as an
from churnsense import viz
from churnsense.config import Config


def _with_column(frame: pd.DataFrame, name: str, build) -> pd.DataFrame:
    """Return a frame carrying ``name``, computing it only if it is absent."""
    return frame if name in frame.columns else frame.assign(**{name: build(frame)})


def _rate_bars(frame: pd.DataFrame, column: str, label: str, color: str) -> go.Figure:
    rates = an.churn_rate_by(frame, column)
    figure = go.Figure(
        go.Bar(
            x=rates[column].astype(str),
            y=rates["churn_rate"],
            marker_color=color,
            text=[f"{r:.1%}" for r in rates["churn_rate"]],
            textposition="outside",
            customdata=rates[["customers", "churned"]].to_numpy(),
            hovertemplate=(
                "<b>%{x}</b><br>churn rate %{y:.1%}<br>"
                "%{customdata[1]:,} of %{customdata[0]:,} customers<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        xaxis_title=label,
        yaxis_title="Churn rate",
        yaxis_tickformat=".0%",
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
    )
    return ui.add_label_headroom(figure, "y", 1.16)


def _heatmap(frame: pd.DataFrame) -> go.Figure:
    rate, counts = an.risk_concentration(frame)
    text = [
        [
            "n&lt;20"
            if pd.isna(rate.iat[i, j])
            else f"{rate.iat[i, j]:.0%}<br>n={counts.iat[i, j]:,}"
            for j in range(rate.shape[1])
        ]
        for i in range(rate.shape[0])
    ]
    figure = go.Figure(
        go.Heatmap(
            z=rate.to_numpy(),
            x=rate.columns.astype(str),
            y=rate.index.astype(str),
            colorscale=[
                [i / (len(viz.SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(viz.SEQUENTIAL_BLUE)
            ],
            zmin=0,
            zmax=0.6,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 11},
            hovertemplate="<b>%{y}, %{x} months</b><br>churn rate %{z:.1%}<extra></extra>",
            colorbar={"title": "Churn rate", "tickformat": ".0%"},
            hoverongaps=False,
        )
    )
    figure.update_layout(
        xaxis_title="Tenure (months)",
        yaxis_title=None,
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
    )
    return figure


def _addon_dumbbell(frame: pd.DataFrame) -> go.Figure:
    table = an.service_addon_rates(frame)
    figure = go.Figure()
    for _, row in table.iterrows():
        figure.add_trace(
            go.Scatter(
                x=[row["with_addon"], row["without_addon"]],
                y=[row["addon"]] * 2,
                mode="lines",
                line={"color": viz.AXIS, "width": 3},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    for column, name, color in (
        ("with_addon", "With add-on", viz.SERIES[0]),
        ("without_addon", "Without add-on", viz.STATUS["critical"]),
    ):
        figure.add_trace(
            go.Scatter(
                x=table[column],
                y=table["addon"],
                mode="markers",
                name=name,
                marker={"size": 13, "color": color, "line": {"color": viz.SURFACE, "width": 2}},
                hovertemplate=f"<b>%{{y}}</b><br>{name}: %{{x:.1%}}<extra></extra>",
            )
        )
    figure.update_layout(
        xaxis_title="Churn rate",
        xaxis_tickformat=".0%",
        yaxis_title=None,
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return figure


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Contract, tenure, payment and pricing")
    st.caption("Observed churn across the dimensions a retention team can actually act on.")

    tabs = st.tabs(["Lifecycle", "Pricing", "Products", "Concentration"])

    with tabs[0]:
        # data.customers() already attached tenure_bucket and charge_band;
        # recomputing them over 7,043 rows on every rerun of a tab buys nothing.
        working = _with_column(frame, "tenure_bucket", lambda f: an.tenure_bucket(f["tenure"]))
        ui.question("When in the customer lifecycle does churn happen?")
        ui.chart(
            _rate_bars(working, "tenure_bucket", "Tenure (months)", viz.ACCENT_ALT),
            height=320,
            key="dr_tenure",
        )
        churned_median = frame.loc[frame["Churn"].eq(1), "tenure"].median()
        retained_median = frame.loc[frame["Churn"].eq(0), "tenure"].median()
        st.caption(
            f"Median tenure is {churned_median:.0f} months for churned customers "
            f"against {retained_median:.0f} for retained ones. Risk is front-loaded, "
            "which argues for onboarding interventions over uniform campaigns."
        )

    with tabs[1]:
        working = _with_column(frame, "charge_band", lambda f: an.charge_band(f["MonthlyCharges"]))
        ui.question("Are the customers we lose the expensive ones?")
        ui.chart(
            _rate_bars(working, "charge_band", "Monthly charges", viz.SERIES[3]),
            height=320,
            key="dr_price",
        )
        rates = an.churn_rate_by(working, "charge_band")
        peak = rates.loc[rates["churn_rate"].idxmax()]
        st.caption(
            f"The relationship is not monotonic: churn peaks in the "
            f"{peak['charge_band']} band at {peak['churn_rate']:.1%} and falls to "
            f"{rates.iloc[-1]['churn_rate']:.1%} among the highest-paying customers. "
            "The top band is dominated by long-tenure customers holding several "
            "services, so price and commitment pull against each other there. A "
            "plain 'higher price drives churn' reading does not fit the shape."
        )

    with tabs[2]:
        ui.question("Which add-ons go with lower churn?")
        ui.chart(_addon_dumbbell(frame), height=360, key="dr_addons")
        st.caption(
            "Restricted to customers who have internet - for everyone else these "
            "columns read 'No internet service' and the comparison would be "
            "meaningless. Support-shaped add-ons show the widest gaps. This is "
            "**not** evidence that giving someone tech support retains them: "
            "customers who buy support may simply be more invested already."
        )

    with tabs[3]:
        ui.question("Where does churn risk concentrate across two dimensions at once?")
        ui.chart(_heatmap(frame), height=330, key="dr_heatmap")
        st.caption(
            "Cells with fewer than 20 customers are left blank rather than coloured. "
            "A rate computed over a handful of customers is noise wearing the "
            "costume of a finding, and colouring it invites someone to act on it."
        )
