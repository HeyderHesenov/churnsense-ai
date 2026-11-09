/**
 * The single-customer page's words. Every number in them is an argument,
 * taken from the API; the fixed sentences are the artboard's, made true of
 * a page that scores on the server (the artboard scored in the browser).
 */
import type { Contribution, CustomerDetail, ExplainResponse } from '../../api/queries';
import { cents, cut, int, probabilityPct } from '../../lib/format';
import { sentenceLabel, valueText } from '../../lib/labels';
import type { AmountProblem } from './customer';

export const LEDE =
  "Each customer is scored by the same Predictor the API serves. The waterfall shows each attribute's signed contribution against an average training customer, and the contributions add up exactly to the served probability.";

export const PROFILE_NOTE =
  "Highlighted values are among this customer's four strongest contributions. Gender is not an input: it is excluded as a protected attribute.";

export const SENTENCES_NOTE =
  'Model attributions, not causal effects. They do not establish that changing an attribute would change whether this customer stays.';

export const LIVE_NOTE =
  'Scored by the API as you edit; the attributions follow once the probability is in.';

export const EXPLAIN_FAILED = 'Attributions are unavailable right now.';

export const EXPLAIN_UNAVAILABLE =
  'Attributions cannot be computed without the verified training background.';

export const EXPLAIN_BLOCKED = 'The attributions follow once the inputs can be scored.';

/** A picker card's line for the README example and for the new customer. */
export const README_META = 'README example';
export const LIVE_META = 'scored by the API';

export function flagText(flagged: boolean, threshold: number): string {
  return `${flagged ? 'Flagged' : 'Not flagged'} at ${cut(threshold)}`;
}

/** "0.443 above the threshold. Flagged at the current operating point." */
export function gapNote(probability: number, threshold: number, flagged: boolean): string {
  const gap = probability - threshold;
  const shown = Math.abs(gap).toFixed(3);
  const where =
    gap === 0
      ? 'At the threshold.'
      : Number(shown) === 0
        ? `Just ${gap > 0 ? 'above' : 'below'} the threshold.`
        : `${shown} ${gap > 0 ? 'above' : 'below'} the threshold.`;
  const offer = flagged
    ? 'Flagged at the current operating point.'
    : 'Not flagged at the current operating point.';
  return `${where} ${offer}`;
}

/** The polite announcement once a customer's figures are in. */
export function statusLine(
  name: string,
  probability: number,
  band: string,
  flagged: boolean,
  threshold: number,
): string {
  return `${name}: ${probabilityPct(probability)}, ${band} band, ${flagged ? 'flagged' : 'not flagged'} at ${cut(threshold)}.`;
}

const PARTITION_WORD: Record<CustomerDetail['partition'], string> = {
  train: 'train',
  validation: 'validation',
  test: 'test',
  unknown: 'unverified',
};

/** "validation · stayed", "test · withheld". */
export function cardMeta(detail: CustomerDetail): string {
  return `${PARTITION_WORD[detail.partition]} · ${detail.outcome}`;
}

export function notFound(id: string): string {
  return `There is no customer ${id} in the book.`;
}

export function inputsTag(count: number): string {
  return `${int(count)} model inputs`;
}

/** The waterfall's last choice: every attribution, counted from the data. */
export function showAll(count: number): string {
  return `All ${int(count)}`;
}

export function otherInputs(count: number): string {
  return `${int(count)} other ${count === 1 ? 'input' : 'inputs'}`;
}

/** One of the strongest attributions in words: the label in bold, then the rest. */
export function sentence(contribution: Contribution): {
  label: string;
  value: string;
  text: string;
} {
  const points = Math.abs(contribution.shap_value * 100).toFixed(1);
  // One decimal shown, so a figure that reads 0.0 is under half its last digit: 0.05 pp.
  const text =
    Number(points) === 0
      ? 'moves the estimated risk by less than 0.05 pp, relative to an average customer.'
      : `${contribution.shap_value > 0 ? 'raises' : 'lowers'} the estimated risk by ${points} pp, relative to an average customer.`;
  return {
    label: sentenceLabel(contribution.label),
    value: valueText(contribution.feature, contribution.value),
    text,
  };
}

/** How the server computed the attributions, in its own figures. */
export function waterfallNote(response: ExplainResponse, threshold: number): string {
  return (
    'Base plus contributions reproduces the served probability; the server checks that sum. ' +
    `Permutation explainer, ${int(response.permutations)} orderings, fixed seed, the same ` +
    `${int(response.background_customers)}-customer training background on every page. ` +
    `Dashed line: threshold ${cut(threshold)}.`
  );
}

const AMOUNT_WORDS = {
  tenure: 'Tenure is a whole number of months.',
  MonthlyCharges: 'Monthly charges are an amount in dollars.',
  TotalCharges: 'Total charges are an amount in dollars.',
} as const;

export function amountMessage(problem: AmountProblem): string {
  if (problem.reason === 'range') {
    const { field, min, max } = problem;
    return field === 'tenure'
      ? `Tenure is ${int(min)} to ${int(max)} months.`
      : `${field === 'MonthlyCharges' ? 'Monthly' : 'Total'} charges are ${cents(min)} to ${cents(max)}.`;
  }
  return problem.reason === 'billed'
    ? 'Total charges are zero only for a customer with no tenure.'
    : AMOUNT_WORDS[problem.field];
}

/** Under the API panel: the request the page made, and the address it went to. */
export function panelCaption(host: string): string {
  return `POST /api/predict · ${host} · same number as the library`;
}
