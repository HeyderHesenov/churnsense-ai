/**
 * The single-customer page's state, and the arrangement of what it draws.
 *
 * The address says who is shown: a customer in the book (``id``, what an
 * "Explain" link opens), the new customer typed in on the page (``live``),
 * or, with neither, the README example. What is typed stays in memory; only
 * the choice is in the address.
 *
 * Every figure is the API's. The arithmetic here only places them: the
 * waterfall's running sum of one customer's own attributions, and the total
 * charges a box can be filled with (tenure times monthly charges).
 */
import type { CustomerFeatures, Explanation, ModelInfo } from '../../api/queries';
import { param, type Values } from '../../app/params';
import { cents, months } from '../../lib/format';
import { categoryValue } from '../../lib/labels';
import { MAX_CUSTOMER_ID } from '../../lib/customerId';

export const CUSTOMER_PARAMS = {
  /** A customer in the book; empty when none is chosen. */
  id: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: MAX_CUSTOMER_ID }),
  /** The new customer typed in on the page. */
  live: param.optional(['true'] as const),
};

export type CustomerState = Values<typeof CUSTOMER_PARAMS>;

/** Who the page shows. */
export type Shown = { kind: 'readme' } | { kind: 'book'; id: string } | { kind: 'live' };

export function shownBy(state: CustomerState): Shown {
  if (state.live === 'true') return { kind: 'live' };
  return state.id ? { kind: 'book', id: state.id } : { kind: 'readme' };
}

/** The change of address that shows ``shown``: its key set, the other cleared. */
export function showChanges(shown: Shown): Partial<CustomerState> {
  if (shown.kind === 'live') return { id: '', live: 'true' };
  return { id: shown.kind === 'book' ? shown.id : '', live: null };
}

/** A name for a choice that is unique among the picker's: a key, and a part of an element id. */
export function choiceKey(shown: Shown): string {
  return shown.kind === 'book' ? `id-${shown.id}` : shown.kind;
}

/**
 * The picker's cards, in order: a customer a link opened (``linked``, unless
 * it is one of the examples), the README example, the examples, the new
 * customer.
 */
export function pickerChoices(examples: readonly string[], linked: string | null): Shown[] {
  const opened: Shown[] =
    linked !== null && !examples.includes(linked) ? [{ kind: 'book', id: linked }] : [];
  return [
    ...opened,
    { kind: 'readme' },
    ...examples.map((id): Shown => ({ kind: 'book', id })),
    { kind: 'live' },
  ];
}

// --- the live form ---------------------------------------------------------------

/** The inputs typed as numbers, in the form's order. */
export const AMOUNTS = ['tenure', 'MonthlyCharges', 'TotalCharges'] as const;
export type Amount = (typeof AMOUNTS)[number];

/** The form as typed: the three amounts as the text in their boxes. */
export type Draft = Omit<CustomerFeatures, Amount> & Record<Amount, string>;

/** The amounts the API accepts (``/model-info``); ``undefined`` until they are in. */
export type Bounds = NonNullable<ModelInfo['numeric_bounds']>;

/** The services that need an internet service. */
export const ADD_ONS = [
  'OnlineSecurity',
  'OnlineBackup',
  'DeviceProtection',
  'TechSupport',
  'StreamingTV',
  'StreamingMovies',
] as const;

const DIGITS = /^\d*$/;

/**
 * Digits only for tenure; digits and decimal points otherwise ("95", "95.5",
 * ".5"). A second point reads as no number at all (``Number`` refuses it).
 */
function readsAsAmount(key: Amount, text: string): boolean {
  const parts = key === 'tenure' ? [text] : text.split('.');
  return parts.every((part) => DIGITS.test(part)) && parts.join('') !== '';
}

/** ``text`` as the number the API would be sent, or ``null`` if it does not read as one. */
function amount(key: Amount, text: string): number | null {
  if (!readsAsAmount(key, text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function draftOf(record: CustomerFeatures): Draft {
  return {
    ...record,
    tenure: String(record.tenure),
    MonthlyCharges: String(record.MonthlyCharges),
    TotalCharges: String(record.TotalCharges),
  };
}

/**
 * The form with the data contract's own rules applied, as the artboard
 * applies them: no internet service means no add-on, no phone service means
 * no multiple lines, and no tenure means nothing billed. The API refuses a
 * record that breaks them; the form keeps to them so it never sends one.
 */
export function coherent(draft: Draft): Draft {
  const next = { ...draft };
  const internet = draft.InternetService !== 'No';
  for (const key of ADD_ONS) {
    if (!internet) next[key] = 'No internet service';
    else if (next[key] === 'No internet service') next[key] = 'No';
  }
  if (draft.PhoneService === 'No') next.MultipleLines = 'No phone service';
  else if (next.MultipleLines === 'No phone service') next.MultipleLines = 'No';
  if (noTenure(draft)) next.TotalCharges = '0';
  return next;
}

/**
 * Why an amount cannot be sent: it does not read as a number (``format``),
 * it is outside the API's bounds (``range``), or total charges are zero for
 * a customer with tenure (``billed``: the data contract has nothing billed
 * exactly when tenure is zero).
 */
export type AmountProblem =
  | { field: Amount; reason: 'format' | 'billed' }
  | { field: Amount; reason: 'range'; min: number; max: number };

/** A customer with no tenure yet: nothing is billed, so total charges are fixed at zero. */
export function noTenure(draft: Draft): boolean {
  return amount('tenure', draft.tenure) === 0;
}

/** The amounts that cannot be sent as typed, in the form's order; the bounds once they are in. */
export function amountProblems(draft: Draft, bounds: Bounds | undefined): AmountProblem[] {
  const problems: AmountProblem[] = [];
  for (const field of AMOUNTS) {
    const value = amount(field, draft[field]);
    const range = bounds?.[field];
    if (value === null) problems.push({ field, reason: 'format' });
    else if (range && (value < range.min || value > range.max))
      problems.push({ field, reason: 'range', min: range.min, max: range.max });
  }
  const tenure = amount('tenure', draft.tenure);
  if (tenure !== null && tenure > 0 && amount('TotalCharges', draft.TotalCharges) === 0)
    problems.push({ field: 'TotalCharges', reason: 'billed' });
  return problems;
}

/**
 * The record the form reads as, or ``null`` while an amount cannot be read or
 * nothing is billed with a tenure. The API's bounds are checked apart
 * (``amountProblems``): they come in later than the form.
 */
export function recordOf(draft: Draft): CustomerFeatures | null {
  const tenure = amount('tenure', draft.tenure);
  const monthly = amount('MonthlyCharges', draft.MonthlyCharges);
  const total = amount('TotalCharges', draft.TotalCharges);
  if (tenure === null || monthly === null || total === null) return null;
  if (amountProblems(draft, undefined).length > 0) return null;
  return { ...draft, tenure, MonthlyCharges: monthly, TotalCharges: total };
}

/** Tenure times monthly charges, to the cent: what "=×" fills total charges with. */
export function estimatedTotal(draft: Draft): string | null {
  const tenure = amount('tenure', draft.tenure);
  const monthly = amount('MonthlyCharges', draft.MonthlyCharges);
  if (tenure === null || monthly === null) return null;
  return String(Math.round(tenure * monthly * 100) / 100);
}

// --- the profile -------------------------------------------------------------------

type Feature = keyof CustomerFeatures;

/** The profile's groups, in the artboard's order. */
export const GROUPS: readonly { title: string; features: readonly Feature[] }[] = [
  {
    title: 'Account',
    features: [
      'tenure',
      'Contract',
      'MonthlyCharges',
      'TotalCharges',
      'PaymentMethod',
      'PaperlessBilling',
    ],
  },
  {
    title: 'Services',
    features: [
      'PhoneService',
      'MultipleLines',
      'InternetService',
      'OnlineSecurity',
      'OnlineBackup',
      'DeviceProtection',
      'TechSupport',
      'StreamingTV',
      'StreamingMovies',
    ],
  },
  { title: 'Household', features: ['SeniorCitizen', 'Partner', 'Dependents'] },
];

/** One input's value as the profile writes it: "3 months", "$95.00", "Credit card (auto)". */
export function formatValue(feature: string, value: string | number): string {
  if (feature === 'tenure') return months(Number(value));
  if (feature === 'MonthlyCharges' || feature === 'TotalCharges') return cents(Number(value));
  if (feature === 'SeniorCitizen') return String(value) === '1' ? 'Yes' : 'No';
  return categoryValue(String(value));
}

export function featureValue(feature: Feature, features: CustomerFeatures): string {
  return formatValue(feature, features[feature]);
}

/** The inputs behind the strongest attributions, which the profile highlights. */
export function strongest(explanation: Explanation | undefined, count = 4): ReadonlySet<string> {
  return new Set(explanation?.contributions.slice(0, count).map((c) => c.feature));
}

// --- the waterfall -----------------------------------------------------------------

export interface Step {
  feature: string;
  label: string;
  value: string | number;
  shap: number;
  /** Where the running sum stands before and after this attribution. */
  start: number;
  end: number;
}

export interface Waterfall {
  base: number;
  steps: Step[];
  /** The attributions not shown one by one, summed; ``null`` when every one is shown. */
  rest: { count: number; shap: number; start: number; end: number } | null;
  /** The served probability, where the last bar ends. */
  total: number;
}

/** The strongest ``shown`` attributions one by one from the base, then the rest as one. */
export function waterfall(explanation: Explanation, shown: number): Waterfall {
  let run = explanation.base_value;
  const steps = explanation.contributions.slice(0, shown).map((c): Step => {
    const start = run;
    run += c.shap_value;
    return {
      feature: c.feature,
      label: c.label,
      value: c.value,
      shap: c.shap_value,
      start,
      end: run,
    };
  });
  const others = explanation.contributions.slice(shown);
  const shap = others.reduce((sum, c) => sum + c.shap_value, 0);
  return {
    base: explanation.base_value,
    steps,
    rest: others.length > 0 ? { count: others.length, shap, start: run, end: run + shap } : null,
    total: explanation.churn_probability,
  };
}

/** The thinnest bar drawn, as a share of the scale: the artboard's 0.4 %. */
const THINNEST = 0.004;

/** A bar from ``start`` to ``end`` on the 0-1 scale: never too thin to see, never past an end. */
export function bar(start: number, end: number): { left: number; width: number } {
  const low = Math.max(0, Math.min(start, end));
  const high = Math.min(1, Math.max(start, end));
  const width = Math.max(THINNEST, high - low);
  return { left: Math.min(low, 1 - width), width };
}
