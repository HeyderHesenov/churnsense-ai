"""Which accounts need attention, and why: transparent, deterministic rules.

**Rules, not a model.** Every assessment says ``method = "rules"`` and the
``METHOD_VERSION`` of the thresholds it used. Nothing here was trained or fitted,
nothing is a probability, and no assessment carries one. The thresholds
(``RULES``) are initial operational heuristics - not learned from, or validated
against, real churn outcomes - and an indicator says what was observed, never
that it causes churn.

**Who is assessed.** Only an account with a paid relationship to keep:
``active`` and ``past_due`` (``ASSESSED_STATUSES``). A ``trialing``, ``paused`` or
``canceled`` account is *not assessed* and says why; it is never reported as
an account with no signals.

**The rules** (thresholds in ``RULES``; every comparison is in whole numbers, so
no rounding decides a level):

1. ``payment_past_due`` - status ``past_due``: attention.
2. ``renewal_due`` - days from ``as_of_date`` to ``renewal_date``: 0-30 attention,
   31-60 watch, later nothing.
3. ``inactivity`` - days from ``last_activity_at`` to ``as_of_date``: 30 or more
   attention, 14-29 watch, fewer nothing.
4. ``sessions_decline`` and ``key_actions_decline`` - the 30-day count against
   the preceding 30-day period, read from the latest earlier snapshot that is
   30 to 45 days older (``trend_min_gap_days``, ``trend_max_gap_days``): adjacent
   windows that do not overlap. A fall of 30% or more is attention, 15% to under
   30% watch. No earlier snapshot in that range, a count not given, or an
   earlier count of zero: no indicator, and the reason is stated.
5. ``low_active_user_coverage`` - ``active_users / seats_purchased`` where seats
   are above zero: under 10% attention, 10% to under 25% watch. This is
   active-user coverage of purchased seats, not verified seat utilisation.

Any attention indicator makes the account ``attention``; otherwise any watch
indicator makes it ``watch``; otherwise it is ``no_signals``.

**Never the future.** ``assess`` reads the snapshot it assesses and earlier
snapshots of the same account only, and refuses anything else.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from churnsense.workspace.snapshot import Snapshot

METHOD: Final = "rules"
METHOD_VERSION: Final = "workbench_rules.v1"

Severity = Literal["attention", "watch"]
Level = Literal["attention", "watch", "no_signals"]
IndicatorCode = Literal[
    "payment_past_due",
    "renewal_due",
    "inactivity",
    "sessions_decline",
    "key_actions_decline",
    "low_active_user_coverage",
]
#: The rules in the order an account's indicators are listed, within a severity.
RULE_ORDER: Final[tuple[str, ...]] = (
    "payment_past_due",
    "renewal_due",
    "inactivity",
    "sessions_decline",
    "key_actions_decline",
    "low_active_user_coverage",
)
LEVELS: Final[tuple[str, ...]] = ("attention", "watch", "no_signals")
ASSESSED_STATUSES: Final = frozenset({"active", "past_due"})
NOT_ASSESSED_REASONS: Final[dict[str, str]] = {
    "trialing": "Trialing: the account has no paid subscription yet, so these retention rules "
    "do not apply.",
    "paused": "Paused: the account has no active paid subscription, so these retention rules "
    "do not apply.",
    "canceled": "Canceled: the account's last paid subscription has ended, so these retention "
    "rules do not apply.",
}
#: Usage counts the decline rule compares, with the words a reader sees.
TREND_METRICS: Final[dict[str, str]] = {
    "sessions_30d": "sessions",
    "key_actions_30d": "key product actions",
}
_TREND_CODES: Final[dict[str, IndicatorCode]] = {
    "sessions_30d": "sessions_decline",
    "key_actions_30d": "key_actions_decline",
}


@dataclass(frozen=True, slots=True)
class Rules:
    """Every threshold, in one place. Percentages are whole numbers of percent."""

    renewal_attention_days: int = 30
    renewal_watch_days: int = 60
    inactivity_watch_days: int = 14
    inactivity_attention_days: int = 30
    decline_watch_percent: int = 15
    decline_attention_percent: int = 30
    coverage_attention_below_percent: int = 10
    coverage_watch_below_percent: int = 25
    trend_min_gap_days: int = 30
    trend_max_gap_days: int = 45

    def text(self, code: str) -> str:
        """One rule in words, from these thresholds: the only text that states them."""
        match code:
            case "payment_past_due":
                return "Attention when the account's status is past_due."
            case "renewal_due":
                return (
                    "Attention when the renewal is 0 to "
                    f"{self.renewal_attention_days} days away; watch at "
                    f"{self.renewal_attention_days + 1} to {self.renewal_watch_days} days."
                )
            case "inactivity":
                return (
                    "Attention when no user has been active for "
                    f"{self.inactivity_attention_days} days or more; watch at "
                    f"{self.inactivity_watch_days} to {self.inactivity_attention_days - 1} days."
                )
            case "sessions_decline" | "key_actions_decline":
                words = "sessions" if code == "sessions_decline" else "key product actions"
                return (
                    f"30-day {words} against the preceding 30-day period (a snapshot "
                    f"{self.trend_min_gap_days} to {self.trend_max_gap_days} days earlier): "
                    f"attention at a fall of {self.decline_attention_percent}% or more; watch "
                    f"from {self.decline_watch_percent}% to under "
                    f"{self.decline_attention_percent}%."
                )
            case "low_active_user_coverage":
                return (
                    "Active users as a share of purchased seats: attention below "
                    f"{self.coverage_attention_below_percent}%; watch from "
                    f"{self.coverage_attention_below_percent}% to below "
                    f"{self.coverage_watch_below_percent}%."
                )
        raise ValueError(f"no rule {code!r}")

    def describe(self) -> dict[str, str]:
        """Every rule in words, in ``RULE_ORDER``."""
        return {code: self.text(code) for code in RULE_ORDER}


RULES: Final = Rules()


@dataclass(frozen=True, slots=True)
class Evidence:
    """One value a rule read, from the snapshot of ``as_of_date``."""

    field: str
    as_of_date: date
    value: int | date | str


@dataclass(frozen=True, slots=True)
class Indicator:
    code: IndicatorCode
    severity: Severity
    title: str
    explanation: str
    #: The number the rule compared, in ``unit``; a percentage is cut to one decimal
    #: place for reading, never rounded up, so it shows on the side of the threshold
    #: the exact comparison put it.
    observed: Decimal | None
    unit: Literal["days", "percent"] | None
    rule: str
    observed_on: date
    compared_with: date | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class SkippedRule:
    """A rule that could not be applied to the snapshot, and why."""

    rule: IndicatorCode
    reason: str


@dataclass(frozen=True, slots=True)
class Assessment:
    account_id: str
    as_of_date: date
    status: str
    assessed: bool
    level: Level | None
    not_assessed_reason: str | None
    indicators: tuple[Indicator, ...]
    skipped: tuple[SkippedRule, ...]
    #: The earlier snapshot the usage counts were compared with, if any.
    trend_baseline: date | None
    method: str = METHOD
    method_version: str = METHOD_VERSION


def _percent(numerator: int, denominator: int) -> Decimal:
    """``numerator / denominator`` as a percentage, cut (never rounded up) to one decimal
    place, in whole-number arithmetic."""
    return Decimal(numerator * 1000 // denominator).scaleb(-1)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def _payment(s: Snapshot, rules: Rules) -> Indicator | None:
    if s.status != "past_due":
        return None
    return Indicator(
        code="payment_past_due",
        severity="attention",
        title="Payment past due",
        explanation="At least one paid subscription has an overdue payment.",
        observed=None,
        unit=None,
        rule=rules.text("payment_past_due"),
        observed_on=s.as_of_date,
        compared_with=None,
        evidence=(Evidence("status", s.as_of_date, s.status),),
    )


def _renewal(s: Snapshot, rules: Rules, skipped: list[SkippedRule]) -> Indicator | None:
    if s.renewal_date is None:
        skipped.append(SkippedRule("renewal_due", "No renewal date is given."))
        return None
    days = (s.renewal_date - s.as_of_date).days
    if days < 0:
        skipped.append(SkippedRule("renewal_due", "The renewal date is before the snapshot date."))
        return None
    if days <= rules.renewal_attention_days:
        severity: Severity = "attention"
    elif days <= rules.renewal_watch_days:
        severity = "watch"
    else:
        return None
    when = "today" if days == 0 else f"in {_plural(days, 'day')}"
    return Indicator(
        code="renewal_due",
        severity=severity,
        title="Renewal approaching",
        explanation=f"The next renewal is {when}, on {s.renewal_date.isoformat()}.",
        observed=Decimal(days),
        unit="days",
        rule=rules.text("renewal_due"),
        observed_on=s.as_of_date,
        compared_with=None,
        evidence=(Evidence("renewal_date", s.as_of_date, s.renewal_date),),
    )


def _inactivity(s: Snapshot, rules: Rules, skipped: list[SkippedRule]) -> Indicator | None:
    if s.last_activity_at is None:
        skipped.append(SkippedRule("inactivity", "No last activity date is given."))
        return None
    days = (s.as_of_date - s.last_activity_at).days
    if days < 0:
        skipped.append(
            SkippedRule("inactivity", "The last activity date is after the snapshot date.")
        )
        return None
    if days >= rules.inactivity_attention_days:
        severity: Severity = "attention"
    elif days >= rules.inactivity_watch_days:
        severity = "watch"
    else:
        return None
    return Indicator(
        code="inactivity",
        severity=severity,
        title="No recent product activity",
        explanation=(
            f"No user has been active for {_plural(days, 'day')}, since "
            f"{s.last_activity_at.isoformat()}."
        ),
        observed=Decimal(days),
        unit="days",
        rule=rules.text("inactivity"),
        observed_on=s.as_of_date,
        compared_with=None,
        evidence=(Evidence("last_activity_at", s.as_of_date, s.last_activity_at),),
    )


def trend_baseline(current: Snapshot, earlier: Iterable[Snapshot], rules: Rules) -> Snapshot | None:
    """The latest earlier snapshot whose 30-day window is the preceding period, if any."""
    candidates = [
        s
        for s in earlier
        if rules.trend_min_gap_days
        <= (current.as_of_date - s.as_of_date).days
        <= rules.trend_max_gap_days
    ]
    return max(candidates, key=lambda s: s.as_of_date, default=None)


def _decline(
    metric: str,
    current: Snapshot,
    baseline: Snapshot,
    rules: Rules,
    skipped: list[SkippedRule],
) -> Indicator | None:
    code = _TREND_CODES[metric]
    words = TREND_METRICS[metric]
    now, before = getattr(current, metric), getattr(baseline, metric)
    if now is None or before is None:
        skipped.append(
            SkippedRule(code, f"30-day {words} are not given for both periods, so no trend.")
        )
        return None
    if before == 0:
        skipped.append(
            SkippedRule(
                code,
                f"The preceding period had no {words}, so a decline cannot be measured.",
            )
        )
        return None
    fall = before - now
    # Whole numbers only: fall / before >= p% exactly when 100 * fall >= p * before.
    if 100 * fall >= rules.decline_attention_percent * before:
        severity: Severity = "attention"
    elif 100 * fall >= rules.decline_watch_percent * before:
        severity = "watch"
    else:
        return None
    observed = _percent(fall, before)
    return Indicator(
        code=code,
        severity=severity,
        title=f"Fewer {words}",
        explanation=(
            f"30-day {words} fell from {before:,} to {now:,} ({observed}% lower) against the "
            f"30 days before {baseline.as_of_date.isoformat()}."
        ),
        observed=observed,
        unit="percent",
        rule=rules.text(code),
        observed_on=current.as_of_date,
        compared_with=baseline.as_of_date,
        evidence=(
            Evidence(metric, baseline.as_of_date, before),
            Evidence(metric, current.as_of_date, now),
        ),
    )


def _coverage(s: Snapshot, rules: Rules, skipped: list[SkippedRule]) -> Indicator | None:
    if not s.seats_purchased or s.active_users is None:
        skipped.append(
            SkippedRule(
                "low_active_user_coverage",
                "Active users and a number of purchased seats above zero are not both given.",
            )
        )
        return None
    users, seats = s.active_users, s.seats_purchased
    if 100 * users < rules.coverage_attention_below_percent * seats:
        severity: Severity = "attention"
    elif 100 * users < rules.coverage_watch_below_percent * seats:
        severity = "watch"
    else:
        return None
    observed = _percent(users, seats)
    return Indicator(
        code="low_active_user_coverage",
        severity=severity,
        title="Low active-user coverage",
        explanation=(
            f"{_plural(users, 'active user')} for {_plural(seats, 'purchased seat')}: "
            f"{observed}% coverage."
        ),
        observed=observed,
        unit="percent",
        rule=rules.text("low_active_user_coverage"),
        observed_on=s.as_of_date,
        compared_with=None,
        evidence=(
            Evidence("active_users", s.as_of_date, users),
            Evidence("seats_purchased", s.as_of_date, seats),
        ),
    )


def _ordered(indicators: Iterable[Indicator]) -> tuple[Indicator, ...]:
    return tuple(
        sorted(
            indicators,
            key=lambda i: (i.severity != "attention", RULE_ORDER.index(i.code)),
        )
    )


def assess(current: Snapshot, earlier: Sequence[Snapshot] = (), rules: Rules = RULES) -> Assessment:
    """The rules applied to ``current``, reading ``earlier`` snapshots for usage trends only.

    Every snapshot in ``earlier`` must be the same account's, dated before ``current``:
    a later one would let the future decide the past, and is refused.
    """
    for s in earlier:
        if s.account_external_id != current.account_external_id:
            raise ValueError("an earlier snapshot belongs to another account")
        if s.as_of_date >= current.as_of_date:
            raise ValueError("an earlier snapshot is not dated before the one assessed")

    if current.status not in ASSESSED_STATUSES:
        return Assessment(
            account_id=current.account_external_id,
            as_of_date=current.as_of_date,
            status=current.status,
            assessed=False,
            level=None,
            not_assessed_reason=NOT_ASSESSED_REASONS[current.status],
            indicators=(),
            skipped=(),
            trend_baseline=None,
        )

    skipped: list[SkippedRule] = []
    found = [
        _payment(current, rules),
        _renewal(current, rules, skipped),
        _inactivity(current, rules, skipped),
    ]
    baseline = trend_baseline(current, earlier, rules)
    if baseline is None:
        for metric in TREND_METRICS:
            skipped.append(
                SkippedRule(
                    _TREND_CODES[metric],
                    f"No earlier snapshot {rules.trend_min_gap_days} to "
                    f"{rules.trend_max_gap_days} days before this one, so there is no "
                    "preceding 30-day period to compare.",
                )
            )
    else:
        found += [_decline(m, current, baseline, rules, skipped) for m in TREND_METRICS]
    found.append(_coverage(current, rules, skipped))

    indicators = _ordered(i for i in found if i is not None)
    if any(i.severity == "attention" for i in indicators):
        level: Level = "attention"
    elif indicators:
        level = "watch"
    else:
        level = "no_signals"
    return Assessment(
        account_id=current.account_external_id,
        as_of_date=current.as_of_date,
        status=current.status,
        assessed=True,
        level=level,
        not_assessed_reason=None,
        indicators=indicators,
        skipped=tuple(skipped),
        trend_baseline=baseline.as_of_date if baseline else None,
    )


def assess_history(snapshots: Sequence[Snapshot], rules: Rules = RULES) -> tuple[Assessment, ...]:
    """Each snapshot assessed with only the snapshots dated before it, oldest first."""
    ordered = sorted(snapshots, key=lambda s: s.as_of_date)
    return tuple(assess(s, ordered[:i], rules) for i, s in enumerate(ordered))


def days_to_renewal(s: Snapshot) -> int | None:
    """Days from the snapshot to its renewal, for an assessed account with a valid one."""
    if s.status not in ASSESSED_STATUSES or s.renewal_date is None:
        return None
    days = (s.renewal_date - s.as_of_date).days
    return days if days >= 0 else None
