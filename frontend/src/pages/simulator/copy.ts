/**
 * The simulator's words. Each sentence that carries a figure takes it from
 * the response (or the served bounds) and only writes it; none works one out.
 * Three kinds of figure are kept apart in the words as on the page: the
 * assumptions the reader sets, the counts measured on validation customers,
 * and the money simulated from both.
 */
import type { Sweep, SweepPoint } from '../../api/queries';
import { cut, int, minorDigits, pct, price, signedMoney } from '../../lib/format';
import {
  ADVANCED,
  FIELDS,
  MAX_TEXT,
  type Change,
  type Scenario,
  type Echo,
  type FieldKey,
  type FieldProblem,
  type FieldSpec,
  type Range,
  type Status,
} from './simulator';

export const LEDE =
  'A model that ranks customers well still needs someone to decide where to draw the line. ' +
  'Set what an offer costs and how often you assume it works; the API works out the curve ' +
  'and the recommended cut on the validation partition.';

export const SIMULATED_NOTE = {
  title: 'Simulated, not measured.',
  body:
    'The dataset holds no retention-campaign outcomes, so the success rate is an assumption ' +
    'you supply, not an estimate, and no amount here has been observed. The counts at a cut ' +
    'compare the model’s scores with what validation customers did; the money assumes your inputs.',
};

export const FIELD_COPY: Record<FieldKey, { label: string; noun: string }> = {
  cost: { label: 'Cost of one offer', noun: 'the cost of one offer' },
  success: { label: 'Offer success rate', noun: 'the success rate' },
  horizon: { label: 'Revenue horizon', noun: 'the horizon' },
  margin: { label: 'Gross margin', noun: 'the gross margin' },
};

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A value of ``spec``'s field, in its units: "$12.50", "30%", "12 months". */
export function fieldValue(spec: FieldSpec, value: number, currency: string): string {
  if (spec.unit === 'money') return price(value, currency);
  if (spec.unit === 'percent') return pct(value, 0);
  return `${int(value)} ${value === 1 ? 'month' : 'months'}`;
}

export function rangeText(
  spec: FieldSpec,
  range: Pick<Range, 'min' | 'max'>,
  currency: string,
): string {
  if (spec.unit === 'months') return `${int(range.min)} to ${int(range.max)} months`;
  return `${fieldValue(spec, range.min, currency)} to ${fieldValue(spec, range.max, currency)}`;
}

/** Under a field: what the API accepts, and the configured default. */
export function boundsNote(spec: FieldSpec, range: Range, fallback: number, currency: string) {
  return `${rangeText(spec, range, currency)} · default ${fieldValue(spec, fallback, currency)}`;
}

export function fieldMessage(spec: FieldSpec, problem: FieldProblem, currency: string): string {
  const noun = FIELD_COPY[spec.key].noun;
  switch (problem.reason) {
    case 'required':
      return `Enter ${noun}.`;
    case 'long':
      return `${capital(noun)} is written in at most ${MAX_TEXT} characters.`;
    case 'format':
      return `${capital(noun)} is a number, written in digits with a point or a comma for any decimals.`;
    case 'whole':
      return `${capital(noun)} is a whole number of months.`;
    case 'step': {
      if (spec.unit !== 'money') return `${capital(noun)} is in whole percents.`;
      const digits = minorDigits(currency);
      return digits === 0
        ? `${capital(noun)} is a whole amount.`
        : `${capital(noun)} has at most ${digits} decimal places.`;
    }
    case 'range':
      return `${capital(noun)} is ${rangeText(spec, problem, currency)}.`;
  }
}

/** The assumptions a response was worked out for, from its echo, and on whom. */
export function provenance(sweep: Sweep): string {
  const a = sweep.assumptions;
  return (
    `Figures for a ${price(a.retention_offer_cost, a.currency)} offer, ` +
    `${pct(a.offer_success_rate, 0)} success, a ${int(a.expected_horizon_months)}-month horizon ` +
    `and ${pct(a.gross_margin, 0)} margin, on ${int(sweep.customers)} validation customers ` +
    `(${int(sweep.churners)} churned).`
  );
}

/** What the reader must know about the figures shown, when they are not for what is set. */
export function statusNote(status: Status, shown: boolean): string | null {
  const named = 'the figures below are for the assumptions they name.';
  switch (status) {
    case 'updating':
      return 'Updating. Until the new figures arrive, those below are for the assumptions they name.';
    case 'unsent':
      return shown
        ? `Not sent: correct the marked field. ${capital(named)}`
        : 'Not sent: correct the marked field.';
    case 'mismatch':
      return 'The server answered for other assumptions than those set: the figures below are for the ones they name.';
    default:
      return null;
  }
}

/** The net as shown, with a word for its direction, so the sign is never told by colour alone. */
export function netParts(net: number, currency: string) {
  const amount = signedMoney(net, currency);
  const word = amount.startsWith('+')
    ? 'net gain'
    : amount.startsWith('−')
      ? 'net loss'
      : 'break-even';
  return { amount, word };
}

/** Precision is not defined where nothing is flagged; the API writes it as 0 there. */
export function precisionText(point: Pick<SweepPoint, 'flagged' | 'precision'>): string {
  return point.flagged === 0 ? '—' : point.precision.toFixed(3);
}

/** Recall is not defined without churners; the API writes it as 0 then. */
export function recallText(point: Pick<SweepPoint, 'recall'>, churners: number): string {
  return churners === 0 ? '—' : point.recall.toFixed(3);
}

/** A pinned scenario's assumptions, short: "$50 · 30% · 12 mo · 65%". */
export function pinSummary(a: Echo): string {
  return (
    `${price(a.retention_offer_cost, a.currency)} · ${pct(a.offer_success_rate, 0)} · ` +
    `${int(a.expected_horizon_months)} mo · ${pct(a.gross_margin, 0)}`
  );
}

function flags(count: number): string {
  return count === 0 ? 'no one' : count === 1 ? '1 customer' : `${int(count)} customers`;
}

/** Why the recommendation is not simply the peak, in the response's own figures. */
export function whyNotPeak(sweep: Sweep): string {
  const { peak, recommended: rec } = sweep;
  const currency = sweep.assumptions.currency;
  const offer = price(sweep.assumptions.retention_offer_cost, currency);
  const net = (value: number) => signedMoney(value, currency);
  if (peak.net_benefit <= 0)
    return (
      `No cut pays under these assumptions: the best simulated net is ${net(peak.net_benefit)}, ` +
      `at ${cut(peak.threshold)}. The recommendation, ${cut(rec.threshold)}, flags ${flags(rec.flagged)}.`
    );
  const same = cut(peak.threshold) === cut(rec.threshold);
  if (sweep.assumptions.retention_offer_cost === 0)
    return same
      ? `With an offer that costs nothing there is no tolerance, and the recommendation is the ` +
          `peak: ${cut(rec.threshold)}, at ${net(rec.net_benefit)}.`
      : `With an offer that costs nothing there is no tolerance: the recommendation, ` +
          `${cut(rec.threshold)} (${net(rec.net_benefit)}), is the highest cut whose net equals ` +
          `the peak’s (${cut(peak.threshold)}, ${net(peak.net_benefit)}).`;
  if (same)
    return (
      `The recommendation is the peak: ${cut(rec.threshold)}, at ${net(rec.net_benefit)}. ` +
      `No higher cut comes within one offer (${offer}) of it.`
    );
  return (
    `The peak sits at ${cut(peak.threshold)} (${net(peak.net_benefit)}). The recommendation, ` +
    `${cut(rec.threshold)} (${net(rec.net_benefit)}), is the highest cut whose net is within ` +
    `one offer (${offer}) of it: on a figure built from an assumed success rate, cuts that ` +
    `close are treated as indistinguishable, and the tie goes to the higher cut, which makes ` +
    `fewer offers.`
  );
}

/** How the API's recommended cut moves across the success rates it tried. */
export function sensitivityNote(rows: Sweep['sensitivity']): string | null {
  const first = rows[0];
  const last = rows.at(-1);
  if (!first || !last) return null;
  const cuts = rows.map((row) => cut(row.threshold));
  if (cuts.every((label) => label === cuts[0]))
    return `Across these rates the recommended cut stays at ${cut(first.threshold)}.`;
  if (cut(first.threshold) === cut(last.threshold)) {
    // Labels on the 0.01 grid sort as their numbers do.
    const sorted = [...cuts].sort();
    return `Across these rates the recommended cut moves between ${sorted[0]} and ${sorted.at(-1)}.`;
  }
  return (
    `Across these rates the recommended cut moves from ${cut(first.threshold)} at ` +
    `${pct(first.offer_success_rate, 0)} to ${cut(last.threshold)} at ` +
    `${pct(last.offer_success_rate, 0)}: where to cut is a business decision as much as a model’s.`
  );
}

/** F1 is not defined where nothing is flagged or no one churned; the API writes it as 0 then. */
export function f1Text(point: Pick<SweepPoint, 'flagged' | 'f1'>, churners: number): string {
  return point.flagged === 0 || churners === 0 ? '—' : point.f1.toFixed(3);
}

/** The cut's figures in one line, each the response's own at that cut. */
export function readout(point: SweepPoint, churners: number): string {
  return (
    `At ${cut(point.threshold)}: ${int(point.flagged)} flagged · precision ${precisionText(point)} · ` +
    `recall ${recallText(point, churners)} · F1 ${f1Text(point, churners)}.`
  );
}

export const CUT_CAPTION =
  'Customers scored at or above the cut get an offer. Only the cuts the API sent can be chosen; the labelled tick is the served cut.';

/** Why the cut cannot be chosen yet, by what the figures are. */
export function cutUnavailable(status: Status, malformed: boolean): string {
  if (status === 'withheld') return 'No cut can be chosen while the sweep is withheld.';
  if (status === 'failed') return 'No cut can be chosen: the sweep for these assumptions failed.';
  if (status === 'unsent') return 'No cut can be chosen until every assumption can be sent.';
  if (malformed) return 'No cut can be chosen: the sweep has no point at its own recommendation.';
  return 'The cuts arrive with the sweep.';
}

/** The Advanced controls' summary: the two values behind it, as set. */
export function advancedSummary(scenario: Scenario, currency: string): string {
  return FIELDS.filter((spec) => ADVANCED.includes(spec.key))
    .map((spec) => {
      const value = scenario.readings[spec.key].value;
      return `${FIELD_COPY[spec.key].label} ${value === null ? 'not set' : fieldValue(spec, value, currency)}`;
    })
    .join(' · ');
}

/** One changed field: "Cost of one offer $80 (default $50)". */
export function changeLine(change: Change, currency: string): string {
  const { spec, value, fallback } = change;
  const now = value === null ? 'cannot be sent' : fieldValue(spec, value, currency);
  return `${FIELD_COPY[spec.key].label} ${now} (default ${fieldValue(spec, fallback, currency)})`;
}

export const AT_DEFAULTS = 'Every assumption is at the served default.';

export const SIDE_BY_SIDE =
  'Three cuts of the same response, read side by side: each figure is the API’s own at its cut, and nothing is subtracted from anything.';

export const NOT_A_POINT = 'not one of the cuts the API sent';

export const FORMULA_NOTE =
  'The API works every amount out from the counts at a cut and the assumptions as named; the page writes them and works none out.';
