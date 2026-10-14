"""Model comparison, validation performance, segments and calibration.

This page computes nothing from the test partition. Everything computed live
here is on the validation partition, which development may re-read freely; the
test result is shown only as the figure ``make evaluate CONFIRM=yes`` recorded
in the artifact's metadata, never recomputed. How outcomes reach a page is set
out in ``app/data.py``.
"""

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
from churnsense.data import schema
from churnsense.evaluation.metrics import (
    evaluate,
    expected_calibration_error,
    reliability_table,
    segment_table,
)

#: Who the segment table is shown for; the same breakdown `make train` publishes.
SEGMENTS: tuple[str, ...] = ("Contract", "InternetService", "SeniorCitizen", "gender")


def _comparison_chart(table: pd.DataFrame) -> go.Figure:
    """CV score with one standard error either side: the quantities the rule used."""
    ordered = table.sort_values("cv_pr_auc")
    # viz.AXIS sits at 1.4:1 against the card surface - effectively invisible as
    # a fill. The unselected bars still have to be readable.
    colors = [viz.ACCENT if selected else "#4a5058" for selected in ordered["selected"]]
    leader = table.loc[table["cv_pr_auc"].idxmax()]
    cutoff = float(leader["cv_pr_auc"] - leader["cv_se"])
    figure = go.Figure(
        go.Bar(
            x=ordered["cv_pr_auc"],
            y=ordered["model"],
            orientation="h",
            marker_color=colors,
            error_x={
                "type": "data",
                "array": ordered["cv_se"],
                "color": viz.INK_MUTED,
                "thickness": 1.5,
                "width": 5,
            },
            text=[f"{v:.4f}" for v in ordered["cv_pr_auc"]],
            textposition="outside",
            customdata=ordered[["val_pr_auc"]].to_numpy(),
            hovertemplate=(
                "<b>%{y}</b><br>CV PR-AUC %{x:.4f} +/- %{error_x.array:.4f} (SE)"
                "<br>validation PR-AUC %{customdata[0]:.4f}<extra></extra>"
            ),
        )
    )
    figure.add_vline(
        x=cutoff,
        line_dash="dash",
        line_color=viz.ACCENT_ALT,
        line_width=1.4,
        annotation_text=f"cutoff {cutoff:.4f}",
        annotation_position="top",
        annotation_font_color=viz.ACCENT_ALT,
    )
    figure.update_layout(
        xaxis_title="Cross-validated PR-AUC on train (error bars: one standard error)",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 8, "r": 20, "t": 24, "b": 8},
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


def _segment_frame(segments: pd.DataFrame) -> pd.DataFrame:
    """The segment table as displayed: rates with their 95 % intervals."""

    def interval(value: float, low: float, high: float) -> str:
        return "—" if pd.isna(value) else f"{value:.2f} [{low:.2f}, {high:.2f}]"

    return pd.DataFrame(
        {
            "Dimension": segments["dimension"],
            "Segment": segments["segment"],
            "Customers": segments["customers"],
            "Churn rate": segments["churn_rate"].map("{:.1%}".format),
            "Flagged": segments["flagged"],
            "Recall [95% CI]": [
                interval(*row)
                for row in segments[["recall", "recall_low", "recall_high"]].to_numpy()
            ],
            "Precision [95% CI]": [
                interval(*row)
                for row in segments[["precision", "precision_low", "precision_high"]].to_numpy()
            ],
        }
    )


def _recorded_test_result(meta: dict) -> None:
    """The final evaluation as recorded in the metadata - shown, never recomputed."""
    st.subheader("Recorded test-set result")
    test = meta.get("test_metrics")
    if not test:
        st.info(
            "No final test evaluation is recorded for this artifact. The published "
            "figures, and the history of that partition, are in "
            "`reports/final_evaluation.md` and `docs/MODEL_CARD.md`. This dashboard "
            "computes no performance figure from the test partition."
        )
        return
    when = meta.get("test_evaluated_at", "before evaluation dates were recorded")
    st.caption(
        f"Recorded by `make evaluate CONFIRM=yes` ({str(when)[:10]}) at threshold "
        f"{test['threshold']:.2f} and stored with this artifact; not recomputed here. "
        "The test partition has been scored more than once in this project's history: "
        'see docs/MODEL_CARD.md, "Evaluation history".'
    )
    ui.kpi_row(
        [
            ("Precision", f"{test['precision']:.3f}", "recorded on test", ""),
            ("Recall", f"{test['recall']:.3f}", "recorded on test", ""),
            ("ROC-AUC", f"{test['roc_auc']:.3f}", "recorded on test", ""),
            ("PR-AUC", f"{test['average_precision']:.3f}", "recorded on test", ""),
        ]
    )


def render(frame: pd.DataFrame, cfg: Config) -> None:  # noqa: ARG001 - dispatch signature
    st.header("Model performance")

    model = data.predictor()
    meta = model.meta
    comparison = data.report_csv("model_comparison.csv")

    st.caption(
        f"Serving **{meta['display_name']}**, trained {meta['trained_at'][:10]} on "
        f"{meta['n_train']:,} customers"
        + (f", calibrated with {meta['calibration_method']}." if meta["calibrated"] else ".")
    )

    if comparison is not None and {"cv_se", "within_one_se"} <= set(comparison.columns):
        st.subheader("Candidate comparison")
        ui.question("Is the selected model actually better than the alternatives?")
        ui.chart(_comparison_chart(comparison), height=240, key="pf_compare")
        st.caption(
            "Cross-validated on the training partition. The dashed line is one "
            "standard error below the leader: every model to its right is "
            "statistically tied with it, and the simplest of those is selected. "
            "Validation scores are in the tooltip as separate evidence."
        )

    # The validation rows of the scored book (app/data.py), not re-scored.
    validation = data.labelled(frame, "validation")
    if validation.empty:
        ui.outcomes_withheld(data.withheld_reason())
        st.divider()
        _recorded_test_result(meta)
        return
    y_val = validation[schema.TARGET].to_numpy()
    proba = validation["churn_probability"].to_numpy()
    metrics = evaluate(y_val, proba, threshold=model.threshold)

    st.divider()
    st.subheader(f"Validation performance at threshold {model.threshold:.2f}")
    st.caption(
        f"The validation partition ({metrics.n:,} customers) decided whether "
        "calibration is kept and where the threshold sits, so these numbers are "
        "somewhat optimistic. It is shown live because development may re-read it; "
        "nothing here is computed from the held-out test partition."
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
        precision, recall, _ = precision_recall_curve(y_val, proba)
        ui.chart(
            _curve(
                recall,
                precision,
                x_title="Recall",
                y_title="Precision",
                baseline=float(np.mean(y_val)),
                baseline_label="no-skill",
                name="PR",
            ),
            height=300,
            key="pf_pr",
        )
    with middle:
        ui.question("How well does the model rank customers?")
        fpr, tpr, _ = roc_curve(y_val, proba)
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
    st.subheader("Who the threshold reaches")
    ui.question("Does one global threshold serve every segment?")
    segments = segment_table(y_val, proba, validation, SEGMENTS, model.threshold)
    st.dataframe(_segment_frame(segments), hide_index=True, width="stretch")
    unscanned = segments[segments["flagged"].eq(0) & segments["churners"].gt(0)]
    if not unscanned.empty:
        names = ", ".join(f"{r.segment} ({r.dimension})" for r in unscanned.itertuples())
        st.warning(
            f"Nobody is flagged in: {names}. A segment with no flags is not scanned - "
            "that is not the same as no risk. Its churners would get nothing from a "
            "campaign driven by this threshold."
        )
    ui.caveat(
        "Validation partition, with 95 % Wilson intervals: small segments give wide "
        "intervals, and the interval is the finding. `gender` is not a model input; "
        "it is shown because excluding a column does not show the groups are treated "
        "alike. This is a descriptive check, not a fairness certification."
    )

    st.divider()
    st.subheader("Calibration")
    ui.question("When the model says 70%, do 70% of those customers actually churn?")
    table = reliability_table(y_val, proba)
    ui.chart(
        _reliability_chart(table, expected_calibration_error(y_val, proba)),
        height=340,
        key="pf_cal",
    )
    st.caption(
        f"Brier score {metrics.brier:.4f}. Calibration is a headline metric rather "
        "than a diagnostic because the probability itself is what people read here "
        "and in the API, and the risk bands are fixed probability ranges. Calibration "
        "was accepted on this partition's error, so the curve is the one that made "
        "that decision."
    )

    ui.caveat(
        "Accuracy is deliberately absent from the tiles above. Predicting that "
        f"nobody churns would score {1 - metrics.positives / metrics.n:.1%} on this "
        "partition and be worth nothing."
    )

    st.divider()
    _recorded_test_result(meta)
