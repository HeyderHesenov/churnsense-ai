/**
 * The overview's sentences that depend on the data. Every figure in them
 * comes from the API, and a claim is made only when the figures support it:
 * "not monotonic" only when the peak is inside the range, "within one offer's
 * cost" only when it is. Formatting only: nothing here computes a statistic.
 */
import type { DataSource, Overview } from '../../api/queries';
import type { Schemas } from '../../api/client';
import { bandTop as top } from '../../lib/bands';
import { cut, int, money, pct } from '../../lib/format';
import { FEATURE, isFeature, overviewLevel } from '../../lib/labels';

type Range = { min: number; max: number };

/** A band card's range: "0–0.25", "0.75–1.00". */
export function bandRange(band: Range): string {
  return `${band.min === 0 ? '0' : cut(band.min)}–${cut(top(band))}`;
}

/** The scale's legend: the first band in full ("0–25%"), the rest by their top ("–50%"). */
export function scaleLabel(band: Range, index: number): string {
  const percent = (value: number) => String(Math.round(value * 100));
  return index === 0 ? `${percent(band.min)}–${percent(top(band))}%` : `–${percent(top(band))}%`;
}

/** The scale's accessible name: every band's range, and the threshold. */
export function scaleDescription(
  bands: readonly (Range & { name: string })[],
  threshold: number,
): string {
  const percent = (value: number) => String(Math.round(value * 100));
  const ranges = bands.map((band, i) =>
    i === 0
      ? `${band.name} ${percent(band.min)} to ${percent(top(band))}%`
      : `${band.name} to ${percent(top(band))}%`,
  );
  return `Probability scale: ${ranges.join(', ')}; flagged at ${cut(threshold)} and above`;
}

/**
 * The queue's link to everyone flagged. The queue leaves out customers recorded as
 * churned and the explorer does not, which is worth saying only where an outcome is
 * recorded: a generated customer has none.
 */
export function allFlaggedLink(flagged: number, synthetic: boolean): string {
  return synthetic
    ? `See all ${int(flagged)} flagged in the explorer`
    : `See all ${int(flagged)} flagged, churned included, in the explorer`;
}

/** A ratio of rates: "17×", "3.2×". */
export function spread(value: number): string {
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)}×`;
}

type SegmentTab = Schemas['SegmentTab'];

const NO_WHY = 'An association in one snapshot; it does not say why.';

/** The note under a segment tab. */
export function segmentNote(tab: SegmentTab): string {
  switch (tab.key) {
    case 'Contract':
      return tab.spread === null
        ? `Observed churn differs across contract types. ${NO_WHY}`
        : `A ${spread(tab.spread)} spread in observed churn across contract types. ${NO_WHY}`;
    case 'PaymentMethod':
      return `Observed churn differs across payment methods. ${NO_WHY}`;
    case 'InternetService':
      return `Observed churn differs across internet service types. ${NO_WHY}`;
    case 'monthly':
      return priceNote(tab);
    default:
      return '';
  }
}

function priceNote(tab: SegmentTab): string {
  if (tab.peak === null) return '';
  const label = overviewLevel(tab.key, tab.peak);
  const levels = tab.levels.map((level) => level.row);
  const inside = levels.indexOf(tab.peak) > 0 && levels.indexOf(tab.peak) < levels.length - 1;
  return inside ? `Not monotonic: churn peaks at ${label}.` : `Churn is highest at ${label}.`;
}

const ORDINALS = [
  'first',
  'second',
  'third',
  'fourth',
  'fifth',
  'sixth',
  'seventh',
  'eighth',
  'ninth',
  'tenth',
];

function ordinal(rank: number): string {
  return ORDINALS[rank - 1] ?? `${rank}th`;
}

/**
 * How the feature most associated with churn on its own ranks in the model,
 * or ``null`` when the two rankings cannot be compared.
 */
export function importanceNote(
  importance: NonNullable<Overview['importance']>,
  association: NonNullable<Overview['association']>,
): string | null {
  const lead = association[0]?.feature;
  if (lead === undefined) return null;
  const rank = importance.findIndex((row) => row.feature === lead) + 1;
  if (rank === 0) return null;
  const name = isFeature(lead) ? FEATURE[lead] : lead;
  if (rank === 1) return `${name} ranks first both on its own and in the model.`;
  return `${name} ranks first on its own but ${ordinal(rank)} in the model: on its own, a feature also carries what it shares with others.`;
}

type Campaign = Schemas['Campaign'];

/** "0.36 shipped · 0.30 peak, $32 higher". */
export function campaignCaption(campaign: Campaign): string {
  if (campaign.peak_threshold === campaign.threshold)
    return `${cut(campaign.threshold)} shipped, at the peak`;
  return `${cut(campaign.threshold)} shipped · ${cut(campaign.peak_threshold)} peak, ${money(campaign.peak_gain)} higher`;
}

/**
 * The sparkline's accessible name. "Within one offer's cost of the peak" is
 * the server's recommendation rule, so it is said only of the threshold that
 * rule chose.
 */
export function campaignLabel(campaign: Campaign): string {
  const within = campaign.threshold === campaign.recommended_threshold;
  return (
    `Simulated net benefit by threshold on validation. Peak at ${cut(campaign.peak_threshold)}, ` +
    `shipped threshold ${cut(campaign.threshold)}` +
    (within ? ", within one offer's cost of the peak." : '.')
  );
}

/** Why a band holds nobody flagged: its top, when that is below the threshold. */
export function unflaggedNote(
  band: Range & { name: string; customers: number },
  threshold: number,
): string {
  if (top(band) <= threshold) {
    return (
      `${band.name} tops out at a probability of ${cut(top(band))}, below the ${cut(threshold)} ` +
      `threshold, so these ${int(band.customers)} customers never enter the queue.`
    );
  }
  return `None of the ${int(band.customers)} customers in ${band.name} reaches the ${cut(threshold)} threshold.`;
}

/** What following a key figure into the queue does. */
export const SHOW_IN_QUEUE = 'Show them in the retention queue';

/** The bands the key figure opens the queue on: the two riskiest. */
export function riskiestBands(book: Overview): string[] {
  return book.bands.slice(-2).map((band) => band.name);
}

/** Every assumption the simulated money rests on. */
export function assumptionsNote(assumptions: Schemas['BusinessAssumptions']): string {
  return (
    `Assumed: offer ${money(assumptions.retention_offer_cost)}, ` +
    `success ${pct(assumptions.offer_success_rate, 0)}, ` +
    `${assumptions.expected_horizon_months} months, margin ${pct(assumptions.gross_margin, 0)}. ` +
    'Not measured: the data holds no campaign outcomes.'
  );
}

/**
 * Whose customers the page counts, for its footer: the dataset's rows, or -
 * where the service lists generated customers - the name the service gives them.
 */
export function footerSource(source: DataSource | null | undefined, customers: number): string {
  return source?.customer_records === 'synthetic'
    ? `${source.title} · ${int(customers)} generated customers`
    : `IBM Telco Customer Churn · ${int(customers)} rows`;
}

type Observed = Schemas['Observed'];

/** A band's observed churn in a few words: "8.0% churned", "withheld", or "—" (too few, or none published). */
export function observedShort(band: { observed: Observed | null }, withheld: boolean): string {
  if (withheld) return 'withheld';
  const rate = band.observed?.rate;
  return rate === null || rate === undefined ? '—' : `${pct(rate)} churned`;
}

/** The ring's accessible name: each band's count and share, and everyone flagged. */
export function donutDescription(book: Overview): string {
  const bands = book.bands
    .map(
      (band) =>
        `${band.name} ${int(band.customers)}${band.share === null ? '' : ` (${pct(band.share)})`}`,
    )
    .join(', ');
  const share = book.flagged.share === null ? '' : `, ${pct(book.flagged.share)} of the book`;
  return `Customers by risk band: ${bands}. ${int(book.flagged.customers)} flagged at ${cut(book.threshold)}${share}.`;
}

/** A median tenure as whole months, or a dash when the API has none. */
export function wholeMonths(value: number | null): string {
  return value === null ? '—' : String(Math.round(value));
}

/** The briefing's row on who is flagged: the API's share and count, never a ratio made here. */
export function flaggedBrief(book: Overview): { title: string; fact: string } {
  const share = book.flagged.share;
  return {
    title:
      share === null
        ? `${int(book.flagged.customers)} customers are flagged`
        : `${pct(share)} of the book is flagged`,
    fact: `${int(book.flagged.customers)} customers at or above ${cut(book.threshold)}; ${int(book.flagged.in_queue)} of them in the retention queue.`,
  };
}

/** The briefing's row on tenure, or ``null`` when either median is missing. */
export function tenureBrief(
  lifecycle: Overview['lifecycle'],
): { title: string; fact: string } | null {
  if (
    !lifecycle ||
    lifecycle.median_tenure_churned === null ||
    lifecycle.median_tenure_stayed === null
  )
    return null;
  return {
    title: 'Tenure of who left and who stayed',
    fact: `Median tenure ${wholeMonths(lifecycle.median_tenure_churned)} months among customers who churned, ${wholeMonths(lifecycle.median_tenure_stayed)} among those who stayed (training partition).`,
  };
}

/** The briefing's row on the simulated campaign: "pays" only when the API's net benefit is above zero. */
export function campaignBrief(campaign: Campaign): { title: string; fact: string } {
  return {
    title:
      campaign.net_benefit > 0
        ? 'A campaign pays, on paper'
        : 'A campaign does not pay at these assumptions',
    fact: `${money(campaign.net_benefit)} simulated net benefit at ${cut(campaign.threshold)} on validation, ${int(campaign.flagged)} offers.`,
  };
}

/** What the "segments to watch" card says of how its groups were chosen. */
export function watchNote(minimum: number): string {
  return (
    `The highest observed churn among contract and tenure groups on the training partition, ` +
    `${int(minimum)} or more customers each, in the order the API ranks them. Chosen for being ` +
    'highest, so more likely overstated than understated. An association, not a cause.'
  );
}
