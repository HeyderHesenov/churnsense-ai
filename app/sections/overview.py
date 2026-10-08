"""Executive overview: the state of the book, and where risk sits."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import analysis as an
from churnsense import viz
from churnsense.config import Config


def _band_bars(
    frame: pd.DataFrame, values: pd.Series, *, axis_title: str, fmt, hover: str
) -> go.Figure:
    """Horizontal bars over the risk bands, highest risk at the top.

    Both band charts share this form on purpose. They cover the same four
    categories, so giving each its own orientation would make the reader
    re-learn the layout halfway across the row for no gain.
    """
    order = [b for b in viz.RISK_BAND_COLORS if (frame["risk_band"] == b).any()]
    series = values.reindex(order).fillna(0)

    figure = go.Figure(
        go.Bar(
            x=series.to_numpy(),
            y=series.index.astype(str),
            orientation="h",
            marker_color=ui.band_colors(order),
            text=[fmt(v) for v in series],
            textposition="outside",
            hovertemplate=hover,
        )
    )
    figure.update_layout(
        xaxis_title=axis_title,
        yaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 20, "t": 10, "b": 8},
    )
    # Bands read top-to-bottom in scale order (Low -> Critical), matching the
    # caption beneath and the band chips used elsewhere. Plotly places the
    # first category at the bottom of a y-axis, hence the reversal.
    figure.update_yaxes(categoryorder="array", categoryarray=order[::-1])
    return ui.add_label_headroom(figure, "x", 1.22)


def _risk_distribution(frame: pd.DataFrame) -> go.Figure:
    return _band_bars(
        frame,
        frame["risk_band"].value_counts(),
        axis_title="Customers",
        fmt=lambda v: f"{int(v):,}",
        hover="<b>%{y} risk</b><br>%{x:,} customers<extra></extra>",
    )


def _revenue_at_risk(frame: pd.DataFrame, currency: str) -> go.Figure:
    return _band_bars(
        frame,
        frame.groupby("risk_band", observed=True)["MonthlyCharges"].sum(),
        axis_title=f"Monthly charges ({currency})",
        fmt=lambda v: ui.money(v, currency),
        hover="<b>%{y} risk</b><br>%{x:,.0f} per month<extra></extra>",
    )


def _churn_by_dimension(frame: pd.DataFrame, column: str, title: str) -> go.Figure:
    rates = an.churn_rate_by(frame, column, sort_by_rate=True)
    figure = go.Figure(
        go.Bar(
            x=rates["churn_rate"],
            y=rates[column].astype(str),
            orientation="h",
            marker_color=viz.ACCENT,
            text=[f"{r:.1%}" for r in rates["churn_rate"]],
            textposition="outside",
            customdata=rates[["customers", "churned"]].to_numpy(),
            hovertemplate=(
                "<b>%{y}</b><br>churn rate %{x:.1%}<br>"
                "%{customdata[1]:,} of %{customdata[0]:,} customers<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title=title,
        xaxis_title="Observed churn rate",
        yaxis_title=None,
        showlegend=False,
        xaxis_tickformat=".0%",
        margin={"l": 8, "r": 20, "t": 40, "b": 8},
    )
    figure.update_yaxes(autorange="reversed")
    return ui.add_label_headroom(figure, "x", 1.22)


def render(frame: pd.DataFrame, cfg: Config) -> None:
    st.header("Executive overview")
    st.caption(
        "Observed outcomes from the historical snapshot, alongside the model's "
        "current risk scoring of the same customers."
    )

    churned = frame["Churn"].eq(1)
    flagged = frame["flagged"]
    high_risk = frame["risk_band"].isin(["High", "Critical"])
    model = data.predictor()
    currency = cfg.business.currency

    ui.kpi_row(
        [
            ("Customers", f"{len(frame):,}", "in the dataset", "accent"),
            (
                "Observed churn rate",
                f"{churned.mean():.1%}",
                f"{int(churned.sum()):,} customers left",
                "crit",
            ),
            (
                "Monthly charges on the book",
                ui.money(frame["MonthlyCharges"].sum(), currency),
                "sum across all customers",
                "",
            ),
            (
                "High or critical risk",
                f"{int(high_risk.sum()):,}",
                f"{high_risk.mean():.1%} of customers",
                "warn",
            ),
            (
                f"Flagged at threshold {model.threshold:.2f}" if model else "Flagged",
                f"{int(flagged.sum()):,}",
                "would receive an offer",
                "warn",
            ),
        ]
    )

    st.markdown("")
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Risk distribution")
        ui.question("How many customers sit in each risk band right now?")
        ui.chart(_risk_distribution(frame), height=300, key="ov_bands")
    with right:
        st.subheader("Monthly charges by risk band")
        ui.question("How much recurring revenue is attached to each band?")
        ui.chart(_revenue_at_risk(frame, currency), height=300, key="ov_revenue")

    st.caption(
        "Risk bands are fixed probability ranges "
        + ", ".join(f"{b.name} {b.min:.0%}-{min(b.max, 1.0):.0%}" for b in cfg.risk_bands)
        + ". They are independent of the decision threshold, so retuning the "
        "threshold does not silently redefine what the team calls 'Critical'."
    )

    st.divider()
    st.subheader("Where churn concentrated historically")
    first, second = st.columns(2)
    with first:
        ui.question("Which contract types lost customers?")
        ui.chart(_churn_by_dimension(frame, "Contract", ""), height=240, key="ov_contract")
    with second:
        ui.question("Which payment methods go with leaving?")
        ui.chart(_churn_by_dimension(frame, "PaymentMethod", ""), height=240, key="ov_payment")

    ui.caveat(
        "These are observed associations in one historical snapshot. Customers who "
        "expect to stay are also the ones willing to sign long contracts, so part of "
        "the contract gap is self-selection rather than an effect of the contract. "
        "Nothing here identifies a cause."
    )
