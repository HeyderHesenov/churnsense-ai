"""Explainability: what moves the model, globally and for one customer."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Config
from churnsense.explainability.shap_explain import CAVEAT, explain_customer, narrate

CAUSATION_WARNING = (
    "SHAP values describe how a feature moved **this model's output** relative to "
    "an average customer. They are not causal effects. Nothing here says that "
    "changing a feature would change whether a customer stays - a customer who "
    "buys tech support may simply be more invested already."
)


def _global_chart(importance: pd.DataFrame, top_n: int) -> go.Figure:
    data_frame = importance.head(top_n).iloc[::-1]
    figure = go.Figure(
        go.Bar(
            x=data_frame["mean_abs_shap"],
            y=data_frame["label"],
            orientation="h",
            marker_color=viz.ACCENT,
            text=[f"{v:.3f}" for v in data_frame["mean_abs_shap"]],
            textposition="outside",
            customdata=data_frame[["share"]].to_numpy(),
            hovertemplate=(
                "<b>%{y}</b><br>mean |SHAP| %{x:.4f}"
                "<br>%{customdata[0]:.1%} of total influence<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        xaxis_title="Mean |SHAP value| (probability points)",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 20, "t": 10, "b": 8},
    )
    return ui.add_label_headroom(figure, "x", 1.22)


def _waterfall(explanation) -> go.Figure:
    contributions = list(reversed(explanation.contributions))
    values = [c.percentage_points for c in contributions]
    labels = [f"{c.label} = {c.value}" for c in contributions]
    colors = [viz.STATUS["critical"] if v > 0 else viz.SERIES[0] for v in values]

    figure = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.1f} pts" for v in values],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>%{x:+.2f} percentage points<extra></extra>",
        )
    )
    figure.add_vline(x=0, line_color=viz.AXIS, line_width=1.5)
    figure.update_layout(
        xaxis_title="Contribution to estimated risk (percentage points)",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 20, "t": 10, "b": 8},
    )
    # Signed values, so the range is symmetric rather than anchored at zero.
    figure.update_traces(cliponaxis=False)
    span = max((abs(v) for v in values), default=1.0) * 1.4
    figure.update_xaxes(range=[-span, span])
    return figure


def render(frame: pd.DataFrame, cfg: Config) -> None:
    st.header("Explainability")
    st.warning(CAUSATION_WARNING)

    importance = data.shap_importance()
    model = data.predictor()

    st.subheader("Global feature influence")
    if importance is None:
        st.info(
            "Global SHAP values have not been computed yet. Run `make explain` to "
            "cache them; the dashboard reads the cache rather than recomputing on "
            "every interaction."
        )
    else:
        cached = data.report_json("shap_global.json") or {}
        ui.question("Which features move this model's predictions the most?")
        top_n = st.slider("Features to show", 5, min(18, len(importance)), 12, key="sh_top")
        ui.chart(_global_chart(importance, top_n), height=max(260, 26 * top_n), key="sh_global")
        st.caption(
            f"Computed on {cached.get('n_explained', '?')} "
            f"{cached.get('partition', 'validation')} customers against a "
            f"{cached.get('n_background', '?')}-customer background. "
            "This ranking differs from the univariate association ranking in the "
            "EDA report, and both are published: contract type is the strongest "
            "single signal on its own, but it overlaps heavily with tenure, so its "
            "unique contribution inside a multivariate model is smaller."
        )

    st.divider()
    st.subheader("Why this customer?")
    ui.question("Which of this customer's attributes pushed the estimate up or down?")

    scored = frame.sort_values("churn_probability", ascending=False)
    labels = {
        f"{row.customerID} - {ui.format_probability(row.churn_probability)} "
        f"({row.risk_band})": row.customerID
        for row in scored.head(300).itertuples()
    }
    chosen_label = st.selectbox(
        "Customer (highest risk first)",
        list(labels),
        key="sh_customer",
        help="Limited to the 300 highest-risk customers to keep the list usable.",
    )
    customer_id = labels[chosen_label]

    position = frame.index.get_loc(frame.index[frame["customerID"] == customer_id][0])
    features = frame.loc[:, model.feature_columns]

    with st.spinner("Computing SHAP contributions..."):
        explanation = explain_customer(
            model.model, features, row=int(position), top_k=8, seed=cfg.random_seed
        )

    left, right = st.columns([2, 1])
    with left:
        ui.chart(_waterfall(explanation), height=360, key="sh_local")
    with right:
        ui.kpi(
            "Estimated churn probability",
            ui.format_probability(explanation.probability),
            f"average customer: {explanation.base_value:.1%}",
            "crit",
        )
        st.markdown("**In words**")
        for sentence in narrate(explanation):
            st.markdown(f"- {sentence}")

    ui.caveat(
        CAVEAT + " Probabilities at the very ends of the scale are shown as bounds rather "
        "than exact values: isotonic calibration is a step function, so its "
        "terminal bins would otherwise report 0% and 100% - certainty no sample "
        "of this size can support."
    )
