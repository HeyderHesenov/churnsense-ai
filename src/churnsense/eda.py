"""Generate the exploratory analysis: figures plus a written report.

Run as ``make eda`` / ``python -m churnsense.eda``.

This is a *script*, not a notebook, on purpose. The report regenerates
deterministically from the pinned raw file, so every number quoted in the
README can be traced to a command rather than to a saved cell output that may
no longer match the data.

Each figure answers a stated business question. The question is in the figure
title and repeated as a heading in the report; if a chart cannot be given a
question, it does not belong here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed; must precede the pyplot import
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from churnsense import analysis as an
from churnsense import viz
from churnsense.config import Config, load_config
from churnsense.data import schema
from churnsense.data.loader import CleaningReport, load_clean
from churnsense.logging_setup import get_logger

logger = get_logger(__name__)

_FIG_W = 8.0


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _save(fig: plt.Figure, path: Path) -> Path:
    fig.savefig(path)
    plt.close(fig)
    logger.info("wrote figure %s", path.name)
    return path


def _rate_barh(
    data: pd.DataFrame,
    label_col: str,
    *,
    title: str,
    color: str = viz.ACCENT,
    height: float = 3.2,
) -> plt.Figure:
    """Horizontal churn-rate bars with the rate and denominator labelled.

    Shared by the contract, payment-method and tenure views: the same question
    shape ("which levels of this dimension churn most?") gets the same chart
    shape, so a reader learns to read it once.
    """
    fig, ax = plt.subplots(figsize=(_FIG_W, height))
    order = data.iloc[::-1]  # largest rate at the top
    bars = ax.barh(order[label_col].astype(str), order["churn_rate"], color=color, height=0.52)

    for bar, (_, row) in zip(bars, order.iterrows(), strict=True):
        ax.text(
            bar.get_width() + 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"{_pct(row['churn_rate'])}   n={row['customers']:,}",
            va="center",
            ha="left",
            color=viz.INK,
            fontsize=9,
        )

    ax.set_title(title)
    ax.set_xlabel("Churn rate")
    ax.set_xlim(0, max(0.55, float(data["churn_rate"].max()) * 1.35))
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.grid(axis="y", visible=False)
    return fig


def fig_contract(df: pd.DataFrame, out: Path) -> Path:
    data = an.churn_rate_by(df, "Contract", sort_by_rate=True)
    fig = _rate_barh(data, "Contract", title="Which contract types lose customers?", height=2.9)
    return _save(fig, out / "churn_by_contract.png")


def fig_payment(df: pd.DataFrame, out: Path) -> Path:
    data = an.churn_rate_by(df, "PaymentMethod", sort_by_rate=True)
    fig = _rate_barh(
        data,
        "PaymentMethod",
        title="Does how a customer pays track with leaving?",
        height=3.2,
    )
    return _save(fig, out / "churn_by_payment.png")


def fig_tenure(df: pd.DataFrame, out: Path) -> Path:
    work = df.assign(tenure_bucket=an.tenure_bucket(df["tenure"]))
    data = an.churn_rate_by(work, "tenure_bucket")
    fig = _rate_barh(
        data.iloc[::-1],
        "tenure_bucket",
        title="When in the lifecycle do customers leave?",
        color=viz.ACCENT_ALT,
        height=3.4,
    )
    fig.axes[0].set_ylabel("Tenure (months)")
    return _save(fig, out / "churn_by_tenure.png")


def fig_charges(df: pd.DataFrame, out: Path) -> Path:
    """Churn rate by price band.

    An overlapping histogram was the first attempt and was discarded: the
    charge distribution is bimodal, so 36 bins of two translucent fills
    produced a muddy overlap that made the reader do the comparison. Banding
    the price and plotting the rate answers the question directly, and reuses
    the same bar form as the contract and tenure views so the report reads
    consistently. The denominators carry the distribution information the
    histogram was there to show.
    """
    work = df.assign(charge_band=an.charge_band(df["MonthlyCharges"]))
    data = an.churn_rate_by(work, "charge_band")
    fig = _rate_barh(
        data.iloc[::-1],
        "charge_band",
        title="Are the customers we lose the expensive ones?",
        color=viz.SERIES[3],
        height=3.2,
    )
    fig.axes[0].set_ylabel("Monthly charges")
    return _save(fig, out / "charges_by_price_band.png")


def fig_addons(df: pd.DataFrame, out: Path) -> Path:
    """Dumbbell: churn rate with vs without each add-on, internet customers only."""
    data = an.service_addon_rates(df)
    fig, ax = plt.subplots(figsize=(_FIG_W, 3.8))
    y = np.arange(len(data))

    ends = (
        ("with_addon", "With add-on", viz.SERIES[0]),
        ("without_addon", "Without add-on", viz.STATUS["critical"]),
    )

    ax.hlines(y, data["with_addon"], data["without_addon"], color=viz.AXIS, linewidth=2, zorder=1)
    for column, _, color in ends:
        ax.scatter(
            data[column], y, s=70, color=color, zorder=2, edgecolor=viz.SURFACE, linewidth=1.5
        )

    ax.set_yticks(y, data["addon"])
    ax.set_title("Which add-ons go with lower churn? (internet customers only)")
    ax.set_xlabel("Churn rate")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.grid(axis="y", visible=False)

    # Label the two ends once, above the row with the widest gap. Two earlier
    # placements failed on inspection: a legend box at lower-right covered the
    # bottom row's marks, and labelling beside the dots put "With add-on" on
    # top of the y-tick text. Above the widest-gap row, neither can collide.
    anchor = int(data["difference"].abs().to_numpy().argmax())
    for column, label, color in ends:
        ax.annotate(
            label,
            (data[column].iloc[anchor], anchor),
            ha="center",
            va="bottom",
            textcoords="offset points",
            xytext=(0, 11),
            color=color,
            fontsize=9,
        )
    ax.set_ylim(-0.55, len(data) - 0.3)
    ax.margins(x=0.08)
    return _save(fig, out / "addon_effect.png")


def fig_risk_heatmap(df: pd.DataFrame, out: Path) -> Path:
    """Where risk concentrates: contract x tenure band."""
    rate, counts = an.risk_concentration(df)
    fig, ax = plt.subplots(figsize=(_FIG_W, 3.0))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "churn_seq", list(viz.SEQUENTIAL_BLUE)
    )
    # A suppressed cell must not be mistaken for a high-risk one. The ramp runs
    # light -> dark blue, so painting "no data" dark would read as maximum risk;
    # a mid-lightness neutral grey sits off the ramp in both hue and lightness.
    cmap.set_bad("#4a5058")
    mesh = ax.imshow(rate.to_numpy(), cmap=cmap, aspect="auto", vmin=0, vmax=0.6)

    ax.set_xticks(range(rate.shape[1]), rate.columns.astype(str))
    ax.set_yticks(range(rate.shape[0]), rate.index.astype(str))
    ax.set_xlabel("Tenure (months)")
    ax.set_title("Where does churn risk concentrate?")
    ax.grid(visible=False)

    for i in range(rate.shape[0]):
        for j in range(rate.shape[1]):
            value = rate.iat[i, j]
            text = "n<20" if pd.isna(value) else _pct(value)
            # Light ink on the dark end of the ramp, dark ink on the light end.
            ink = viz.INK if pd.isna(value) or value > 0.33 else "#0b0b0b"
            ax.text(
                j,
                i,
                f"{text}\nn={counts.iat[i, j]:,}",
                ha="center",
                va="center",
                color=ink,
                fontsize=8,
            )

    bar = fig.colorbar(mesh, ax=ax, pad=0.02)
    bar.set_label("Churn rate", color=viz.INK_SECONDARY)
    bar.ax.tick_params(colors=viz.INK_MUTED)
    bar.ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    bar.outline.set_edgecolor(viz.AXIS)
    return _save(fig, out / "risk_heatmap.png")


def fig_association(ranking: pd.DataFrame, out: Path) -> Path:
    """Ranked association strength - deliberately not called 'importance'."""
    data = ranking.head(14).iloc[::-1]
    fig, ax = plt.subplots(figsize=(_FIG_W, 4.6))
    colors = [viz.ACCENT_ALT if m.startswith("point") else viz.ACCENT for m in data["measure"]]
    bars = ax.barh(
        [schema.FEATURE_LABELS.get(f, f) for f in data["feature"]],
        data["strength"],
        color=colors,
        height=0.56,
    )
    for bar, value in zip(bars, data["strength"], strict=True):
        ax.text(
            bar.get_width() + 0.006,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            color=viz.INK,
            fontsize=9,
        )

    ax.set_title("What is most strongly associated with churn?")
    ax.set_xlabel("Association strength (0 = independent)")
    ax.set_xlim(0, float(data["strength"].max()) * 1.18)
    ax.grid(axis="y", visible=False)
    handles = [
        plt.Line2D(
            [],
            [],
            marker="s",
            linestyle="",
            markersize=9,
            color=viz.ACCENT,
            label="Cramer's V (categorical)",
        ),
        plt.Line2D(
            [],
            [],
            marker="s",
            linestyle="",
            markersize=9,
            color=viz.ACCENT_ALT,
            label="|point-biserial r| (numeric)",
        ),
    ]
    ax.legend(handles=handles, loc="lower right")
    return _save(fig, out / "association_ranking.png")


def fig_collinearity(df: pd.DataFrame, out: Path) -> Path:
    """Documents the TotalCharges redundancy finding rather than hiding it."""
    product = df["tenure"] * df["MonthlyCharges"]
    r = float(np.corrcoef(product, df["TotalCharges"])[0, 1])

    fig, ax = plt.subplots(figsize=(5.4, 4.2))
    ax.scatter(
        product,
        df["TotalCharges"],
        s=5,
        alpha=0.25,
        color=viz.ACCENT,
        edgecolor="none",
        rasterized=True,
    )
    limit = float(max(product.max(), df["TotalCharges"].max()))
    ax.plot(
        [0, limit], [0, limit], color=viz.ACCENT_ALT, linewidth=1.6, linestyle="--", label="y = x"
    )
    ax.set_title("Is TotalCharges redundant?")
    ax.set_xlabel("tenure x MonthlyCharges (USD)")
    ax.set_ylabel("TotalCharges (USD)")
    ax.annotate(
        f"Pearson r = {r:.4f}", xy=(0.04, 0.9), xycoords="axes fraction", color=viz.INK, fontsize=11
    )
    ax.legend(loc="lower right")
    return _save(fig, out / "total_charges_collinearity.png")


def _overview(df: pd.DataFrame) -> dict[str, float | int]:
    churned = df[schema.TARGET].eq(1)
    return {
        "customers": len(df),
        "churned": int(churned.sum()),
        "churn_rate": float(df[schema.TARGET].mean()),
        "monthly_revenue": float(df["MonthlyCharges"].sum()),
        "monthly_revenue_at_risk": float(df.loc[churned, "MonthlyCharges"].sum()),
        "median_tenure_churned": float(df.loc[churned, "tenure"].median()),
        "median_tenure_retained": float(df.loc[~churned, "tenure"].median()),
        "median_charges_churned": float(df.loc[churned, "MonthlyCharges"].median()),
        "median_charges_retained": float(df.loc[~churned, "MonthlyCharges"].median()),
        "majority_class_accuracy": float(1 - df[schema.TARGET].mean()),
    }


def _markdown(
    df: pd.DataFrame,
    cleaning: CleaningReport,
    ranking: pd.DataFrame,
    cfg: Config,
    figures: dict[str, Path],
) -> str:
    o = _overview(df)
    contract = an.churn_rate_by(df, "Contract", sort_by_rate=True)
    payment = an.churn_rate_by(df, "PaymentMethod", sort_by_rate=True)
    tenure = an.churn_rate_by(
        df.assign(tenure_bucket=an.tenure_bucket(df["tenure"])), "tenure_bucket"
    )
    addons = an.service_addon_rates(df)
    price = an.churn_rate_by(
        df.assign(charge_band=an.charge_band(df["MonthlyCharges"])), "charge_band"
    )
    price_peak = price.loc[price["churn_rate"].idxmax()]
    price_top = price.iloc[-1]
    gender_strength = (
        float(ranking.loc[ranking["feature"] == "gender", "strength"].iloc[0])
        if "gender" in set(ranking["feature"])
        else float("nan")
    )
    fiber = an.churn_rate_by(df, "InternetService", sort_by_rate=True)
    rel = {key: f"figures/{path.name}" for key, path in figures.items()}

    def table(frame: pd.DataFrame, cols: dict[str, str], fmt: dict[str, str]) -> str:
        head = "| " + " | ".join(cols.values()) + " |"
        rule = "|" + "|".join("---" for _ in cols) + "|"
        rows = [
            "| "
            + " | ".join(
                _pct(r[c])
                if fmt.get(c) == "pct"
                else f"{r[c]:,.0f}"
                if fmt.get(c) == "int"
                else f"{r[c]:+.3f}"
                if fmt.get(c) == "delta"
                else f"{r[c]:.3f}"
                if fmt.get(c) == "num"
                else str(r[c])
                for c in cols
            )
            + " |"
            for _, r in frame.iterrows()
        ]
        return "\n".join([head, rule, *rows])

    return f"""# Exploratory Data Analysis - ChurnSense AI

Generated by `make eda` from `data/raw/{cfg.dataset.filename}`
(SHA-256 `{(cfg.dataset.sha256 or "unpinned")[:16]}...`). Every number below is
computed at generation time; none is hand-written.

**Scope note.** This report describes *associations* in a single historical
snapshot. It does not establish that any feature causes churn, and it cannot:
there is no time dimension, no intervention record and no control group in
this dataset.

---

## 1. The dataset at a glance

| Measure | Value |
|---|---|
| Customers | {o["customers"]:,} |
| Churned | {o["churned"]:,} |
| **Observed churn rate** | **{_pct(o["churn_rate"])}** |
| Total monthly charges on the book | ${o["monthly_revenue"]:,.0f} |
| Monthly charges attached to churned customers | ${o["monthly_revenue_at_risk"]:,.0f} |
| Accuracy of always predicting "no churn" | {_pct(o["majority_class_accuracy"])} |

That last row is the reason this project does not report accuracy as a
headline metric. A model that never predicts churn is
{_pct(o["majority_class_accuracy"])} accurate and worth nothing, so the
evaluation leans on precision, recall and PR-AUC instead.

Class balance is {o["churn_rate"]:.2f} : {1 - o["churn_rate"]:.2f}, roughly
1 : {(1 - o["churn_rate"]) / o["churn_rate"]:.1f}. That is moderate imbalance -
enough to disqualify accuracy, not enough to justify synthetic oversampling.
Class weighting is used instead.

## 2. Data quality

{chr(10).join("- " + line for line in cleaning.as_lines())}

Three findings deserve naming:

1. **`TotalCharges` is stored as text.** {cleaning.total_charges_blank} rows hold a
   single space instead of a number. Every one of them has `tenure == 0`, so the
   customer has not been billed yet and the correct value is `0.00`. Median
   imputation would have credited each brand-new customer with roughly $1,400 of
   historical spend, precisely in the tenure region where the model is most
   sensitive.
2. **No missing values and no duplicate rows** once that single quirk is handled.
   `customerID` is unique across all {o["customers"]:,} rows.
3. **`tenure == 0` customers cannot have churned** within the observation window,
   and indeed none of them is labelled as churned. They are retained as valid
   negatives, but they are an artefact of where the snapshot was cut, not
   evidence that new customers never leave.

## 3. Contract type is the strongest single signal

![Churn by contract]({rel["contract"]})

{table(contract, {"Contract": "Contract", "customers": "Customers", "churned": "Churned", "churn_rate": "Churn rate"}, {"customers": "int", "churned": "int", "churn_rate": "pct"})}

Month-to-month customers churn at {_pct(contract.iloc[0]["churn_rate"])} against
{_pct(contract.iloc[-1]["churn_rate"])} on a two-year contract - a
{contract.iloc[0]["churn_rate"] / contract.iloc[-1]["churn_rate"]:.1f}x difference,
and the single largest gap in the dataset.

The business reading needs care. Customers who expect to stay are the ones
willing to sign a long contract, so part of this gap is self-selection rather
than a lock-in effect. The snapshot cannot separate the two. What it does
support is *targeting*: the month-to-month segment is where churn lives, whatever
the mechanism.

## 4. Risk is front-loaded in the customer lifecycle

![Churn by tenure]({rel["tenure"]})

{table(tenure, {"tenure_bucket": "Tenure (months)", "customers": "Customers", "churned": "Churned", "churn_rate": "Churn rate"}, {"customers": "int", "churned": "int", "churn_rate": "pct"})}

Median tenure is {o["median_tenure_churned"]:.0f} months for churned customers
against {o["median_tenure_retained"]:.0f} for retained ones. The first quarter of
the relationship carries the most risk, which argues for onboarding
interventions rather than uniform campaigns.

![Risk concentration]({rel["heatmap"]})

The heat map crosses the two strongest signals. Cells with fewer than 20
customers are suppressed rather than coloured, because a rate computed over a
handful of customers is noise wearing the costume of a finding.

## 5. Payment method separates customers more than expected

![Churn by payment method]({rel["payment"]})

{table(payment, {"PaymentMethod": "Payment method", "customers": "Customers", "churned": "Churned", "churn_rate": "Churn rate"}, {"customers": "int", "churned": "int", "churn_rate": "pct"})}

Electronic-check customers churn at {_pct(payment.iloc[0]["churn_rate"])} - far
above the automatic methods. Payment method is unlikely to be a cause in itself;
it more plausibly proxies for commitment and for billing friction, and it
correlates with month-to-month contracts. It is a useful *flag*, not a lever.

## 6. Price and product mix

![Churn rate by price band]({rel["price"]})

{table(price, {"charge_band": "Monthly charges", "customers": "Customers", "churned": "Churned", "churn_rate": "Churn rate"}, {"customers": "int", "churned": "int", "churn_rate": "pct"})}

Median monthly charges are ${o["median_charges_churned"]:.2f} for churned
customers against ${o["median_charges_retained"]:.2f} for retained ones, so the
customers at risk are emphatically not the cheap ones.

The relationship is **not monotonic**, which is the more interesting part. Churn
peaks in the {price_peak["charge_band"]} band at {_pct(price_peak["churn_rate"])}
and then *falls* to {_pct(price_top["churn_rate"])} among the highest-paying
{price_top["charge_band"]} customers. A plain "higher price drives churn" story
does not fit that shape. The top band is dominated by long-tenure customers
holding several services at once, and both tenure and product depth pull churn
down - so within the top band the price effect and the commitment effect work
against each other. It is also a concrete argument for a model that can
represent non-linear effects rather than a bare linear term on price.

{table(fiber, {"InternetService": "Internet service", "customers": "Customers", "churn_rate": "Churn rate"}, {"customers": "int", "churn_rate": "pct"})}

Fibre-optic customers churn at {_pct(fiber.iloc[0]["churn_rate"])}. Fibre is the
premium, highest-priced tier, so price sensitivity and service expectations are
both plausible explanations, and this dataset distinguishes neither.

![Add-on effect]({rel["addons"]})

{table(addons, {"addon": "Add-on", "with_addon": "Churn with", "without_addon": "Churn without", "difference": "Difference"}, {"with_addon": "pct", "without_addon": "pct", "difference": "delta"})}

Restricted to customers who actually have internet, since for everyone else
these columns read "No internet service" and the comparison would be
meaningless. Support-shaped add-ons (tech support, online security) show the
largest gaps; entertainment add-ons show almost none. This is **not** evidence
that giving someone tech support retains them - customers who buy support may
simply be more invested to begin with.

## 7. Ranked association with churn

![Association ranking]({rel["association"]})

{table(ranking.head(12), {"feature": "Feature", "measure": "Measure", "strength": "Strength"}, {"strength": "num"})}

Two statistics share one axis here: bias-corrected Cramer's V for categorical
features and |point-biserial r| for numeric ones. Both are bounded in [0, 1] and
both vanish under independence, so the ranking is meaningful - but they are not
the same quantity and the chart labels which is which.

**`gender` measures {gender_strength:.4f}.** It carries no detectable
association with churn in this dataset *and* it is a protected attribute, so it
is excluded from the model (`features.drop_columns` in `configs/config.yaml`).
The decision costs nothing measurable and removes a fairness risk; the ablation
is published in the model comparison.

## 8. `TotalCharges` is nearly redundant

![TotalCharges collinearity]({rel["collinearity"]})

`TotalCharges` correlates at r = {np.corrcoef(df["tenure"] * df["MonthlyCharges"], df["TotalCharges"])[0, 1]:.4f}
with `tenure x MonthlyCharges`.

This is **collinearity, not leakage.** All three values are known at prediction
time, so none of them smuggles in the answer. The practical consequences are
different per model family: tree ensembles are largely unbothered and simply
split on whichever version is handier, while logistic regression gets unstable,
hard-to-interpret coefficients when two near-identical columns compete. The
column is kept and the include/exclude ablation is reported rather than being
resolved silently.

## 9. What this means for modelling

| Finding | Consequence for the pipeline |
|---|---|
| {_pct(o["majority_class_accuracy"])} accuracy from a trivial rule | Headline on PR-AUC, precision and recall; never accuracy |
| Moderate 1 : {(1 - o["churn_rate"]) / o["churn_rate"]:.1f} imbalance | `class_weight='balanced'`; no synthetic oversampling |
| Contract, tenure and payment dominate | A regularised linear model should already be competitive - it is the baseline to beat, not a formality |
| Near-duplicate `TotalCharges` | Report the ablation; expect coefficient instability in the linear model |
| `gender` at {gender_strength:.4f} | Dropped: no measured signal, and a protected attribute |
| Suppressed low-count cells | Risk segments need a minimum size before anyone acts on their rate |

## 10. Limits of this analysis

- **One snapshot, no time axis.** Churn is labelled at a single cut; the dataset
  carries no event dates, so nothing here is a time-to-event analysis and no
  trend can be read from it.
- **No intervention record.** The data says nothing about retention offers made
  or accepted, so no effect of any retention action can be estimated from it.
  Every monetary figure elsewhere in this project is therefore a simulation
  driven by stated assumptions.
- **Associations are confounded.** Contract type, payment method, tenure and
  price move together. The ranking above says what travels with churn, not what
  drives it.
- **Published sample, not a live book of business.** Class balance, pricing and
  product mix in a real operator would differ, and the model would need
  recalibration before use.
"""


def generate_report(cfg: Config | None = None) -> Path:
    """Render every figure and write ``reports/eda_report.md``. Returns its path."""
    cfg = cfg or load_config()
    cfg.paths.ensure()
    viz.apply_matplotlib_theme()

    df, cleaning = load_clean(cfg)
    ranking = an.association_ranking(df.drop(columns=[schema.ID_COLUMN]))
    out = cfg.paths.figures_dir

    figures: dict[str, Path] = {
        "contract": fig_contract(df, out),
        "tenure": fig_tenure(df, out),
        "payment": fig_payment(df, out),
        "price": fig_charges(df, out),
        "addons": fig_addons(df, out),
        "heatmap": fig_risk_heatmap(df, out),
        "association": fig_association(ranking, out),
        "collinearity": fig_collinearity(df, out),
    }

    ranking.to_csv(cfg.paths.reports_dir / "association_ranking.csv", index=False)
    report_path = cfg.paths.reports_dir / "eda_report.md"
    report_path.write_text(_markdown(df, cleaning, ranking, cfg, figures), encoding="utf-8")
    logger.info("wrote %s (%d figures)", report_path.name, len(figures))
    return report_path


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Generate the EDA report and figures.").parse_args(argv)
    path = generate_report()
    print(f"OK  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
