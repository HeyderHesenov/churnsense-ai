"""Model comparison, test-set performance and calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sklearn.metrics import precision_recall_curve, roc_curve

from app import components as ui
from app import data
from churnsense import viz
from churnsense.config import Config
from churnsense.evaluation.metrics import (
    evaluate,
    expected_calibration_error,
    reliability_table,
)


def _comparison_chart(table: pd.DataFrame) -> go.Figure:
    ordered = table.sort_values("val_pr_auc")
    # viz.AXIS sits at 1.4:1 against the card surface - effectively invisible as
    # a fill. The unselected bars still have to be readable.
    colors = [viz.ACCENT if selected else "#4a5058" for selected in ordered["selected"]]
    figure = go.Figure(
        go.Bar(
            x=ordered["val_pr_auc"],
            y=ordered["model"],
            orientation="h",
            marker_color=colors,
            error_x={
                "type": "data",
                "array": ordered["cv_std"],
                "color": viz.INK_MUTED,
                "thickness": 1.5,
                "width": 5,
            },
            text=[f"{v:.4f}" for v in ordered["val_pr_auc"]],
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>validation PR-AUC %{x:.4f}"
                "<br>CV std +/-%{error_x.array:.4f}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        xaxis_title="Validation PR-AUC (error bars: cross-validation std)",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 20, "t": 10, "b": 8},
    )
    return ui.add_label_headroom(figure, "x", 1.3)


def _curve(
    x, y, *, x_title: str, y_title: str, baseline: float | None, baseline_label: str, name: str
) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="lines",
            name=name,
            line={"color": viz.ACCENT, "width": 2.5},
            hovertemplate=f"{x_title} %{{x:.1%}}<br>{y_title} %{{y:.1%}}<extra></extra>",
        )
    )
    if baseline is not None:
        figure.add_hline(
            y=baseline,
            line_dash="dash",
            line_color=viz.STATUS["critical"],
            line_width=1.6,
            annotation_text=baseline_label,
            annotation_position="bottom right",
            annotation_font_color=viz.STATUS["critical"],
        )
    figure.update_layout(
        xaxis_title=x_title,
        yaxis_title=y_title,
        xaxis={"tickformat": ".0%", "range": [0, 1]},
        yaxis={"tickformat": ".0%", "range": [0, 1.02]},
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
    )
    return figure


def _reliability_chart(table: pd.DataFrame, ece: float) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            name="Perfect calibration",
            line={"color": viz.AXIS, "dash": "dash", "width": 1.6},
            hoverinfo="skip",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=table["mean_predicted"],
            y=table["observed_rate"],
            mode="lines+markers",
            name="Model",
            line={"color": viz.ACCENT, "width": 2.5},
            marker={
                "size": 6 + 22 * table["count"] / table["count"].max(),
                "color": viz.ACCENT,
                "line": {"color": viz.SURFACE, "width": 1.5},
            },
            customdata=table[["count"]].to_numpy(),
            hovertemplate=(
                "predicted %{x:.1%}<br>observed %{y:.1%}"
                "<br>%{customdata[0]:,} customers<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        xaxis_title="Mean predicted probability",
        yaxis_title="Observed churn rate",
        xaxis={"tickformat": ".0%", "range": [0, 1]},
        yaxis={"tickformat": ".0%", "range": [0, 1]},
        title=f"Expected calibration error {ece:.4f}",
        showlegend=False,
        margin={"l": 8, "r": 8, "t": 40, "b": 8},
    )
    return figure


def _confusion(metrics) -> go.Figure:
    matrix = metrics.confusion
    labels = [["True negative", "False positive"], ["False negative", "True positive"]]
    text = [[f"{labels[i][j]}<br><b>{matrix[i][j]:,}</b>" for j in range(2)] for i in range(2)]
    figure = go.Figure(
        go.Heatmap(
            z=matrix,
            x=["Predicted stay", "Predicted churn"],
            y=["Actually stayed", "Actually churned"],
            colorscale=[
                [i / (len(viz.SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(viz.SEQUENTIAL_BLUE)
            ],
            text=text,
            texttemplate="%{text}",
            textfont={"size": 13},
            showscale=False,
            hovertemplate="%{text}<extra></extra>",
        )
    )
    figure.update_layout(margin={"l": 8, "r": 8, "t": 28, "b": 8})
    figure.update_yaxes(autorange="reversed")
    return figure


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Model performance")

    model = data.predictor()
    meta = model.meta
    comparison = data.report_csv("model_comparison.csv")
    parts = data.splits()

    st.caption(
        f"Serving **{meta['display_name']}**, trained {meta['trained_at'][:10]} on "
        f"{meta['n_train']:,} customers"
        + (f", calibrated with {meta['calibration_method']}." if meta["calibrated"] else ".")
    )

    if comparison is not None:
        st.subheader("Candidate comparison")
        ui.question("Is the selected model actually better than the alternatives?")
        ui.chart(_comparison_chart(comparison), height=240, key="pf_compare")
        st.caption(
            "Scored on the validation partition. The error bars are the "
            "cross-validation standard deviation and they overlap, which is the "
            "point: the top models are statistically tied. Selection uses the "
            "one-standard-error rule and breaks the tie on simplicity rather than "
            "chasing a difference the data cannot support."
        )

    if parts is None:
        st.info("Partition information is unavailable.")
        return

    # Through Predictor, not the raw model: predict_frame applies the column
    # contract (a clear error instead of a raw sklearn one when the artifact
    # and configs/config.yaml disagree), exactly as every other surface does.
    test_proba = model.predict_frame(parts.X_test)["churn_probability"].to_numpy()
    metrics = evaluate(parts.y_test, test_proba, threshold=model.threshold)

    st.divider()
    st.subheader(f"Test-set performance at threshold {model.threshold:.2f}")
    st.caption(
        f"The test partition ({metrics.n:,} customers) was scored once, after "
        "model selection and threshold tuning were both closed on validation."
    )

    ui.kpi_row(
        [
            ("Precision", f"{metrics.precision:.3f}", "of those flagged, really churned", "accent"),
            ("Recall", f"{metrics.recall:.3f}", "of churners, caught", "accent"),
            ("F1", f"{metrics.f1:.3f}", "harmonic mean", ""),
            ("ROC-AUC", f"{metrics.roc_auc:.3f}", "ranking quality", ""),
            (
                "PR-AUC",
                f"{metrics.average_precision:.3f}",
                f"baseline {metrics.positives / metrics.n:.3f}",
                "",
            ),
        ]
    )

    st.markdown("")
    left, middle, right = st.columns(3)
    with left:
        ui.question("How does precision trade against recall?")
        precision, recall, _ = precision_recall_curve(parts.y_test, test_proba)
        ui.chart(
            _curve(
                recall,
                precision,
                x_title="Recall",
                y_title="Precision",
                baseline=float(np.mean(parts.y_test)),
                baseline_label="no-skill",
                name="PR",
            ),
            height=300,
            key="pf_pr",
        )
    with middle:
        ui.question("How well does the model rank customers?")
        fpr, tpr, _ = roc_curve(parts.y_test, test_proba)
        ui.chart(
            _curve(
                fpr,
                tpr,
                x_title="False positive rate",
                y_title="True positive rate",
                baseline=None,
                baseline_label="",
                name="ROC",
            ),
            height=300,
            key="pf_roc",
        )
    with right:
        ui.question("What does the model get right and wrong?")
        ui.chart(_confusion(metrics), height=300, key="pf_cm")

    st.divider()
    st.subheader("Calibration")
    ui.question("When the model says 70%, do 70% of those customers actually churn?")
    table = reliability_table(parts.y_test, test_proba)
    ui.chart(
        _reliability_chart(table, expected_calibration_error(parts.y_test, test_proba)),
        height=340,
        key="pf_cal",
    )
    st.caption(
        f"Brier score {metrics.brier:.4f}. Calibration is reported as a headline "
        "metric rather than a diagnostic because the retention simulator multiplies "
        "a predicted probability by a customer's value. A model that ranks well but "
        "is systematically overconfident would produce a plausible-looking budget "
        "built on inflated numbers."
    )

    ui.caveat(
        "Accuracy is deliberately absent from the tiles above. Predicting that "
        f"nobody churns would score {1 - metrics.positives / metrics.n:.1%} on this "
        "partition and be worth nothing."
    )
