"""Static figures for the evaluation report.

Each answers one question and none uses a second y-axis. Net benefit (money)
and precision/recall (rates) share a threshold axis but nothing else, so they
are stacked as small multiples rather than overlaid on twin scales - a dual
axis lets the reader "see" a crossing point that is an artefact of two
arbitrary scalings.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve

from churnsense import viz

_W = 8.0


def threshold_economics(
    sweep: pd.DataFrame,
    chosen: float,
    currency: str,
    out: Path,
    band: pd.DataFrame | None = None,
) -> Path:
    """Two stacked panels on a shared threshold axis: money, then rates.

    ``band`` is the indifference region, passed in rather than re-derived, so
    the shading here and the recommendation that produced ``chosen`` cannot
    describe different rows. The dashboard's equivalent chart does the same.
    """
    fig, (top, bottom) = viz.figure(
        2, 1, figsize=(_W, 5.6), sharex=True, gridspec_kw={"height_ratios": [1.25, 1]}
    )

    top.plot(sweep["threshold"], sweep["net_benefit"], color=viz.ACCENT, linewidth=2.2)
    top.axhline(0, color=viz.AXIS, linewidth=1)

    # Shade the indifference band. The chosen threshold is deliberately not the
    # argmax, so showing only a peak marker would make the choice look like an
    # error; the band is what makes it legible.
    if band is not None and len(band) > 1:
        ceiling = sweep["net_benefit"].max()
        top.axvspan(
            band["threshold"].min(),
            band["threshold"].max(),
            color=viz.ACCENT_ALT,
            alpha=0.13,
            zorder=0,
        )
        top.annotate(
            "within one offer of the best",
            xy=(band["threshold"].mean(), ceiling),
            textcoords="offset points",
            xytext=(0, 14),
            ha="center",
            color=viz.INK_MUTED,
            fontsize=8.5,
        )

    at_chosen = float(sweep.loc[sweep["threshold"].sub(chosen).abs().idxmin(), "net_benefit"])
    top.scatter(
        [chosen],
        [at_chosen],
        s=90,
        color=viz.ACCENT_ALT,
        zorder=3,
        edgecolor=viz.SURFACE,
        linewidth=1.5,
    )
    # Anchored to the chosen point's own value, not to the peak: those are no
    # longer the same number once the indifference band is applied.
    top.annotate(
        f"chosen {chosen:.2f} - {viz.money(at_chosen, currency)}",
        xy=(chosen, at_chosen),
        textcoords="offset points",
        xytext=(14, -38),
        ha="left",
        va="top",
        color=viz.ACCENT_ALT,
        fontsize=9,
    )
    top.margins(y=0.18)
    top.set_title("What does the threshold cost, and what does it return? (SIMULATED)")
    top.set_ylabel(f"Net benefit ({currency})")
    top.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:,.0f}k")

    for column, color, label in (
        ("precision", viz.SERIES[2], "Precision"),
        ("recall", viz.SERIES[3], "Recall"),
    ):
        bottom.plot(sweep["threshold"], sweep[column], color=color, linewidth=2, label=label)
    bottom.axvline(chosen, color=viz.ACCENT_ALT, linewidth=1.2, linestyle="--")
    bottom.set_xlabel("Decision threshold")
    bottom.set_ylabel("Rate")
    bottom.set_ylim(0, 1.02)
    bottom.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    bottom.legend(loc="center right")

    fig.align_ylabels()
    return viz.save_figure(fig, out / "threshold_economics.png")


def reliability(table: pd.DataFrame, ece: float, out: Path) -> Path:
    """Predicted probability against observed rate, sized by bin population."""
    fig, ax = viz.figure(figsize=(5.4, 4.6))
    ax.plot(
        [0, 1], [0, 1], color=viz.AXIS, linestyle="--", linewidth=1.4, label="Perfect calibration"
    )

    sizes = 40 + 320 * table["count"] / table["count"].max()
    ax.plot(
        table["mean_predicted"], table["observed_rate"], color=viz.ACCENT, linewidth=2, zorder=2
    )
    ax.scatter(
        table["mean_predicted"],
        table["observed_rate"],
        s=sizes,
        color=viz.ACCENT,
        zorder=3,
        edgecolor=viz.SURFACE,
        linewidth=1.5,
        label="Bin (area = customers)",
    )

    ax.set_title("Do predicted probabilities match reality?")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed churn rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.annotate(
        f"ECE = {ece:.4f}", xy=(0.05, 0.9), xycoords="axes fraction", color=viz.INK, fontsize=11
    )
    ax.legend(loc="lower right")
    return viz.save_figure(fig, out / "reliability.png")


def precision_recall(y_true, y_proba, out: Path) -> Path:
    """The PR curve, against the only baseline that means anything here."""
    precision, recall, _ = precision_recall_curve(y_true, y_proba)
    base_rate = float(np.mean(y_true))

    fig, ax = viz.figure(figsize=(5.4, 4.6))
    ax.plot(recall, precision, color=viz.ACCENT, linewidth=2.2, label="Model")
    ax.axhline(
        base_rate,
        color=viz.STATUS["critical"],
        linestyle="--",
        linewidth=1.6,
        label=f"No-skill baseline ({base_rate:.1%})",
    )

    ax.set_title("Precision against recall on the test partition")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(loc="upper right")
    return viz.save_figure(fig, out / "precision_recall.png")


def shap_importance(importance: pd.DataFrame, out: Path, top_n: int = 12) -> Path:
    """Mean absolute SHAP per feature - model influence, not causal effect."""
    data = importance.head(top_n).iloc[::-1]
    fig, ax = viz.figure(figsize=(_W, 4.4))
    bars = ax.barh(data["label"], data["mean_abs_shap"], color=viz.ACCENT, height=0.56)
    for bar, value in zip(bars, data["mean_abs_shap"], strict=True):
        ax.text(
            bar.get_width() * 1.02,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            color=viz.INK,
            fontsize=9,
        )

    ax.set_title("Which features move this model's output most?")
    ax.set_xlabel("Mean |SHAP value| (probability points)")
    ax.set_xlim(0, float(data["mean_abs_shap"].max()) * 1.16)
    ax.grid(axis="y", visible=False)
    return viz.save_figure(fig, out / "shap_importance.png")
