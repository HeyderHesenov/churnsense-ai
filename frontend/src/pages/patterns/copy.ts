/**
 * The patterns page's words. Every figure is a served one, formatted; the
 * wording describes observed outcomes in one historical snapshot - never a
 * cause, never a prediction - and says what each comparison is against.
 */
import type { Schemas } from '../../api/client';
import type { Cell, Segments } from '../../api/queries';
import { int, pct, times } from '../../lib/format';
import { level } from '../../lib/labels';
import type { Explore, Measure, Scale, View } from './patterns';

export const TITLE = 'Where did churn concentrate, and in which combinations?';

export function lede(minimum: number): string {
  return (
    'Observed churn in the training partition, by one attribute or two crossed. ' +
    'Validation and test outcomes are not read here. No model output appears on this page. ' +
    `A group of fewer than ${minimum} customers shows its size only, and so does any group ` +
    "whose churn would let a smaller group's be worked out."
  );
}

/** Who the figures describe: the training partition, out of the whole book. */
export function population(data: Segments): string {
  const parts = [
    'Training partition',
    `${int(data.partition_customers)} of ${int(data.book_customers)} customers`,
    `${int(data.partition_churned)} churned`,
  ];
  if (data.base_rate !== null) parts.push(`${pct(data.base_rate)} observed churn`);
  return parts.join(' · ');
}

export const FAILED = 'The breakdown could not be loaded.';

export const WITHHELD = {
  title: 'Observed outcomes are withheld',
  body:
    'The server could not verify which customers form the training partition, so it shows ' +
    'no figure that rests on their outcomes.',
};

export const NOTE =
  'Associations in one historical snapshot, and confounded with each other: contract, payment ' +
  'method and tenure move together. Nothing here identifies a cause or says an offer would work.';

/** Each dimension as a phrase names it. */
const NAMES: Readonly<Record<string, string>> = {
  Contract: 'contract',
  tenure: 'tenure',
  monthly: 'monthly charges',
  InternetService: 'internet',
  PaymentMethod: 'payment',
  OnlineSecurity: 'online security',
  TechSupport: 'tech support',
  PaperlessBilling: 'paperless billing',
  SeniorCitizen: 'senior citizen',
  Partner: 'partner',
  Dependents: 'dependents',
  StreamingTV: 'streaming TV',
  MultipleLines: 'multiple lines',
  PhoneService: 'phone service',
};

function name(dimension: string): string {
  return NAMES[dimension] ?? dimension;
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function levelPhrase(dimension: string, value: string): string {
  return `${name(dimension)} ${level(dimension, value)}${dimension === 'tenure' ? ' months' : ''}`;
}

/** "Contract × tenure", or "Contract" for one dimension. */
export function viewName(view: View): string {
  return capital(view.cols === null ? name(view.rows) : `${name(view.rows)} × ${name(view.cols)}`);
}

/** "Contract Month-to-month, tenure 0–3 months". */
export function cellName(view: View, cell: Cell): string {
  const rows = levelPhrase(view.rows, cell.row);
  const both =
    view.cols === null || cell.column === null
      ? rows
      : `${rows}, ${levelPhrase(view.cols, cell.column)}`;
  return capital(both);
}

/** What the line above the grid says about ``cell``, or about the whole partition. */
export function focusText(view: View, cell: Cell | null, data: Segments): string {
  if (cell === null) {
    const overall = data.base_rate === null ? '' : `${pct(data.base_rate)} `;
    return (
      `Overall observed churn in the training partition: ${overall}` +
      `(${int(data.partition_churned)} of ${int(data.partition_customers)}). ` +
      'Point at, focus or select a group.'
    );
  }
  const named = cellName(view, cell);
  if (cell.n === 0) return `${named}: no customers.`;
  if (cell.suppressed) {
    return cell.reason === 'too_few'
      ? `${named}: ${int(cell.n)} customers, fewer than ${data.min_customers}, so no churn rate is shown.`
      : `${named}: ${int(cell.n)} customers. Its churn is held back: ` +
          "with it, a smaller group's could be worked out from the totals.";
  }
  const against =
    cell.lift === null || data.base_rate === null
      ? ''
      : `, ${times(cell.lift)} the ${pct(data.base_rate)} of all ` +
        `${int(data.partition_customers)} training customers, these included`;
  return (
    `${named}: ${int(cell.n)} customers, ${int(cell.churned)} churned. ` +
    `${pct(cell.rate)} observed churn (95% interval ${interval(cell)})${against}.`
  );
}

/** "11.9–17.9%". */
export function interval(cell: Schemas['ReportedCell']): string {
  return `${pct(cell.rate_low).replace('%', '')}–${pct(cell.rate_high)}`;
}

const MEASURE_NAMES: Readonly<Record<Measure, string>> = {
  rate: 'Churn rate',
  customers: 'Customers',
  lift: 'Lift',
};

export function measureName(measure: Measure): string {
  return MEASURE_NAMES[measure];
}

export const MEASURE_OPTIONS = (Object.keys(MEASURE_NAMES) as Measure[]).map((value) => ({
  value,
  label: MEASURE_NAMES[value],
}));

/** A cell's figure on ``measure``; a hidden cell has its size and nothing else. */
export function cellText(cell: Cell, measure: Measure): string {
  if (measure === 'customers') return int(cell.n);
  if (cell.suppressed) return '—';
  if (measure === 'rate') return pct(cell.rate);
  return cell.lift === null ? '—' : times(cell.lift);
}

/** The small print under a hidden cell's dash. */
export function hiddenMark(cell: Schemas['SuppressedCell'], minimum: number): string {
  if (cell.n === 0) return 'none';
  return cell.reason === 'too_few' ? `n<${minimum}` : 'held back';
}

/** The legend's two ends and what it measures. */
export function legendText(scale: Scale): [string, string, string] {
  if (scale.measure === 'rate') return ['0%', pct(scale.top, 0), 'observed churn'];
  if (scale.measure === 'lift') {
    return [times(1 / scale.top), times(scale.top), '× the overall rate; 1× is the middle'];
  }
  return ['0', int(scale.top), 'customers'];
}

export function explorerText(explore: Explore, cell: Cell): string {
  if (explore.kind === 'link') {
    return `Open these ${int(cell.n)} training customers in the customer explorer`;
  }
  if (explore.kind === 'hidden')
    return 'A group whose churn is hidden does not open in the explorer.';
  return `The explorer cannot filter by ${name(explore.dimension)}.`;
}

/** Said while the previous view stays on screen. */
export function showing(data: Segments, next: View): string {
  return `Showing ${viewName({ rows: data.rows, cols: data.columns })} while ${viewName(next)} loads.`;
}

export const RANKED = {
  title: 'Highest observed churn in this view',
  empty: 'No group in this view has enough customers for a rate to be shown.',
  axis: 'observed churn, 95% interval; the line is the overall rate',
};

export function rankedSubtitle(reported: number, minimum: number): string {
  return (
    `The highest of ${reported} reported groups, ${minimum} or more customers each. ` +
    'Chosen for being highest, so more likely overstated than understated. ' +
    'Each 95% interval covers one group on its own, not all of them at once.'
  );
}

export const ASSOCIATIONS = {
  title: 'Association with churn, one attribute at a time',
  subtitle:
    "Bias-corrected Cramér's V for categories, |point-biserial r| for numbers: both run from 0 " +
    '(none) to 1, but measure different things. Choose one to break churn down by it alone.',
  unavailable: 'The association ranking is not available.',
  noBreakdown: 'no breakdown on this page',
};

/** "V" or "|r|", from the ranking's own name for its measure. */
export function measureSymbol(measure: string): string {
  return measure.toLowerCase().includes('cramer') ? 'V' : '|r|';
}
