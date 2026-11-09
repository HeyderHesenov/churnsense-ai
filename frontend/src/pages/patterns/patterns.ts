/**
 * The patterns page's state and the arithmetic of drawing it. Every figure is
 * the API's (``/api/segments``): observed churn of the training partition,
 * with small groups' churn already hidden by the server. This module averages,
 * sums and ranks nothing; it reads the address, finds a served cell, scales a
 * served number to a shade, and writes the explorer's own filters for a group.
 */
import type { Schemas } from '../../api/client';
import type { Cell, Segments } from '../../api/queries';
import { hrefTo, param, type Values } from '../../app/params';
import { DIMENSIONS } from '../../lib/labels';
import { EXPLORER_PARAMS, type ExplorerChanges } from '../explorer/explorer';

const DIMENSION_KEYS = DIMENSIONS.map(([key]) => key);
/** The column select's "one dimension only". */
export const NONE = 'none';
export const MEASURES = ['rate', 'customers', 'lift'] as const;
export type Measure = (typeof MEASURES)[number];

/** A breakdown level as the address may hold it: the API's levels use no other characters. */
const LEVEL = { pattern: /^[A-Za-z0-9 $<+().-]*$/, maxLength: 40 };

export const PATTERNS_PARAMS = {
  rows: param.choice(DIMENSION_KEYS, 'Contract'),
  cols: param.choice([...DIMENSION_KEYS, NONE], 'tenure'),
  show: param.choice(MEASURES, 'rate'),
  /** The pinned cell's row and column levels; empty when nothing is pinned. */
  pr: param.text(LEVEL),
  pc: param.text(LEVEL),
};

export type PatternsState = Values<typeof PATTERNS_PARAMS>;

/** The breakdown asked for: two dimensions, or one when ``cols`` is null. */
export interface View {
  rows: string;
  cols: string | null;
}

/** The address's view. A dimension crossed with itself, which the API refuses, reads as one. */
export function viewOf(state: PatternsState): View {
  const cols = state.cols === NONE || state.cols === state.rows ? null : state.cols;
  return { rows: state.rows, cols };
}

export const SUGGESTED: readonly (View & { label: string; cols: string })[] = [
  { label: 'Contract × tenure', rows: 'Contract', cols: 'tenure' },
  { label: 'Internet × payment', rows: 'InternetService', cols: 'PaymentMethod' },
  { label: 'Price × tenure', rows: 'monthly', cols: 'tenure' },
  { label: 'Tech support × contract', rows: 'TechSupport', cols: 'Contract' },
];

/** A response's cells by their levels: its own levels, so a response shown while the next loads keeps its labels. */
export interface Grid {
  rows: readonly string[];
  /** ``null`` for one dimension. */
  columns: readonly string[] | null;
  at(row: string, column: string | null): Cell | undefined;
}

export function grid(data: Segments): Grid {
  const columns = data.columns === null ? null : data.column_totals.map((c) => c.row);
  const cells = columns === null ? data.row_totals : data.cells;
  return {
    rows: data.row_totals.map((c) => c.row),
    columns,
    at: (row, column) => cells.find((c) => c.row === row && c.column === column),
  };
}

/** Whether two cells are the same group (one may come from the ranking, one from the grid). */
export function sameGroup(a: Cell, b: Cell): boolean {
  return a.row === b.row && a.column === b.column;
}

/** The cell the address pins, if the shown response holds it. */
export function pinnedCell(data: Segments, state: PatternsState): Cell | null {
  if (state.pr === '') return null;
  const g = grid(data);
  if (g.columns === null ? state.pc !== '' : !g.columns.includes(state.pc)) return null;
  return g.at(state.pr, g.columns === null ? null : state.pc) ?? null;
}

/** The groups a measure is shaded over: the cells, or the levels of one dimension. */
function groups(data: Segments): readonly Cell[] {
  return data.columns === null ? data.row_totals : data.cells;
}

/** What one shade spans: rates from 0 to ``top``; lift from 1/``top`` to ``top``; sizes from 0. */
export interface Scale {
  measure: Measure;
  top: number;
}

/** The smallest lift range drawn, so that groups a few percent apart do not look far apart. */
const NARROWEST_LIFT = 1.5;

export function scaleOf(data: Segments, measure: Measure): Scale {
  const shown = groups(data).filter((c): c is Schemas['ReportedCell'] => !c.suppressed);
  if (measure === 'customers') {
    return { measure, top: Math.max(1, ...groups(data).map((c) => c.n)) };
  }
  if (measure === 'rate') {
    const highest = Math.max(0, ...shown.map((c) => c.rate));
    return { measure, top: Math.min(1, Math.max(0.1, Math.ceil(highest * 10) / 10)) };
  }
  const widest = Math.max(
    NARROWEST_LIFT,
    ...shown.map((c) => (c.lift ? Math.max(c.lift, 1 / c.lift) : 1)),
  );
  return { measure, top: Math.ceil(widest * 2) / 2 };
}

/**
 * Where a cell's figure sits on its scale: 0 to 1, or -1 to 1 for lift (below
 * and above the partition's rate). ``null`` for a hidden figure.
 */
export function shadeOf(cell: Cell, scale: Scale): number | null {
  if (scale.measure === 'customers') return cell.n / scale.top;
  if (cell.suppressed) return null;
  if (scale.measure === 'rate') return Math.min(1, cell.rate / scale.top);
  if (cell.lift === null) return null;
  // Nobody churned: as far below the partition's rate as the scale reaches.
  if (cell.lift === 0) return -1;
  return Math.max(-1, Math.min(1, Math.log(cell.lift) / Math.log(scale.top)));
}

/** The association ranking's features this page breaks churn down by, under another key. */
const FEATURE_DIMENSION: Readonly<Record<string, string>> = { MonthlyCharges: 'monthly' };
/** A protected attribute the model does not take: never named here. */
const UNNAMED = new Set(['gender']);

export interface AssociationRow {
  feature: string;
  measure: string;
  strength: number;
  /** The dimension it opens, or ``null`` when the page has no breakdown of it. */
  dimension: string | null;
}

export function associationRows(data: Segments): AssociationRow[] {
  return (data.association ?? [])
    .filter((a) => !UNNAMED.has(a.feature))
    .map((a) => {
      const key = FEATURE_DIMENSION[a.feature] ?? a.feature;
      return { ...a, dimension: DIMENSION_KEYS.includes(key) ? key : null };
    });
}

/** Opening a group in the explorer: a link, or why there is none. */
export type Explore =
  { kind: 'link'; href: string } | { kind: 'hidden' } | { kind: 'unfiltered'; dimension: string };

const TENURE_LEVEL = /^(\d+)-(\d+)$/;
type FilterKey = 'contract' | 'internet' | 'payment' | 'tenure_min' | 'tenure_max';

/** The explorer filters that keep exactly one level of ``dimension``, or ``null``. */
function filterFor(dimension: string, level: string): ExplorerChanges | null {
  let changes: ExplorerChanges;
  if (dimension === 'tenure') {
    const months = TENURE_LEVEL.exec(level);
    if (!months) return null;
    changes = { tenure_min: Number(months[1]), tenure_max: Number(months[2]) };
  } else {
    const key = { Contract: 'contract', InternetService: 'internet', PaymentMethod: 'payment' }[
      dimension
    ];
    if (key === undefined) return null;
    changes = { [key]: level };
  }
  // A value the explorer would not read back would silently drop the filter.
  const readBack = Object.entries(changes).every(
    ([key, value]) => EXPLORER_PARAMS[key as FilterKey].parse([String(value)]) === value,
  );
  return readBack ? changes : null;
}

/**
 * The explorer, filtered to the training customers of one reported group: the
 * same rows the group counts, so its summary repeats the group's figures. No
 * link for a hidden group, or for a dimension the explorer cannot filter.
 */
export function explore(view: View, cell: Cell): Explore {
  if (cell.suppressed) return { kind: 'hidden' };
  const rows = filterFor(view.rows, cell.row);
  if (rows === null) return { kind: 'unfiltered', dimension: view.rows };
  let changes = rows;
  if (view.cols !== null) {
    const cols = cell.column === null ? null : filterFor(view.cols, cell.column);
    if (cols === null) return { kind: 'unfiltered', dimension: view.cols };
    changes = { ...rows, ...cols };
  }
  return {
    kind: 'link',
    href: hrefTo('/explorer', EXPLORER_PARAMS, { ...changes, part: 'train' }),
  };
}
