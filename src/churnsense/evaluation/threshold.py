"""Threshold selection as a business decision, not a modelling default.

0.5 is an arbitrary place to cut a probability. The right cut depends on what
a retention offer costs, how often it works, and what a retained customer is
worth - none of which the model knows. This module makes that trade explicit
and sweeps it.

**Everything monetary here is a simulation.** The dataset records no retention
campaigns and no offer outcomes, so ``offer_success_rate`` in particular cannot
be estimated from it; it is an assumption supplied by the business. The numbers
below answer "what would this be worth *if* these assumptions held", and they
are labelled that way everywhere they surface.

The model:

    value of a retained customer = monthly charges x horizon x gross margin
    retained value  = sum over true positives of (value x offer success rate)
    campaign cost   = every flagged customer x offer cost
    net benefit     = retained value - campaign cost

False negatives cost the business a customer; false positives cost the price of
an offer. Because those costs are wildly asymmetric, the optimum sits well away
from 0.5 for most plausible assumptions - which is the whole point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike

from churnsense.config import Business, Config, load_config

DISCLAIMER = (
    "SIMULATED under stated assumptions - not a measured business result. "
    "The dataset contains no retention-campaign outcomes, so the offer success "
    "rate is an assumption, not an estimate."
)


@dataclass(frozen=True, slots=True)
class ThresholdScenario:
    """One operating point and its simulated economics."""

    threshold: float
    flagged: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    intervention_cost: float
    retained_value: float
    net_benefit: float
    currency: str

    @property
    def disclaimer(self) -> str:
        return DISCLAIMER

    @property
    def return_on_spend(self) -> float | None:
        """Retained value per unit spent. ``None`` when nothing was spent."""
        return self.retained_value / self.intervention_cost if self.intervention_cost else None

    def as_dict(self) -> dict[str, float | int | str]:
        return {**asdict(self), "disclaimer": DISCLAIMER}


def _validate(y_true: ArrayLike, y_proba: ArrayLike, monthly_charges: ArrayLike):
    y = np.asarray(y_true).ravel().astype(int)
    p = np.asarray(y_proba, dtype=float).ravel()
    charges = np.asarray(monthly_charges, dtype=float).ravel()

    if not (y.shape[0] == p.shape[0] == charges.shape[0]):
        raise ValueError(
            "y_true, y_proba and monthly_charges must have the same length, got "
            f"{y.shape[0]}, {p.shape[0]} and {charges.shape[0]}"
        )
    if y.size == 0:
        raise ValueError("cannot sweep thresholds over an empty set")
    if (charges < 0).any():
        raise ValueError("monthly_charges contains negative values")
    return y, p, charges


def sweep_thresholds(
    y_true: ArrayLike,
    y_proba: ArrayLike,
    monthly_charges: ArrayLike,
    business: Business,
    thresholds: ArrayLike | None = None,
) -> pd.DataFrame:
    """Evaluate the operating decision across a grid of thresholds.

    Vectorised over the grid rather than looping per customer: the comparison
    matrix is ``(n_thresholds, n_customers)`` of booleans, which at a 101-point
    grid and a few thousand customers is trivial and keeps the dashboard's
    slider responsive without caching.
    """
    y, p, charges = _validate(y_true, y_proba, monthly_charges)
    grid = np.linspace(0.0, 1.0, 101) if thresholds is None else np.asarray(thresholds, float)

    flags = p[None, :] >= grid[:, None]  # (T, N)
    positives = y.astype(bool)[None, :]

    tp_mask = flags & positives
    tp = tp_mask.sum(axis=1)
    fp = (flags & ~positives).sum(axis=1)
    fn = (~flags & positives).sum(axis=1)
    flagged = tp + fp

    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.where(flagged > 0, tp / np.maximum(flagged, 1), 0.0)
        recall = np.where((tp + fn) > 0, tp / np.maximum(tp + fn, 1), 0.0)
        f1 = np.where(
            (precision + recall) > 0,
            2 * precision * recall / np.maximum(precision + recall, 1e-12),
            0.0,
        )

    # Per-customer value, so a skewed book is not mispriced by an average.
    customer_value = charges * business.expected_horizon_months * business.gross_margin
    retained_value = (tp_mask * customer_value[None, :]).sum(axis=1) * business.offer_success_rate
    intervention_cost = flagged * business.retention_offer_cost

    return pd.DataFrame(
        {
            "threshold": grid,
            "flagged": flagged,
            "flagged_share": flagged / len(y),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "intervention_cost": intervention_cost,
            "retained_value": retained_value,
            "net_benefit": retained_value - intervention_cost,
        }
    )


def recommend_threshold(
    y_true: ArrayLike,
    y_proba: ArrayLike,
    monthly_charges: ArrayLike,
    business: Business,
    thresholds: ArrayLike | None = None,
) -> ThresholdScenario:
    """The best threshold, within an indifference band of one offer's cost.

    Taking the plain argmax reads precision the estimate does not have. On the
    real dataset the sweep peaks at 0.30 with a simulated net benefit of
    $26,579, while 0.36 scores $26,547 - a $32 difference on a figure built
    from an assumed offer-success rate. Treating that as a win commits the
    campaign to contacting 63 more customers at lower precision.

    So operating points within **one retention offer's cost** of the maximum
    are treated as indistinguishable, and the tie breaks toward the highest
    threshold in that band - the one that spends least and disturbs fewest
    customers. The tolerance is the business's own unit of account rather than
    an invented epsilon: a simulation cannot resolve a difference smaller than
    a single intervention, and it scales automatically with the offer.

    This is the same discipline the model selection uses, for the same reason.
    """
    return recommend_threshold_from(
        sweep_thresholds(y_true, y_proba, monthly_charges, business, thresholds), business
    )


def indifference_band(sweep: pd.DataFrame, business: Business) -> pd.DataFrame:
    """Rows of ``sweep`` that are statistically indistinguishable from the best.

    Exposed so the dashboard can shade exactly the region the recommendation
    was drawn from, instead of re-deriving it and risking a caption that
    describes something the chart does not show.
    """
    ceiling = sweep["net_benefit"].max()
    tolerance = max(business.retention_offer_cost, 0.0)
    return sweep[sweep["net_benefit"] >= ceiling - tolerance]


def recommend_threshold_from(sweep: pd.DataFrame, business: Business) -> ThresholdScenario:
    """Pick the operating point from a sweep that has already been computed.

    Separate from ``recommend_threshold`` so a caller holding a sweep - the
    dashboard redraws one on every slider move - does not pay for a second
    identical pass over the same 101 x n comparison matrix.
    """
    band = indifference_band(sweep, business)
    return _scenario_from_row(band.loc[band["threshold"].idxmax()], business)


def _scenario_from_row(row: pd.Series, business: Business) -> ThresholdScenario:
    """Build a scenario from one row of a sweep."""
    return ThresholdScenario(
        threshold=float(row["threshold"]),
        flagged=int(row["flagged"]),
        true_positives=int(row["true_positives"]),
        false_positives=int(row["false_positives"]),
        false_negatives=int(row["false_negatives"]),
        precision=float(row["precision"]),
        recall=float(row["recall"]),
        f1=float(row["f1"]),
        intervention_cost=float(row["intervention_cost"]),
        retained_value=float(row["retained_value"]),
        net_benefit=float(row["net_benefit"]),
        currency=business.currency,
    )


def assign_risk_bands(probabilities: ArrayLike, cfg: Config | None = None) -> pd.Series:
    """Map probabilities to ordered risk-band labels.

    Bands are fixed probability cut points, deliberately independent of the
    decision threshold: the retention team builds workflows around "Critical"
    meaning the same thing week to week, and retuning the threshold must not
    silently redefine its queue.
    """
    cfg = cfg or load_config()
    values = np.asarray(probabilities, dtype=float).ravel()
    names = [band.name for band in cfg.risk_bands]
    edges = [band.min for band in cfg.risk_bands] + [cfg.risk_bands[-1].max]

    labels = pd.cut(values, bins=edges, labels=names, right=False, include_lowest=True)
    return pd.Series(labels).astype(pd.CategoricalDtype(categories=names, ordered=True))


def scenario_at(sweep: pd.DataFrame, threshold: float, business: Business) -> ThresholdScenario:
    """The scenario for the grid point nearest ``threshold``.

    In the library rather than in the dashboard because "find the sweep row
    closest to a threshold" was being written out in two places, each with
    its own `.loc`/`.iloc` subtlety, and because the result is exactly a
    ``ThresholdScenario`` - which already knows how to compute return on
    spend, so the caller does not have to reimplement the divide-by-zero case.
    """
    nearest = (sweep["threshold"] - float(threshold)).abs().idxmin()
    return _scenario_from_row(sweep.loc[nearest], business)
