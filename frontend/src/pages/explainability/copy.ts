/**
 * The explainability page's words. A sentence that carries a figure writes a
 * served field through ``lib/format``. None claims more than an attribution
 * supports: a contribution moves this model's output against a baseline; it
 * is not a reason a customer leaves, and a sum of rounded contributions is
 * never printed (it would not add up).
 */
import type { GlobalExplanation } from '../../api/queries';
import { cents, int, signedPoints } from '../../lib/format';
import { FEATURE, isFeature, valueText } from '../../lib/labels';
import type { Comparability, Group } from './explain';

export const TITLE = 'How the shipped model uses each attribute';

export const LEDE =
  'The artifact itself is explained, calibration and clamp included, so contributions plus the base value reproduce the served probability. These are attributions of this model’s output, not reasons customers leave.';

/** The unit of every contribution on the page. */
export const SCALE = 'percentage points of churn probability';

export const ROUNDING_NOTE =
  'Contributions are shown to one decimal. Their unrounded values add up to the served probability, which the server checks to within 1e-9.';

export const ASSOCIATION_METHOD =
  'On its own: each attribute’s association with churn in the training outcomes, Cramér’s V for categories and |point-biserial r| for numbers, two measures in one ranking. In the model: the order of mean |contribution| on the cached validation sample. They answer different questions, so both are shown.';

export const MASKING_CAVEAT =
  'The permutation explainer masks features independently, so it also evaluates combinations no real customer has, such as add-ons without internet. Read every contribution as an attribution under that approximation.';

// --- the cards -------------------------------------------------------------------

export const CARDS = {
  customer: {
    title: 'One customer',
    subtitle: 'Place a customer from the book on the figures, from their own explanation.',
    prompt: 'Pick an example or type an ID to place a customer on the figures.',
  },
  swarm: {
    title: 'Every attribution, every attribute',
    subtitle:
      'Which attributes move the output most, and in which direction? One dot per explained customer. Select a row to study it.',
    note: `Categorical attributes are drawn in grey; select one to see its categories in the dependence chart. Horizontal position is the contribution in ${SCALE}.`,
  },
  dependence: {
    noSummary: 'No summary is served for this attribute.',
  },
  rankings: {
    title: 'Two rankings, both published',
    unavailable: 'The association ranking is not available, so only the model’s ranking is shown.',
    hint: 'Select a name to highlight it here and above.',
  },
} as const;

/** What each method tile's figure is. */
export const METHOD_NOTES = {
  base: 'mean served probability over the background',
  background: 'training customers, the same on every page',
  orderings: 'antithetic permutation explainer',
  additivity: 'largest gap before rounding, base + contributions against the served probability',
} as const;

// --- states ----------------------------------------------------------------------

export const NOT_CACHED = {
  title: 'No attributions are cached for the served model',
  body: 'A maintainer computes them with `make explain`; this page shows them once they exist.',
} as const;

export const MISMATCH =
  'The served model changed while this page loaded: the cached attributions and the model’s description no longer describe the same artifact.';

export const FAILED = 'The cached attributions could not be loaded, so nothing can be shown yet.';

export const EXPLAIN_FAILED = 'This customer’s attributions could not be loaded.';

export const EXPLAIN_UNAVAILABLE =
  'Individual customers cannot be explained: the server has no verified training background.';

export const PROVENANCE_UNVERIFIED =
  'Individual customers cannot be explained: the training data behind the served model is not verified.';

export function notFound(id: string): string {
  return `No customer with ID ${id} is in the book.`;
}

const WHY_NOT_DRAWN: Record<Exclude<Comparability, 'ok'>, string> = {
  artifact: 'this explanation is of another artifact than the cached attributions',
  baseline: 'this explanation was measured from another base value than the cached attributions',
  background: 'this explanation used a background of another size than the cached attributions',
};

export function incomparable(reason: Exclude<Comparability, 'ok'>): string {
  return `Not drawn on the figures: ${WHY_NOT_DRAWN[reason]}.`;
}

// --- the method strip ------------------------------------------------------------

export function explainedSub(global: GlobalExplanation): string {
  return global.seed === null
    ? `${global.partition} partition`
    : `${global.partition} partition, seed ${global.seed}`;
}

/** The largest additivity gap the cache recorded, before any rounding: "1.9e-15". */
export function additivity(gap: number): string {
  return gap === 0 ? '0' : gap.toExponential(1);
}

// --- the swarm -------------------------------------------------------------------

/** A row's accessible name: the served mean |contribution| and its rank. */
export function rowName(label: string, meanAbs: number, rank: number): string {
  return `${label}, mean absolute contribution ${(meanAbs * 100).toFixed(1)} percentage points, rank ${rank}`;
}

/** The question each chart answers, from the artboard; a plain one for the rest. */
const QUESTIONS: Partial<Record<string, string>> = {
  tenure: 'Does a longer relationship lower the estimate, and how fast?',
  MonthlyCharges: 'How does the monthly charge’s contribution vary with its value?',
  TotalCharges: 'Collinear with tenure × price; what is left for it to say?',
  Contract: 'Which contract terms go with a higher or a lower contribution?',
  InternetService: 'Which internet tier goes with a higher estimate?',
};

export function question(feature: string): string {
  return QUESTIONS[feature] ?? 'How does each value of this attribute move the estimate?';
}

// --- the dependence chart --------------------------------------------------------

export function featureName(feature: string): string {
  return isFeature(feature) ? FEATURE[feature] : feature;
}

export function depLabel(label: string, customers: number): string {
  return `Contribution of ${label} against its value for ${int(customers)} explained customers`;
}

export function depNote(kind: 'numeric' | 'categorical', minCustomers: number): string {
  return kind === 'numeric'
    ? `Line: mean contribution in each equal-width value group; where a group is empty or has fewer than ${minCustomers} customers there is no mean, and the line breaks.`
    : `Bars: mean contribution per category, given from ${minCustomers} customers; dots are spread sideways only for legibility.`;
}

/** A category as the page writes it: "No internet", "Credit card (auto)", "Yes" for a senior. */
export function categoryText(feature: string, category: string): string {
  if (feature === 'InternetService' && category === 'No') return 'No internet';
  return valueText(feature, category)
    .replace('No internet service', 'No internet')
    .replace('No phone service', 'No phone');
}

/** A bound of a numeric group: months to one decimal, charges in cents. */
function bound(feature: string, value: number): string {
  return feature === 'tenure' ? String(Number(value.toFixed(1))) : cents(value);
}

function range(feature: string, group: Extract<Group, { kind: 'bin' }>): string {
  const low = bound(feature, group.low);
  const high = bound(feature, group.high);
  const unit = feature === 'tenure' ? ' months' : '';
  return group.first ? `${low} to ${high}${unit}` : `over ${low}, up to ${high}${unit}`;
}

/** A group as the table names it: its range, or its category. */
export function groupName(feature: string, group: Group): string {
  return group.kind === 'bin' ? range(feature, group) : categoryText(feature, group.category);
}

/** One group as the slider and its tooltip read it out. */
export function groupText(feature: string, group: Group): string {
  const head = `${featureName(feature)} ${groupName(feature, group)}`;
  if (group.customers === 0) return `${head}: none in the sample`;
  const who = `${int(group.customers)} ${group.customers === 1 ? 'customer' : 'customers'}`;
  return group.mean === null
    ? `${head}: ${who}, too few for a mean`
    : `${head}: ${who}, mean contribution ${signedPoints(group.mean)} pp`;
}

const TICK: Record<string, string> = {
  'Month-to-month': 'Monthly',
  'Fiber optic': 'Fiber',
  'Electronic check': 'E-check',
};

/** A category under the dependence chart, where there is little room. */
export function categoryTick(feature: string, category: string): string {
  return TICK[category] ?? categoryText(feature, category).replace(' (auto)', '');
}

/** Shown with Total charges only, hedged on the served training correlation. */
export function totalChargesNote(r: number | null): string | null {
  if (r === null) return null;
  return `Total charges track tenure × monthly charges in training (r = ${r.toFixed(3)}), so the explainer’s split of credit between the three can be arbitrary; read their contributions together.`;
}

// --- one customer ----------------------------------------------------------------

export function ringNote(id: string, global: GlobalExplanation): string {
  return `Ring: ${id}, explained on their own against the same ${int(global.n_background)} training customers. The dots are a cached sample of ${int(global.n_explained)} ${global.partition} customers; the ring is not one of them, even for a customer in the sample.`;
}
