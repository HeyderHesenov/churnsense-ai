/**
 * The performance page's state and its drawing arithmetic, without React.
 *
 * Every figure the page shows is a field of ``/api/performance`` or
 * ``/api/model-info``: what is here finds a served point by its label, says
 * which rates are undefined (from the served counts: the API writes 0.0
 * there), and turns served values into positions. Nothing here works out a
 * metric.
 */
import { scaleLinear, type ScaleLinear } from 'd3-scale';
import type { Performance } from '../../api/queries';
import { param } from '../../app/params';
import { cut } from '../../lib/format';
import { level } from '../../lib/labels';

export type Curve = NonNullable<Performance['validation']>;
export type Cut = Curve['points'][number];
export type Measured = NonNullable<Performance['validation_evaluation']>;
export type Recorded = NonNullable<Performance['test']>;
export type Metrics = Measured['metrics'];
export type RecordedMetrics = Recorded['metrics'];
export type Bin = Measured['reliability'][number];
export type SegmentRow = Measured['segments'][number];
export type Candidate = NonNullable<Performance['candidates']>[number];
export type Selection = NonNullable<Performance['selection']>;
export type Calibration = Performance['calibration'];

export const PERFORMANCE_PARAMS = {
  /** The explored cut as ``cut`` writes it ("0.40"); empty at the served model's own. */
  cut: param.text({ pattern: /^(0\.\d\d|1\.00)$/, maxLength: 4 }),
};

/** The point labelled ``label``, by label (the grid's floats are inexact), or -1. */
export function indexOfLabel(points: readonly Cut[], label: string): number {
  return points.findIndex((point) => cut(point.threshold) === label);
}

/** The cut in the address when the curve has it, else the served threshold's; -1 if neither. */
export function explored(points: readonly Cut[], raw: string, threshold: number): number {
  const asked = raw ? indexOfLabel(points, raw) : -1;
  return asked >= 0 ? asked : indexOfLabel(points, cut(threshold));
}

/** A rate or score as the page writes it. */
export function score(value: number): string {
  return value.toFixed(3);
}

/** Precision, or "—" where nothing is flagged: undefined, not zero. */
export function precisionOf(flagged: number, precision: number): string {
  return flagged === 0 ? '—' : score(precision);
}

/** Recall, or "—" where nobody churned. */
export function recallOf(churners: number, recall: number): string {
  return churners === 0 ? '—' : score(recall);
}

/** F1, or "—" where either of its rates is undefined. */
export function f1Of(flagged: number, churners: number, f1: number): string {
  return flagged === 0 || churners === 0 ? '—' : score(f1);
}

// --- precision against recall ------------------------------------------------------

export const PR = { width: 560, height: 360, left: 52, right: 540, top: 14, bottom: 318 };
export const prX = scaleLinear().domain([0, 1]).range([PR.left, PR.right]);
export const prY = scaleLinear().domain([0, 1]).range([PR.bottom, PR.top]);

/** A cut with a precision to draw: it flags someone. */
export function drawn(point: Cut): boolean {
  return point.flagged > 0;
}

/** The sweep as a line of (recall, precision), in cut order, through the cuts that flag someone. */
export function sweepPath(points: readonly Cut[]): string {
  return points
    .filter(drawn)
    .map(
      (p, i) => `${i === 0 ? 'M' : 'L'}${prX(p.recall).toFixed(1)} ${prY(p.precision).toFixed(1)}`,
    )
    .join(' ');
}

/**
 * The (recall, precision) pairs whose F1 is ``f1``: precision = f1·r / (2r − f1),
 * from precision 1 (recall f1 / (2 − f1)) to recall 1.
 */
export function contour(f1: number, steps = 48): string {
  const start = f1 / (2 - f1);
  let d = '';
  for (let i = 0; i <= steps; i += 1) {
    const r = start + ((1 - start) * i) / steps;
    const p = (f1 * r) / (2 * r - f1);
    d += `${i === 0 ? 'M' : 'L'}${prX(r).toFixed(1)} ${prY(p).toFixed(1)} `;
  }
  return d.trim();
}

/** The index of the drawn cut nearest a pointer at fractions ``fx``, ``fy`` of the picture. */
export function nearestCut(points: readonly Cut[], fx: number, fy: number): number {
  const x = fx * PR.width;
  const y = fy * PR.height;
  let best = -1;
  let gap = Infinity;
  points.forEach((point, i) => {
    if (!drawn(point)) return;
    const d = Math.hypot(prX(point.recall) - x, prY(point.precision) - y);
    if (d < gap) {
      gap = d;
      best = i;
    }
  });
  return best;
}

export { tabledCuts } from '../../lib/cuts';

// --- reliability --------------------------------------------------------------------

function onATenth(value: number): boolean {
  return Math.abs(value * 10 - Math.round(value * 10)) < 1e-9;
}

/** A reliability bin's range: "0.3–0.4" for tenths, "0.24–0.28" between uneven cuts. */
export function binLabel(bin: Pick<Bin, 'low' | 'high'>): string {
  const tenths = onATenth(bin.low) && onATenth(bin.high);
  const edge = (value: number) => (tenths ? value.toFixed(1) : cut(value));
  return `${edge(bin.low)}–${edge(bin.high)}`;
}

export const REL = { width: 360, height: 360, left: 54, right: 350, top: 12, bottom: 308 };
export const relX = scaleLinear().domain([0, 1]).range([REL.left, REL.right]);
export const relY = scaleLinear().domain([0, 1]).range([REL.bottom, REL.top]);

// --- the one-standard-error rule ---------------------------------------------------

/** The candidates the rule kept inside one standard error of the leader. */
export function charted(candidates: readonly Candidate[]): Candidate[] {
  return candidates.filter((candidate) => candidate.within_one_se);
}

/** An axis that holds every charted whisker and the cutoff, padded and rounded. */
export function selectionScale(
  shown: readonly Candidate[],
  cutoff: number,
): ScaleLinear<number, number> {
  const lows = shown.map((c) => c.cv_pr_auc - c.cv_se);
  const highs = shown.map((c) => c.cv_pr_auc + c.cv_se);
  const low = Math.min(cutoff, ...lows);
  const high = Math.max(cutoff, ...highs);
  const pad = (high - low) * 0.08 || 0.005;
  return scaleLinear()
    .domain([low - pad, high + pad])
    .range([0, 1])
    .nice(4);
}

// --- segments -----------------------------------------------------------------------

const DIMENSION_NAMES: Record<string, string> = {
  Contract: 'Contract',
  InternetService: 'Internet service',
  SeniorCitizen: 'Senior citizen',
  gender: 'Gender',
};

/** A segment breakdown's name; ``gender`` is named here only, never in the patterns page. */
export function dimensionName(key: string): string {
  return DIMENSION_NAMES[key] ?? key;
}

/** A segment's name: as the patterns page writes it, except customers without internet. */
export function segmentName(dimension: string, value: string): string {
  return dimension === 'InternetService' && value === 'No'
    ? 'No internet'
    : level(dimension, value);
}

/** Rows grouped by dimension, in the order the server sent them. */
export function groups<T extends SegmentRow>(
  rows: readonly T[],
): { dimension: string; rows: T[] }[] {
  const grouped = new Map<string, T[]>();
  for (const row of rows) grouped.set(row.dimension, [...(grouped.get(row.dimension) ?? []), row]);
  return [...grouped].map(([dimension, members]) => ({ dimension, rows: members }));
}
