/**
 * The overview's retention queue: what its address holds, and the request it
 * makes. Bands are the configured risk bands; until some are chosen the queue
 * shows every band that holds flagged customers (the data decides, so a new
 * threshold never hides flagged customers from the default view).
 */
import { param, type Values } from '../../app/params';
import type { BandSummary, CustomerQuery, Overview } from '../../api/queries';
import { BAND_NAMES, isBandName, type BandName } from '../../lib/bands';

/** The design's page: eight rows, so one explanation request covers a page. */
export const PAGE_SIZE = 8;

export const QUEUE_PARAMS = {
  bands: param.list(BAND_NAMES, null),
  part: param.optional(['train', 'validation', 'test'] as const),
  q: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: 32 }),
  sort: param.choice(['probability', 'tenure', 'monthly', 'at_risk'] as const, 'at_risk'),
  dir: param.choice(['asc', 'desc'] as const, 'desc'),
  page: param.int({ min: 1, max: 100_000, fallback: 1 }),
};

export type QueueState = Values<typeof QUEUE_PARAMS>;

/** A band summary whose name is one the queue's address can hold. */
export type QueueBand = BandSummary & { name: BandName };

function flaggedBands(book: Overview): QueueBand[] {
  return book.bands.filter((band): band is QueueBand => band.flagged > 0 && isBandName(band.name));
}

/** Every band that holds flagged customers, in the API's order. */
export function defaultBands(book: Overview): BandName[] {
  return flaggedBands(book).map((band) => band.name);
}

/** The bands the queue offers as chips: those that can hold flagged customers, riskiest first. */
export function chipBands(book: Overview): QueueBand[] {
  return flaggedBands(book).reverse();
}

/**
 * A chip pressed: remove the band, or add it and drop any band nobody in it
 * is flagged. A choice that is the default again is ``null``, so the address
 * is the default view's.
 */
export function toggleBand(
  current: readonly BandName[],
  band: BandName,
  book: Overview,
): BandName[] | null {
  const defaults = defaultBands(book);
  const next = current.includes(band)
    ? current.filter((b) => b !== band)
    : [...current.filter((b) => defaults.includes(b)), band];
  const isDefault = next.length === defaults.length && next.every((b) => defaults.includes(b));
  return isDefault ? null : next;
}

/** The export's limit, and so the selection's. */
export const MAX_SELECTED = 200;

/** ``ids`` added to (up to ``max``) or removed from a selection. */
export function select(
  current: ReadonlySet<string>,
  ids: readonly string[],
  on: boolean,
  max = MAX_SELECTED,
): Set<string> {
  const next = new Set(current);
  for (const id of ids) {
    if (!on) next.delete(id);
    else if (next.size < max) next.add(id);
  }
  return next;
}

/** The listing request for the queue's state; ``defaults`` sets the order of any bands. */
export function queueQuery(state: QueueState, defaults: readonly BandName[]): CustomerQuery {
  const bands = state.bands ?? defaults;
  // A band nobody is flagged in is no default, but a link may still name it: it goes last.
  const rank = (band: BandName) =>
    defaults.includes(band) ? defaults.indexOf(band) : defaults.length;
  return {
    flagged: true,
    exclude_churned: true,
    // In one order, so the same bands chosen either way are the same request.
    bands: [...bands].sort((a, b) => rank(a) - rank(b)),
    partition: state.part,
    q: state.q,
    sort: state.sort,
    dir: state.dir,
    page: state.page,
    page_size: PAGE_SIZE,
  };
}
