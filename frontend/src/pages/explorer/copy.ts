/**
 * The explorer's sentences that depend on the data. Every figure in them is
 * the API's, and a claim is made only when the figures support it: a band
 * "tops out below the threshold" only when its top is below it. Formatting
 * only: nothing here computes a statistic.
 */
import type { CustomerPage, ExplainResponse, Explanation, ModelInfo } from '../../api/queries';
import { bandTop } from '../../lib/bands';
import { cut, int, pct, probabilityPct } from '../../lib/format';
import type { Filters, IdleReason } from './explorer';

type Summary = CustomerPage['summary'];
type RiskBand = NonNullable<ModelInfo['risk_bands']>[number];

/**
 * The page's lede: the book's size (from the API; ``undefined`` while it
 * loads, ``null`` if it could not be loaded) and whose outcomes it shows
 * (``undefined`` until the API has said whether they are withheld).
 */
export function lede(
  total: number | null | undefined,
  withheld: string | null | undefined,
  synthetic = false,
): string {
  const count = total === undefined ? '… ' : total === null ? '' : `${int(total)} `;
  // Generated customers have no outcome to show or withhold: the note above says so.
  if (synthetic) return `All ${count}synthetic demo customers with their served probability.`;
  const rows = `All ${count}rows with their served probability.`;
  if (withheld === undefined) return rows;
  return withheld
    ? `${rows} Observed outcomes are withheld: ${withheld}`
    : `${rows} Outcomes appear for train and validation customers only; the test partition's are withheld.`;
}

/** The observed-churn tile: train and validation outcomes only, and never for fewer than 20. */
export function observedTile(
  summary: Summary,
  withheld: string | null,
  synthetic = false,
): { value: string; sub: string } {
  // Nothing was observed of a generated customer: the tile never shows a rate for them.
  if (synthetic) return { value: '—', sub: 'synthetic customers have no outcome' };
  if (withheld) return { value: 'Withheld', sub: 'outcomes not shown' };
  if (summary.customers === 0) return { value: '—', sub: 'nobody in the selection' };
  const observed = summary.observed;
  if (observed === null) return { value: '—', sub: 'test outcomes withheld' };
  if (observed.rate === null || observed.churned === null) {
    return { value: '—', sub: 'fewer than 20 with outcomes' };
  }
  return {
    value: pct(observed.rate),
    sub: `${int(observed.churned)} of ${int(observed.customers)} train + val`,
  };
}

const PARTITION_NAME = { train: 'Train', validation: 'Validation', test: 'Test' } as const;

interface HintInput {
  filters: Filters;
  idle: IdleReason;
  /** The served risk bands, once ``/api/model-info`` has answered. */
  bands: readonly RiskBand[] | undefined;
  threshold: number | undefined;
  withheld: string | null;
}

/** Why nothing matches, in terms of the filters and the served bands. */
export function emptyHint({ filters, idle, bands, threshold, withheld }: HintInput): string {
  if (idle === 'no-band') return 'No band is selected.';
  if (idle === 'tenure') return 'The minimum tenure is above the maximum.';
  if (withheld && filters.part) {
    return `No customer's partition could be verified, so none is listed under ${PARTITION_NAME[filters.part]}.`;
  }
  const only =
    filters.bands.length === 1 ? bands?.find((b) => b.name === filters.bands[0]) : undefined;
  if (only && threshold !== undefined) {
    // A band holds its lower bound, not its upper one (the top band's stored
    // bound is above 1, so it holds 1): it has nobody flagged only when its
    // upper bound is at or below the threshold.
    if (filters.flagged === 'true' && only.max <= threshold) {
      return only.max < threshold
        ? `${only.name} tops out at ${cut(bandTop(only))}, below the ${cut(threshold)} threshold, so nobody in it is flagged.`
        : `${only.name} stops short of ${cut(threshold)}, the threshold, so nobody in it is flagged.`;
    }
    if (filters.flagged === 'false' && only.min >= threshold) {
      return only.min > threshold
        ? `${only.name} starts at ${cut(only.min)}, above the ${cut(threshold)} threshold, so everyone in it is flagged.`
        : `${only.name} starts at ${cut(threshold)}, the threshold, so everyone in it is flagged.`;
    }
  }
  return 'Loosen a filter or clear the ID search.';
}

/** "Showing 1–15 of 7,043", and how to open a row while none is open. */
export function pageNote(page: CustomerPage, shown: number, open: boolean): string {
  if (page.total === 0) return 'No rows';
  const first = (page.page - 1) * page.page_size + 1;
  const range = `Showing ${int(first)}–${int(first + shown - 1)} of ${int(page.total)}`;
  return open ? range : `${range} · select a row to open its profile and explanation`;
}

/** What a screen reader hears once the selection's figures are in. */
export function selectionStatus(summary: Summary): string {
  const one = summary.customers === 1;
  return `${int(summary.customers)} ${one ? 'customer matches' : 'customers match'}, ${int(summary.flagged)} flagged.`;
}

export function exportLabel(total: number | undefined): string {
  if (total === undefined) return 'Export rows';
  return `Export ${int(total)} ${total === 1 ? 'row' : 'rows'}`;
}

/** The flag filter's caption: the served threshold, once it is known. */
export function flagCaption(threshold: number | undefined): string {
  return threshold === undefined ? 'Flag' : `Decision at ${cut(threshold)}`;
}

export function flaggedLabel(threshold: number | undefined): string {
  return threshold === undefined ? 'Flagged' : `Flagged at ${cut(threshold)}`;
}

/** "base 26.5% → 80.3%": from the model's average to this customer. */
export function explanationMeta(explanation: Explanation): string {
  return `base ${pct(explanation.base_value)} → ${probabilityPct(explanation.churn_probability)}`;
}

/** How the server computed the attributions, in its own figures. */
export function explanationNote(response: ExplainResponse): string {
  return (
    `Percentage points against the model's average over ${int(response.background_customers)} ` +
    'training customers; orange raises, blue lowers. Computed on the server with ' +
    `${int(response.permutations)} feature orderings. Model attributions, not causal effects.`
  );
}
