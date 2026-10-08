"""Retention scenario simulator.

The only page in the dashboard where money appears, and every figure on it is
a simulation. The assumptions are sliders rather than constants precisely so
that a user can see how fragile the conclusions are: halve the offer success
rate and the whole business case changes.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Business, Config
from churnsense.evaluation.threshold import (
    indifference_band,
    recommend_threshold_from,
    scenario_at,
    sweep_thresholds,
)


def _assumption_controls(cfg: Config) -> Business:
    st.markdown("**Assumptions** - change these and every figure below changes with them.")
    columns = st.columns(4)
    with columns[0]:
        cost = st.slider(
            f"Cost of one retention offer ({cfg.business.currency})",
            0,
            300,
            int(cfg.business.retention_offer_cost),
            step=5,
            key="sim_cost",
        )
    with columns[1]:
        success = st.slider(
            "Offer success rate",
            0.0,
            1.0,
            float(cfg.business.offer_success_rate),
            step=0.05,
            key="sim_success",
            help="Probability an offer retains a customer who would "
            "otherwise have left. The dataset contains no campaign "
            "outcomes, so this cannot be estimated from it.",
        )
    with columns[2]:
        horizon = st.slider(
            "Revenue horizon (months)",
            1,
            36,
            int(cfg.business.expected_horizon_months),
            key="sim_horizon",
        )
    with columns[3]:
        margin = st.slider(
            "Gross margin", 0.0, 1.0, float(cfg.business.gross_margin), step=0.05, key="sim_margin"
        )
    return Business(cfg.business.currency, float(cost), success, int(horizon), float(margin))


def _economics_chart(
    sweep: pd.DataFrame, chosen: float, business: Business, band: pd.DataFrame
) -> go.Figure:
    """The band is passed in rather than recomputed, so the shading, the
    caption and the recommendation are guaranteed to describe the same rows."""
    tolerance = business.retention_offer_cost
    ceiling = sweep["net_benefit"].max()

    figure = go.Figure()
    if tolerance > 0 and len(band) > 1:  # mirrored by the caption in render()
        figure.add_vrect(
            x0=float(band["threshold"].min()),
            x1=float(band["threshold"].max()),
            fillcolor=viz.ACCENT_ALT,
            opacity=0.14,
            line_width=0,
        )
        # Annotated separately rather than via annotation_position: the shape's
        # own label sits inside the band and collides with the curve, which
        # passes through its top-left corner at exactly the interesting point.
        figure.add_annotation(
            x=float(band["threshold"].max()),
            y=ceiling,
            text="within one offer of the best",
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            xshift=8,
            font={"color": viz.INK_MUTED, "size": 10},
        )
    figure.add_trace(
        go.Scatter(
            x=sweep["threshold"],
            y=sweep["net_benefit"],
            mode="lines",
            line={"color": viz.ACCENT, "width": 2.5},
            name="Net benefit",
            hovertemplate="threshold %{x:.2f}<br>net benefit %{y:,.0f}<extra></extra>",
        )
    )
    figure.add_hline(y=0, line_color=viz.AXIS, line_width=1)
    figure.add_vline(x=chosen, line_dash="dash", line_color=viz.ACCENT_ALT, line_width=2)
    figure.update_layout(
        xaxis_title="Decision threshold",
        yaxis_title="Simulated net benefit (USD)",
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 34, "b": 8},
    )
    return figure


def _tradeoff_chart(sweep: pd.DataFrame, chosen: float) -> go.Figure:
    figure = go.Figure()
    for column, color, name in (
        ("precision", viz.SERIES[2], "Precision"),
        ("recall", viz.SERIES[3], "Recall"),
        ("flagged_share", viz.SERIES[5], "Share flagged"),
    ):
        figure.add_trace(
            go.Scatter(
                x=sweep["threshold"],
                y=sweep[column],
                mode="lines",
                name=name,
                line={"color": color, "width": 2},
                hovertemplate=f"{name} %{{y:.1%}} at %{{x:.2f}}<extra></extra>",
            )
        )
    figure.add_vline(x=chosen, line_dash="dash", line_color=viz.ACCENT_ALT, line_width=2)
    figure.update_layout(
        xaxis_title="Decision threshold",
        yaxis_title="Rate",
        yaxis={"tickformat": ".0%", "range": [0, 1.02]},
        margin={"l": 8, "r": 8, "t": 34, "b": 8},
    )
    return figure


def render(frame: pd.DataFrame, cfg: Config) -> None:
    st.header("Retention scenario simulator")
    ui.simulation_note()

    model = data.predictor()
    parts = data.splits()
    if parts is None:
        st.info("Partition information is unavailable.")
        return

    business = _assumption_controls(cfg)

    # Economics are computed on the validation partition, the same data the
    # shipped threshold was chosen on - so the recommendation the user sees
    # here is produced exactly the way the artifact's own threshold was.
    probabilities = model.predict_frame(parts.X_val)["churn_probability"].to_numpy()
    charges = parts.X_val["MonthlyCharges"]
    sweep = sweep_thresholds(parts.y_val, probabilities, charges, business)
    # recommend_threshold would otherwise recompute this identical sweep
    # internally, doubling the work on every slider nudge.
    recommended = recommend_threshold_from(sweep, business)

    st.markdown("")
    mode = st.radio(
        "Operating point",
        [
            "Recommended for these assumptions",
            f"Shipped ({model.threshold:.2f})",
            "Choose manually",
        ],
        horizontal=True,
        key="sim_mode",
    )
    if mode.startswith("Recommended"):
        threshold = recommended.threshold
    elif mode.startswith("Shipped"):
        threshold = model.threshold
    else:
        threshold = st.slider(
            "Threshold", 0.0, 1.0, float(recommended.threshold), step=0.01, key="sim_threshold"
        )

    scenario = scenario_at(sweep, threshold, business)
    row = sweep.loc[(sweep["threshold"] - threshold).abs().idxmin()]
    currency = business.currency

    ui.kpi_row(
        [
            ("Threshold", f"{threshold:.2f}", "decision cut point", "accent"),
            (
                "Customers flagged",
                f"{int(row['flagged']):,}",
                f"{row['flagged_share']:.1%} of the validation partition",
                "warn",
            ),
            (
                "Churners caught",
                f"{int(row['true_positives']):,}",
                f"recall {row['recall']:.1%}",
                "",
            ),
            ("Churners missed", f"{int(row['false_negatives']):,}", "no offer made", "crit"),
            ("Precision", f"{row['precision']:.1%}", "of those flagged", ""),
        ]
    )

    st.markdown("")
    ui.kpi_row(
        [
            (
                "Simulated campaign cost",
                ui.money(row["intervention_cost"], currency),
                f"{int(row['flagged']):,} offers at {ui.money(business.retention_offer_cost, currency)}",
                "warn",
            ),
            (
                "Simulated retained value",
                ui.money(row["retained_value"], currency),
                f"{business.offer_success_rate:.0%} success over "
                f"{business.expected_horizon_months} months",
                "",
            ),
            (
                "Simulated net benefit",
                ui.money(row["net_benefit"], currency),
                "retained value minus campaign cost",
                "accent" if row["net_benefit"] > 0 else "crit",
            ),
            (
                "Return per unit spent",
                f"{scenario.return_on_spend:.2f}x" if scenario.return_on_spend else "n/a",
                "simulated",
                "",
            ),
        ]
    )

    # Computed once, then handed to both the chart and the caption. Deriving it
    # twice is how a caption ends up describing a region the chart did not draw,
    # which is the kind of small dishonesty that erodes trust in the rest of the
    # page.
    band = indifference_band(sweep, business)
    shaded = business.retention_offer_cost > 0 and len(band) > 1

    st.markdown("")
    left, right = st.columns(2)
    with left:
        ui.question("Where does the simulated return peak, and how flat is that peak?")
        ui.chart(
            _economics_chart(sweep, threshold, business, band), height=330, key="sim_economics"
        )
    with right:
        ui.question("What does moving the threshold do to who gets contacted?")
        ui.chart(_tradeoff_chart(sweep, threshold), height=330, key="sim_tradeoff")

    if shaded:
        st.caption(
            f"The shaded region marks the {len(band)} operating points whose simulated "
            f"net benefit is within one {ui.money(business.retention_offer_cost, currency)} offer of "
            f"the best ({band['threshold'].min():.2f} to {band['threshold'].max():.2f}). "
            "Inside it the differences are smaller than the cost of a single "
            "intervention, so they are treated as tied and the threshold that contacts "
            "the fewest customers wins. Taking the raw peak would commit the campaign "
            "to extra customers for a difference the simulation cannot resolve."
        )
    else:
        st.caption(
            "Under these assumptions the optimum is distinct: no other threshold comes "
            f"within one {ui.money(business.retention_offer_cost, currency)} offer of it, so there is "
            "no indifference band to shade. Flatten the curve - a cheaper offer or a "
            "lower success rate - and a band appears."
        )

    st.divider()
    st.subheader("Which customers would be contacted")
    scored = frame[frame["churn_probability"] >= threshold].sort_values(
        "churn_probability", ascending=False
    )
    st.caption(
        f"{len(scored):,} of {len(frame):,} customers across the whole dataset sit at "
        f"or above {threshold:.2f}. Monthly charges attached to them: "
        f"{ui.money(scored['MonthlyCharges'].sum(), currency)}."
    )
    # The whole selection, not a slice. An earlier version capped the file at
    # 5,000 rows one line below a caption stating the true count, so a user
    # could be told 7,043 and handed 5,000 with nothing to indicate the gap.
    ui.download_button(
        scored[
            ["customerID", "churn_probability", "risk_band", "Contract", "tenure", "MonthlyCharges"]
        ],
        f"churnsense_target_list_{threshold:.2f}.csv",
        f"Download all {len(scored):,} (CSV)",
        "sim_download",
    )

    ui.caveat(
        f"Every {currency} figure on this page is a simulation driven by the four "
        "sliders above. The dataset records no retention campaigns, so none of "
        "these numbers has been observed. They are a structured way to reason "
        "about a trade-off, not a forecast."
    )
