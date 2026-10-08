"""Descriptive statistics shared by the EDA report and the dashboard.

Pure functions over DataFrames: no plotting, no file IO, no config. Keeping
them here means the dashboard and the written report compute "churn rate by
contract" with the *same* code, so the two can never disagree - and the numbers
are unit-testable without a rendering backend.

Every association measure in this module is a measure of **association**, not
of causal effect. Nothing here licenses a claim that changing a feature would
change churn.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from churnsense.data import schema

# Tenure edges chosen on contract-cycle boundaries (quarter, half-year, year,
# two years, then the rest) rather than equal-width bins, because the business
# reasons about renewal anniversaries, not arbitrary month ranges.
TENURE_BINS: tuple[int, ...] = (-1, 3, 6, 12, 24, 48, 72)
TENURE_LABELS: tuple[str, ...] = ("0-3", "4-6", "7-12", "13-24", "25-48", "49-72")


def tenure_bucket(tenure: pd.Series) -> pd.Series:
    """Bucket tenure in months into ordered, business-meaningful bands."""
    return pd.cut(tenure, bins=list(TENURE_BINS), labels=list(TENURE_LABELS), ordered=True)


# Price bands follow the product structure rather than equal widths: the
# charge distribution is bimodal, with a phone-only cluster near $20 and an
# internet cluster from roughly $70 up.
CHARGE_BINS: tuple[float, ...] = (0.0, 35.0, 55.0, 75.0, 95.0, 1e9)
CHARGE_LABELS: tuple[str, ...] = ("<$35", "$35-55", "$55-75", "$75-95", "$95+")


def charge_band(monthly_charges: pd.Series) -> pd.Series:
    """Bucket monthly charges into ordered, product-meaningful price bands."""
    return pd.cut(
        monthly_charges,
        bins=list(CHARGE_BINS),
        labels=list(CHARGE_LABELS),
        ordered=True,
        right=False,
    )


def churn_rate_by(
    df: pd.DataFrame,
    column: str,
    *,
    target: str = schema.TARGET,
    sort_by_rate: bool = False,
) -> pd.DataFrame:
    """Churn count and rate per level of ``column``.

    Returns columns ``[column, customers, churned, churn_rate]``. The customer
    count travels with the rate on purpose: a 100% churn rate over 3 customers
    is noise, and a rate shown without its denominator invites exactly that
    misreading.
    """
    if column not in df.columns:
        raise KeyError(f"column '{column}' is not in the frame")

    grouped = df.groupby(column, observed=True)[target].agg(["size", "sum"])
    out = grouped.rename(columns={"size": "customers", "sum": "churned"}).reset_index()
    out["churned"] = out["churned"].astype(int)
    out["churn_rate"] = out["churned"] / out["customers"]
    return out.sort_values("churn_rate", ascending=False) if sort_by_rate else out


def numeric_summary_by_churn(
    df: pd.DataFrame, column: str, *, target: str = schema.TARGET
) -> pd.DataFrame:
    """Median, mean, quartiles and count of ``column`` split by churn outcome."""
    out = (
        df.groupby(target, observed=True)[column]
        .agg(
            customers="size",
            mean="mean",
            median="median",
            q25=lambda s: s.quantile(0.25),
            q75=lambda s: s.quantile(0.75),
        )
        .reset_index()
    )
    out[target] = out[target].map({0: "Retained", 1: "Churned"})
    return out.rename(columns={target: "outcome"})


def cramers_v(x: pd.Series, y: pd.Series) -> float:
    """Bias-corrected Cramer's V between two categorical series.

    Bias correction (Bergsma 2013) matters here because the predictors have
    very different cardinalities - 2 levels for ``Partner`` against 4 for
    ``PaymentMethod``. Uncorrected V rewards the wider variable purely for
    having more cells, which would scramble the association ranking.

    Returns 0.0 when the contingency table is degenerate (a constant column).
    """
    table = pd.crosstab(x, y)
    n = table.to_numpy().sum()
    if n == 0 or min(table.shape) < 2:
        return 0.0

    from scipy.stats import chi2_contingency

    chi2 = chi2_contingency(table, correction=False)[0]
    phi2 = chi2 / n
    r, k = table.shape
    # Bergsma's correction: subtract the expected chi-square under independence.
    phi2_corrected = max(0.0, phi2 - (r - 1) * (k - 1) / (n - 1))
    r_corrected = r - (r - 1) ** 2 / (n - 1)
    k_corrected = k - (k - 1) ** 2 / (n - 1)
    denominator = min(r_corrected - 1, k_corrected - 1)
    return float(np.sqrt(phi2_corrected / denominator)) if denominator > 0 else 0.0


def point_biserial(numeric: pd.Series, binary: pd.Series) -> float:
    """Point-biserial correlation between a numeric series and a 0/1 series.

    Mathematically identical to Pearson r for this case; named separately so
    the report can say what it actually measured.
    """
    valid = numeric.notna() & binary.notna()
    if valid.sum() < 3 or numeric[valid].nunique() < 2:
        return 0.0
    return float(np.corrcoef(numeric[valid], binary[valid])[0, 1])


def association_ranking(df: pd.DataFrame, *, target: str = schema.TARGET) -> pd.DataFrame:
    """Rank every feature by strength of association with the target.

    Two different statistics appear in one table, which needs care: Cramer's V
    for categorical features and |point-biserial r| for numeric ones. Both are
    bounded in [0, 1] and both are 0 under independence, so the ranking is
    informative - but they are not the same quantity, so the ``measure`` column
    is reported alongside and the report says so in words.
    """
    rows: list[dict[str, object]] = []
    for col in df.columns:
        if col in {target, schema.ID_COLUMN}:
            continue
        if col in schema.NUMERIC_COLUMNS:
            r = point_biserial(df[col], df[target])
            rows.append(
                {
                    "feature": col,
                    "measure": "point-biserial |r|",
                    "strength": abs(r),
                    "direction": "higher -> more churn" if r > 0 else "higher -> less churn",
                }
            )
        else:
            rows.append(
                {
                    "feature": col,
                    "measure": "Cramer's V",
                    "strength": cramers_v(df[col], df[target]),
                    "direction": "n/a (categorical)",
                }
            )
    return pd.DataFrame(rows).sort_values("strength", ascending=False).reset_index(drop=True)


def risk_concentration(
    df: pd.DataFrame,
    *,
    index: str = "Contract",
    columns: str = "tenure_bucket",
    target: str = schema.TARGET,
    min_cell: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Churn-rate and customer-count matrices for a two-way breakdown.

    Returns ``(rate, counts)``. Cells with fewer than ``min_cell`` customers
    are set to NaN in the rate matrix - a heatmap that colours a 3-customer
    cell the same as a 300-customer cell is actively misleading, and the counts
    matrix is returned so the caller can annotate what was suppressed.
    """
    work = df.copy()
    if columns == "tenure_bucket" and columns not in work.columns:
        work[columns] = tenure_bucket(work["tenure"])

    counts = pd.crosstab(work[index], work[columns])
    rate = pd.crosstab(work[index], work[columns], values=work[target], aggfunc="mean")
    return rate.where(counts >= min_cell), counts


def service_addon_rates(df: pd.DataFrame, *, target: str = schema.TARGET) -> pd.DataFrame:
    """Churn rate among internet subscribers for each optional add-on.

    Restricted to customers who *have* internet: for everyone else the add-on
    columns read "No internet service", and mixing them in would compare a
    product decision against a non-purchase and call the difference an add-on
    effect.
    """
    addons = [
        "OnlineSecurity",
        "OnlineBackup",
        "DeviceProtection",
        "TechSupport",
        "StreamingTV",
        "StreamingMovies",
    ]
    subscribers = df[df["InternetService"].ne("No")]
    rows = [
        {
            "addon": schema.FEATURE_LABELS.get(col, col),
            "with_addon": float(subscribers.loc[subscribers[col].eq("Yes"), target].mean()),
            "without_addon": float(subscribers.loc[subscribers[col].eq("No"), target].mean()),
            "n_with": int(subscribers[col].eq("Yes").sum()),
            "n_without": int(subscribers[col].eq("No").sum()),
        }
        for col in addons
        if col in subscribers.columns
    ]
    out = pd.DataFrame(rows)
    out["difference"] = out["with_addon"] - out["without_addon"]
    return out.sort_values("difference").reset_index(drop=True)
