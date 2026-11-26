"""Logo retention, GRR and NRR from recorded outcomes: pure, exact, per currency.

**One window, one starting cohort.** ``retention_metrics`` measures the outcomes
recorded for one measurement window. Its starting cohort is the accounts paying
on the window's first day (starting MRR above zero); a record for another window,
or with zero starting MRR (a customer acquired later), is counted and left out.

**Maturity is judged on ``as_of``, passed in.** An outcome counts only when its
window has ended by ``as_of`` and it was observed by then. Nothing here reads a
clock, a database or a file.

**The metrics** (``DEFINITIONS``), over the matured accounts with an observed
outcome, retained or churned:

* Logo retention = retained / (retained + churned) x 100.
* GRR = sum of min(starting MRR, ending MRR) / sum of starting MRR x 100. A
  contraction counts, an expansion does not, so GRR never exceeds 100.
* NRR = sum of ending MRR / sum of starting MRR x 100: expansion included, so it
  may exceed 100.

A churned account (ending MRR exactly zero) stays in every denominator. An
``unknown`` outcome is in no numerator and no denominator - never read as
churned, retained or zero MRR - and is reported, with its starting MRR, in the
coverage. GRR and NRR are computed per currency only; no amount crosses one.

**Rounding.** Every sum is exact (``validate.MONEY`` raises rather than round).
Each percentage is the exact ratio cut toward zero to one decimal place
(``percent``), as M2 shows its percentages; the exact numerator and denominator
are always reported beside it. With no denominator there is no percentage - never
a 0% - and the reason is stated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal

from churnsense.workspace import outcome_schemas as out
from churnsense.workspace.outcome_schemas import AccountOutcome, check_unique_outcomes
from churnsense.workspace.validate import MONEY

METRICS_VERSION: Final = "retention_metrics.v1"

Standing = Literal[
    "not_in_starting_cohort", "immature", "not_yet_observed", "retained", "churned", "unknown"
]

DEFINITIONS: Final[dict[str, str]] = {
    "starting_cohort": "Accounts whose outcome record is for this window and whose starting MRR "
    "on the window's first day is above zero. Customers acquired later are not in it.",
    "matured": "The window ended on or before as_of and the outcome was observed on or before "
    "as_of.",
    "logo_retention": "Retained accounts / (retained + churned accounts) x 100, over matured "
    "starting-cohort accounts. Unknown outcomes are in neither count.",
    "grr": "Sum of min(starting MRR, ending MRR) / sum of starting MRR x 100, per currency, over "
    "matured starting-cohort accounts with an observed outcome. Expansion is excluded, so it "
    "cannot exceed 100.",
    "nrr": "Sum of ending MRR / sum of starting MRR x 100, per currency, over the same accounts "
    "as GRR. Expansion and contraction are included, so it may exceed 100.",
    "coverage": "Observed outcomes (retained or churned) / matured accounts x 100: how much of "
    "the matured cohort has a known outcome.",
    "rounding": "Percentages are the exact ratio cut toward zero to one decimal place; the exact "
    "numerator and denominator are reported with each.",
}
NOTE_RECORDED: Final = (
    "Every outcome is a recorded fact from the source named in its provenance. No outcome is "
    "inferred from a status, a risk signal or a task."
)
NOTE_UNKNOWN: Final = (
    "Unknown outcomes and outcomes not yet observed are reported, never counted as churned, "
    "retained or zero MRR."
)
NOTE_CURRENCY: Final = (
    "GRR and NRR are computed within each currency; amounts in different currencies are never "
    "added together."
)
NOTE_DESCRIPTIVE: Final = (
    "These metrics describe what was observed. They do not show why accounts stayed or left."
)


def percent(numerator: int | Decimal, denominator: int | Decimal) -> Decimal:
    """``numerator / denominator x 100`` from the exact ratio, cut toward zero to 0.1."""
    if denominator == 0:
        raise ZeroDivisionError("no percentage without a denominator")
    tenths = int(Fraction(numerator) * 1000 / Fraction(denominator))  # int() cuts toward zero
    return Decimal(tenths).scaleb(-1)


def count_ratio(numerator: int, denominator: int, reason: str) -> out.CountRatio:
    """A share of accounts; ``reason`` is used only when the denominator is zero."""
    if denominator == 0:
        return out.CountRatio(numerator=0, denominator=0, percent=None, unavailable_reason=reason)
    return out.CountRatio(
        numerator=numerator,
        denominator=denominator,
        percent=percent(numerator, denominator),
        unavailable_reason=None,
    )


def amount_ratio(
    numerator: Decimal, denominator: Decimal, reason: str, *, can_exceed_100: bool
) -> out.AmountRatio:
    if denominator == 0:
        return out.AmountRatio(
            numerator=Decimal(0),
            denominator=Decimal(0),
            percent=None,
            unavailable_reason=reason,
            can_exceed_100=can_exceed_100,
        )
    return out.AmountRatio(
        numerator=numerator,
        denominator=denominator,
        percent=percent(numerator, denominator),
        unavailable_reason=None,
        can_exceed_100=can_exceed_100,
    )


def standing(outcome: AccountOutcome, as_of: date) -> Standing:
    """Where one outcome record stands as of ``as_of``: the only place maturity is decided."""
    if outcome.starting_mrr == 0:
        return "not_in_starting_cohort"
    if outcome.window_end > as_of:
        return "immature"
    if outcome.observed_on > as_of:
        return "not_yet_observed"
    return outcome.status


def _total(amounts: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for amount in amounts:
        total = MONEY.add(total, amount)
    return total


def _no_outcome_reason(window: out.MeasurementWindow, as_of: date) -> str:
    if window.end > as_of:
        return (
            f"The window ends on {window.end.isoformat()}, after as_of "
            f"({as_of.isoformat()}): its outcomes are not mature."
        )
    return "No matured starting-cohort account in this window has an observed outcome."


def outcome_counts(standings: Sequence[Standing], reason: str) -> out.OutcomeCounts:
    """The counts of starting-cohort standings (``not_in_starting_cohort`` must be removed)."""
    n = {s: standings.count(s) for s in ("immature", "not_yet_observed", "retained", "churned")}
    unknown = standings.count("unknown")
    matured = n["retained"] + n["churned"] + unknown
    observed = n["retained"] + n["churned"]
    return out.OutcomeCounts(
        eligible=len(standings),
        immature=n["immature"],
        not_yet_observed=n["not_yet_observed"],
        matured=matured,
        retained=n["retained"],
        churned=n["churned"],
        unknown=unknown,
        observed=observed,
        coverage=count_ratio(observed, matured, reason),
    )


def _revenue(
    currency: str, cohort: Sequence[tuple[AccountOutcome, Standing]], reason: str
) -> out.CurrencyRevenueRetention:
    matured = [o for o, s in cohort if s in ("retained", "churned", "unknown")]
    observed = [o for o in matured if o.status != "unknown"]
    starting = _total(o.starting_mrr for o in observed)
    retained = _total(min(o.starting_mrr, o.ending_mrr) for o in observed)  # type: ignore[type-var]
    ending = _total(o.ending_mrr for o in observed)  # type: ignore[misc]
    matured_starting = _total(o.starting_mrr for o in matured)
    return out.CurrencyRevenueRetention(
        currency=currency,
        counts=outcome_counts([s for _, s in cohort], reason),
        matured_starting_mrr=matured_starting,
        unknown_starting_mrr=_total(o.starting_mrr for o in matured if o.status == "unknown"),
        grr=amount_ratio(retained, starting, reason, can_exceed_100=False),
        nrr=amount_ratio(ending, starting, reason, can_exceed_100=True),
        revenue_coverage=amount_ratio(starting, matured_starting, reason, can_exceed_100=False),
    )


def retention_metrics(
    outcomes: Sequence[AccountOutcome], window: out.MeasurementWindow, as_of: date
) -> out.RetentionMetrics:
    """Logo retention, and GRR and NRR per currency, for ``window`` as of ``as_of``.

    Refuses duplicate records (one per account and window). Every record given is
    accounted for in ``coverage``: used, immature, not yet observed, unknown, not in
    the starting cohort, or for another window.
    """
    check_unique_outcomes(outcomes)
    in_window = [
        o for o in outcomes if (o.window_start, o.window_end) == (window.start, window.end)
    ]
    standings = [(o, standing(o, as_of)) for o in in_window]
    cohort = [(o, s) for o, s in standings if s != "not_in_starting_cohort"]
    reason = _no_outcome_reason(window, as_of)
    counts = outcome_counts([s for _, s in cohort], reason)
    by_currency: dict[str, list[tuple[AccountOutcome, Standing]]] = {}
    for o, s in cohort:
        by_currency.setdefault(o.currency, []).append((o, s))
    return out.RetentionMetrics(
        metrics_version=METRICS_VERSION,
        coverage=out.MeasurementCoverage(
            window=window,
            as_of=as_of,
            records=len(outcomes),
            other_window=len(outcomes) - len(in_window),
            not_in_starting_cohort=len(standings) - len(cohort),
            counts=counts,
        ),
        logo_retention=count_ratio(counts.retained, counts.observed, reason),
        revenue=tuple(_revenue(c, by_currency[c], reason) for c in sorted(by_currency)),
        definitions=dict(DEFINITIONS),
        notes=(NOTE_RECORDED, NOTE_UNKNOWN, NOTE_CURRENCY, NOTE_DESCRIPTIVE),
    )
