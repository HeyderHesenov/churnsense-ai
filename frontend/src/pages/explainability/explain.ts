/**
 * The explainability page's state and its drawing arithmetic, without React.
 *
 * Every figure is the API's: ``/api/explain/global`` serves ``make explain``'s
 * cached attributions for a validation sample (the matrix, the ranking and the
 * dependence summaries), ``/api/explain`` one book customer's. What is here
 * picks, compares and places them; it averages, sums or ranks nothing itself.
 *
 * One customer is ringed on the cached figures only when both were measured
 * the same way (``comparability``): the same artifact, the same base value and
 * the same background. Even then the ring is that customer explained on their
 * own, not one of the cached dots.
 */
import { scaleLinear } from 'd3-scale';
import type {
  Contribution,
  ExplainResponse,
  Explanation,
  GlobalExplanation,
} from '../../api/queries';
import { param } from '../../app/params';
import { MAX_CUSTOMER_ID } from '../../lib/customerId';
import { shownSign } from '../../lib/format';
import { FEATURES, isFeature, type Feature } from '../../lib/labels';

export type Dependence = GlobalExplanation['dependence'][number];
export type NumericDependence = Extract<Dependence, { kind: 'numeric' }>;
export type CategoricalDependence = Extract<Dependence, { kind: 'categorical' }>;
export type NumericBin = NumericDependence['bins'][number];

export const EXPLAIN_PARAMS = {
  /** The feature studied; ``null`` is the top of the served ranking. */
  f: param.optional(FEATURES),
  /** A book customer to place on the figures; empty when none is chosen. */
  id: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: MAX_CUSTOMER_ID }),
};

/** The feature the served ranking puts first, or ``null`` for an empty ranking. */
function topFeature(global: GlobalExplanation): Feature | null {
  const top = global.importance.find((row) => isFeature(row.feature));
  return top ? (top.feature as Feature) : null;
}

/** The feature studied: the one asked for when the cache explains it, else the top-ranked one. */
export function selectedFeature(global: GlobalExplanation, asked: Feature | null): Feature | null {
  return asked !== null && global.features.includes(asked) ? asked : topFeature(global);
}

/** What to write for ``feature``: nothing for the top of the ranking, the page's default. */
export function featureChange(global: GlobalExplanation, feature: Feature): Feature | null {
  return feature === topFeature(global) ? null : feature;
}

/** The explanation of ``id`` in ``response``, by its customer: never another one's. */
export function pickExplanation(
  response: ExplainResponse | undefined,
  id: string,
): Explanation | undefined {
  return response?.explanations.find((e) => e.customer_id === id);
}

/** How far apart two base values may be and still be one baseline (the server's own check). */
export const BASELINE_TOLERANCE = 1e-9;

export type Comparability = 'ok' | 'artifact' | 'baseline' | 'background';

/**
 * Whether one customer's explanation can be drawn on the cached figures. The
 * seed and the number of orderings are deliberately not compared: they change
 * how a contribution is split, not what it is measured against.
 */
export function comparability(
  global: GlobalExplanation,
  response: ExplainResponse,
  explanation: Explanation,
): Comparability {
  // An explanation that names no artifact cannot be shown to be of this one.
  if (response.model_sha256 !== global.model_sha256) return 'artifact';
  if (!(Math.abs(explanation.base_value - global.base_value) <= BASELINE_TOLERANCE))
    return 'baseline';
  if (response.background_customers !== global.n_background) return 'background';
  return 'ok';
}

/** One feature's value and contribution in a customer's explanation. */
export function contributionOf(
  explanation: Explanation | undefined,
  feature: string,
): { value: Contribution['value']; shap: number } | undefined {
  const found = explanation?.contributions.find((c) => c.feature === feature);
  return found && { value: found.value, shap: found.shap_value };
}

/** The strongest contributions that raise and that lower the estimate, by the sign shown. */
export function strongest(
  explanation: Explanation,
  count: number,
): { raises: Contribution[]; lowers: Contribution[] } {
  const by = (sign: -1 | 1) =>
    explanation.contributions.filter((c) => shownSign(c.shap_value) === sign).slice(0, count);
  return { raises: by(1), lowers: by(-1) };
}

/** A domain from zero and ``values``, widened to round ticks. */
function niceDomain(values: readonly number[]): [number, number] {
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const [a = low, b = high] = scaleLinear().domain([low, high]).nice().domain();
  return [a, b];
}

/** The swarm's shared contribution axis: zero, every cached contribution and the ringed customer's. */
export function contributionDomain(
  global: GlobalExplanation,
  explanation: Explanation | undefined,
): [number, number] {
  const customer = explanation?.contributions.map((c) => c.shap_value) ?? [];
  return niceDomain([...global.values.flat(), ...customer]);
}

/** The dependence chart's contribution axis: zero, the feature's column and the customer's. */
export function columnDomain(
  global: GlobalExplanation,
  feature: string,
  customer: number | undefined,
): [number, number] {
  const j = global.features.indexOf(feature);
  const column = j < 0 ? [] : global.values.map((row) => row[j] ?? 0);
  return niceDomain(customer === undefined ? column : [...column, customer]);
}

/** A cached customer's numeric value of ``feature``, or ``null`` for a category. */
function numberOf(row: GlobalExplanation['x'][number], feature: string): number | null {
  const value = row[feature];
  return typeof value === 'number' ? value : null;
}

/** The values axis: every cached value and the ringed customer's; a constant one is widened by 1. */
export function valueDomain(
  global: GlobalExplanation,
  feature: string,
  customer: number | undefined,
): [number, number] {
  const values = global.x.map((row) => numberOf(row, feature)).filter((v) => v !== null);
  if (customer !== undefined) values.push(customer);
  const low = Math.min(...values);
  const high = Math.max(...values);
  return low === high ? [low - 1, high + 1] : [low, high];
}

export type ValueClass = 'low' | 'mid' | 'high' | 'category';

/**
 * Each cached customer's colour on the swarm: the third of the observed range
 * their value falls in, or grey for a category (categories have no order).
 */
export function valueClasses(global: GlobalExplanation, feature: string): ValueClass[] {
  const numbers = global.x.map((row) => numberOf(row, feature));
  const present = numbers.filter((v) => v !== null);
  const low = Math.min(...present);
  const span = Math.max(...present) - low;
  return numbers.map((v) => {
    if (v === null) return 'category';
    if (span === 0) return 'mid';
    const t = (v - low) / span;
    return t < 1 / 3 ? 'low' : t < 2 / 3 ? 'mid' : 'high';
  });
}

/** The swarm's rows: their height, the space kept at each edge, and the dots. */
export const SWARM = { row: 26, pad: 3, radius: 1.6, step: 2.4, bucket: 3 } as const;

function circle(cx: number, cy: number, r: number): string {
  return `M${(cx - r).toFixed(1)} ${cy.toFixed(1)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
}

/**
 * One row of the swarm as one path per colour. Dots that land on the same
 * spot are stacked alternately above and below the row's middle, and a dense
 * stack is held at the row's edges: it saturates, it never spills into the
 * next row (the table carries the numbers).
 */
export function swarmPaths(
  column: readonly number[],
  classes: readonly ValueClass[],
  x: (value: number) => number,
  rowIndex: number,
): Record<ValueClass, string> {
  const top = rowIndex * SWARM.row;
  const middle = top + SWARM.row / 2;
  const seen = new Map<number, number>();
  const paths: Record<ValueClass, string> = { low: '', mid: '', high: '', category: '' };
  column.forEach((value, i) => {
    const cx = x(value);
    const bucket = Math.round(cx / SWARM.bucket);
    const k = (seen.get(bucket) ?? 0) + 1;
    seen.set(bucket, k);
    const offset = (k % 2 ? 1 : -1) * Math.floor(k / 2) * SWARM.step;
    const cy = Math.min(top + SWARM.row - SWARM.pad, Math.max(top + SWARM.pad, middle + offset));
    paths[classes[i] ?? 'category'] += circle(cx, cy, SWARM.radius);
  });
  return paths;
}

/** The served dependence summary of ``feature``. */
export function dependenceOf(global: GlobalExplanation, feature: string): Dependence | undefined {
  return global.dependence.find((d) => d.feature === feature);
}

/**
 * The bin means as runs of (centre, mean) to join with a line. A run ends at a
 * group too small for a mean and between groups that are not neighbours (an
 * empty group was left out): a gap is drawn as a gap, never bridged.
 * Neighbours share one served edge, the same double on both sides of the JSON
 * round trip, so the edges are compared exactly.
 */
export function meanSegments(bins: readonly NumericBin[]): [number, number][][] {
  const runs: [number, number][][] = [];
  let run: [number, number][] = [];
  let previous: NumericBin | undefined;
  for (const bin of bins) {
    if (bin.mean === null || (previous !== undefined && previous.high !== bin.low)) {
      if (run.length > 0) runs.push(run);
      run = [];
    }
    if (bin.mean !== null) run.push([(bin.low + bin.high) / 2, bin.mean]);
    previous = bin;
  }
  if (run.length > 0) runs.push(run);
  return runs;
}

/** The centre of category ``k`` of ``count``, spread evenly between ``left`` and ``right``. */
export function categoryCenter(k: number, count: number, left: number, right: number): number {
  return left + ((right - left) / count) * (k + 0.5);
}

/** A deterministic sideways offset within ``width`` for dot ``i``, for legibility only. */
export function jitter(i: number, width: number): number {
  return (((i * 9301 + 49297) % 233280) / 233280 - 0.5) * width;
}

export type Group =
  | {
      kind: 'bin';
      low: number;
      high: number;
      first: boolean;
      customers: number;
      mean: number | null;
    }
  | { kind: 'level'; category: string; customers: number; mean: number | null };

/** The groups a reader steps through on the dependence chart, in the served order. */
export function groupsOf(dependence: Dependence): Group[] {
  if (dependence.kind === 'numeric') {
    return dependence.bins.map((b, i) => ({
      kind: 'bin',
      low: b.low,
      high: b.high,
      first: i === 0,
      customers: b.customers,
      mean: b.mean,
    }));
  }
  return dependence.levels.map((l) => ({
    kind: 'level',
    category: l.category,
    customers: l.customers,
    mean: l.mean,
  }));
}

/** Each feature's rank inside the model: its place in the served ranking. */
export function modelRanks(global: GlobalExplanation): Record<string, number> {
  return Object.fromEntries(global.importance.map((row, i) => [row.feature, i + 1]));
}

export interface SlopeLine {
  feature: string;
  /** Its rank on its own, from the training outcomes. */
  association: number;
  /** Its rank inside the model, from the cached sample. */
  model: number;
}

/** A line for each feature ranked both ways, in its association order. */
export function slopeLines(global: GlobalExplanation): SlopeLine[] {
  const model = modelRanks(global);
  return Object.entries(global.association_rank ?? {})
    .filter(([feature]) => model[feature] !== undefined)
    .map(([feature, association]) => ({ feature, association, model: model[feature] ?? 0 }))
    .sort((a, b) => a.association - b.association);
}
