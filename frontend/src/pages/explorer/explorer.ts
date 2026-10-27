/**
 * The customer explorer's state: what its address holds, the requests it
 * makes, its saved views, and where previous and next lead. Every figure the
 * page shows is the API's; this module only says what to ask for.
 */
import type { Schemas } from '../../api/client';
import {
  customerParams,
  type CustomerFilter,
  type CustomerQuery,
  type Direction,
  type SortKey,
} from '../../api/queries';
import { param, type Values } from '../../app/params';
import { BAND_NAMES } from '../../lib/bands';

type Features = Schemas['CustomerFeatures'];

/** Each filter's choices, as the API names them, with the words the artboard shows. */
export const CONTRACT_LABELS = {
  'Month-to-month': 'Month-to-month',
  'One year': 'One year',
  'Two year': 'Two year',
} satisfies Record<Features['Contract'], string>;
export const INTERNET_LABELS = {
  'Fiber optic': 'Fiber optic',
  DSL: 'DSL',
  No: 'None',
} satisfies Record<Features['InternetService'], string>;
export const PAYMENT_LABELS = {
  'Electronic check': 'Electronic check',
  'Mailed check': 'Mailed check',
  'Bank transfer (automatic)': 'Bank transfer (automatic)',
  'Credit card (automatic)': 'Credit card (automatic)',
} satisfies Record<Features['PaymentMethod'], string>;

function keys<T extends string>(labels: Record<T, string>): T[] {
  return Object.keys(labels) as T[];
}

/** The tenure the API accepts in a filter, in months (not the book's own range). */
export const TENURE = { min: 0, max: 120 } as const;

export const PAGE_SIZES = ['15', '25', '50'] as const;

export const EXPLORER_PARAMS = {
  q: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: 32 }),
  bands: param.list(BAND_NAMES, BAND_NAMES),
  contract: param.optional(keys(CONTRACT_LABELS)),
  internet: param.optional(keys(INTERNET_LABELS)),
  payment: param.optional(keys(PAYMENT_LABELS)),
  tenure_min: param.optionalInt(TENURE),
  tenure_max: param.optionalInt(TENURE),
  flagged: param.optional(['true', 'false'] as const),
  part: param.optional(['train', 'validation', 'test'] as const),
  sort: param.choice(['id', 'probability', 'tenure', 'monthly', 'at_risk'] as const, 'probability'),
  dir: param.choice(['asc', 'desc'] as const, 'desc'),
  page: param.int({ min: 1, max: 100_000, fallback: 1 }),
  size: param.choice(PAGE_SIZES, '15'),
  /** The customer open in the detail panel; empty when none is. */
  id: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: 32 }),
};

export type ExplorerState = Values<typeof EXPLORER_PARAMS>;

/** The detail panel's id, which an open row's button controls. */
export const DETAIL_ID = 'customer-detail';
export type ExplorerChanges = Partial<ExplorerState>;

type FilterKey =
  | 'q'
  | 'bands'
  | 'contract'
  | 'internet'
  | 'payment'
  | 'tenure_min'
  | 'tenure_max'
  | 'flagged'
  | 'part';
export type Filters = Pick<ExplorerState, FilterKey>;

export const FILTER_DEFAULTS: Filters = {
  q: '',
  bands: BAND_NAMES,
  contract: null,
  internet: null,
  payment: null,
  tenure_min: null,
  tenure_max: null,
  flagged: null,
  part: null,
};

const FILTER_KEYS = Object.keys(FILTER_DEFAULTS) as FilterKey[];

function sameValue(a: Filters[FilterKey], b: Filters[FilterKey]): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    const these = new Set<string>(a);
    const those = new Set<string>(b);
    return these.size === those.size && [...these].every((v) => those.has(v));
  }
  return a === b;
}

/** The listing filter for the address's filters; every band is no band list at all. */
export function explorerFilter(filters: Filters): CustomerFilter {
  const everyBand = sameValue(filters.bands, BAND_NAMES);
  return {
    q: filters.q,
    // Omitted rather than undefined: the filter's keys are optional, not nullable.
    ...(everyBand ? {} : { bands: filters.bands }),
    contract: filters.contract,
    internet: filters.internet,
    payment: filters.payment,
    tenure_min: filters.tenure_min,
    tenure_max: filters.tenure_max,
    flagged: filters.flagged === null ? null : filters.flagged === 'true',
    partition: filters.part,
  };
}

/** The page of the listing the address shows. */
export function listingQuery(state: ExplorerState): CustomerQuery {
  return {
    ...explorerFilter(state),
    sort: state.sort,
    dir: state.dir,
    page: state.page,
    page_size: Number(state.size),
  };
}

/** The export's query: the same customers in the same order, every page of them. */
export function exportQuery(state: ExplorerState) {
  return customerParams({ ...explorerFilter(state), sort: state.sort, dir: state.dir });
}

/**
 * Why the listing asks for nothing: no band chosen (the API would read that
 * as every band), or a tenure range that holds nobody (the API refuses it).
 */
export type IdleReason = 'no-band' | 'tenure' | null;

export function idleReason(filters: Filters): IdleReason {
  if (filters.bands.length === 0) return 'no-band';
  if (tenureInvalid(filters)) return 'tenure';
  return null;
}

/** A tenure range whose minimum is above its maximum, whatever else is chosen. */
export function tenureInvalid(filters: Filters): boolean {
  const { tenure_min: min, tenure_max: max } = filters;
  return min !== null && max !== null && min > max;
}

/**
 * What a tenure box shows for what was typed: digits only, as the bound they
 * mean ("06" is 6, past the API's range is its end), so the box never shows a
 * bound the filter does not hold.
 */
export function tenureText(typed: string): string {
  const digits = typed.replace(/\D/g, '');
  return digits === '' ? '' : String(Math.min(Number(digits), TENURE.max));
}

/** A tenure box's digits as a bound: empty is open, past the API's range is its end. */
export function tenureValue(text: string): number | null {
  return text === '' ? null : Math.min(Number(text), TENURE.max);
}

export interface Preset {
  label: string;
  filters: Partial<Filters>;
}

export const PRESETS: readonly Preset[] = [
  { label: 'All customers', filters: {} },
  { label: 'Flagged · month-to-month', filters: { flagged: 'true', contract: 'Month-to-month' } },
  { label: 'First six months', filters: { tenure_max: 6 } },
  { label: 'Fiber + e-check', filters: { internet: 'Fiber optic', payment: 'Electronic check' } },
  { label: 'Two-year contracts', filters: { contract: 'Two year' } },
];

export function presetFilters(preset: Preset): Filters {
  return { ...FILTER_DEFAULTS, ...preset.filters };
}

/** A view applied: its filters over the defaults (the search cleared), from the first page. */
export function presetChanges(preset: Preset): ExplorerChanges {
  return { ...presetFilters(preset), page: 1 };
}

function sameFilters(a: Filters, b: Filters): boolean {
  return FILTER_KEYS.every((key) => sameValue(a[key], b[key]));
}

/** A view is pressed only while every filter is exactly what it sets. */
export function isPreset(filters: Filters, preset: Preset): boolean {
  return sameFilters(filters, presetFilters(preset));
}

/** How many filters are off their default; each tenure bound counts on its own. */
export function activeFilters(filters: Filters): number {
  return FILTER_KEYS.filter((key) => !sameValue(filters[key], FILTER_DEFAULTS[key])).length;
}

/** Every filter back to its default; the sort, page size and open customer stay. */
export const RESET: ExplorerChanges = { ...FILTER_DEFAULTS, page: 1 };

/** Where a column starts when it is chosen: IDs and tenure ascending, the rest descending. */
export const DEFAULT_DIRECTION: Record<SortKey, Direction> = {
  id: 'asc',
  probability: 'desc',
  tenure: 'asc',
  monthly: 'desc',
  at_risk: 'desc',
};

/**
 * The customer before or after the one at ``index`` (0-based, in the whole
 * filtered and sorted list), wrapping at both ends: its page and its row on
 * that page. ``null`` when there is nobody else to go to.
 */
export function neighbour(
  index: number,
  step: 1 | -1,
  total: number,
  size: number,
): { page: number; row: number } | null {
  if (total < 2) return null;
  const next = (index + step + total) % total;
  return { page: Math.floor(next / size) + 1, row: next % size };
}

/** The page, at a new size, that holds the customer at ``anchor`` (0-based). */
export function pageKeeping(anchor: number, size: number): number {
  return Math.floor(anchor / size) + 1;
}
