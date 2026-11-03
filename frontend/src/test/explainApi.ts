/**
 * The explainability page's API, answered from fixtures.
 *
 * Twelve explained customers whose contributions are their own (no figure is
 * another's sum or mean at the digits shown), so a page that worked a figure
 * out instead of reading it would show another number. The dependence groups
 * are summarised here the way the server does it (twelve equal-width,
 * right-closed groups; a mean only from five customers) so the fixture is
 * self-consistent; the page itself never computes one.
 *
 * Kept on purpose:
 * - Paperless billing's first five contributions sit on rounding edges
 *   (+0.0004, −0.0004, +0.0005, −0.0005 and an exact 0) — one decimal of a
 *   percentage point shows "0.0", "0.0", "+0.1", "−0.1" and "0.0";
 * - "One year" has four customers (no mean), "Two year" two, and DSL four;
 * - Monthly charges leave empty groups between filled ones (a gap, not a line);
 * - the association ranking names seven features only.
 *
 * Nothing here is a real figure.
 */
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../api/client';
import type { CustomerDetail, ExplainResponse, GlobalExplanation } from '../api/queries';
import { FEATURE, FEATURES, type Feature } from '../lib/labels';
import { EXAMPLES, LINKED, README_RECORD } from './customerApi';
import { answer } from './server';

type Values = Record<Feature, string | number>;
type Dependence = GlobalExplanation['dependence'][number];

/** The artifact ``modelInfo()`` serves. */
export const ARTIFACT = 'a'.repeat(64);
export const BASE = 0.257639;
const MIN_CUSTOMERS = 5;
const BINS = 12;

/** The column contract's categories, in its order (``schema.ALLOWED_CATEGORIES``). */
export const VOCABULARY: Partial<Record<Feature, readonly string[]>> = {
  SeniorCitizen: ['0', '1'],
  Partner: ['Yes', 'No'],
  Dependents: ['Yes', 'No'],
  PhoneService: ['Yes', 'No'],
  MultipleLines: ['No', 'Yes', 'No phone service'],
  InternetService: ['DSL', 'Fiber optic', 'No'],
  OnlineSecurity: ['No', 'Yes', 'No internet service'],
  OnlineBackup: ['No', 'Yes', 'No internet service'],
  DeviceProtection: ['No', 'Yes', 'No internet service'],
  TechSupport: ['No', 'Yes', 'No internet service'],
  StreamingTV: ['No', 'Yes', 'No internet service'],
  StreamingMovies: ['No', 'Yes', 'No internet service'],
  Contract: ['Month-to-month', 'One year', 'Two year'],
  PaperlessBilling: ['Yes', 'No'],
  PaymentMethod: [
    'Electronic check',
    'Mailed check',
    'Bank transfer (automatic)',
    'Credit card (automatic)',
  ],
};

const NUMERIC: readonly Feature[] = ['tenure', 'MonthlyCharges', 'TotalCharges'];

const TENURE = [1, 2, 3, 5, 6, 12, 24, 36, 48, 60, 70, 72];
const MONTHLY = [20.05, 20.1, 20.2, 20.25, 20.35, 20.4, 45.5, 70.1, 85.3, 95.2, 104.8, 118.75];

function row(i: number): Values {
  const internet = i < 5 ? 'Fiber optic' : i < 9 ? 'DSL' : 'No';
  const addOn = (yes: boolean) => (internet === 'No' ? 'No internet service' : yes ? 'Yes' : 'No');
  return {
    SeniorCitizen: i % 4 === 0 ? '1' : '0',
    Partner: i % 2 === 0 ? 'Yes' : 'No',
    Dependents: i % 3 === 0 ? 'Yes' : 'No',
    tenure: TENURE[i] ?? 0,
    PhoneService: i === 7 ? 'No' : 'Yes',
    MultipleLines: i === 7 ? 'No phone service' : i % 2 === 0 ? 'Yes' : 'No',
    InternetService: internet,
    OnlineSecurity: addOn(i % 3 === 1),
    OnlineBackup: addOn(i % 2 === 1),
    DeviceProtection: addOn(i % 4 === 1),
    TechSupport: addOn(i % 5 === 1),
    StreamingTV: addOn(i % 2 === 0),
    StreamingMovies: addOn(i % 3 === 0),
    Contract: i < 6 ? 'Month-to-month' : i < 10 ? 'One year' : 'Two year',
    PaperlessBilling: i % 3 === 2 ? 'No' : 'Yes',
    PaymentMethod:
      i < 5
        ? 'Electronic check'
        : i < 8
          ? 'Mailed check'
          : i < 10
            ? 'Bank transfer (automatic)'
            : 'Credit card (automatic)',
    MonthlyCharges: MONTHLY[i] ?? 0,
    TotalCharges: Math.round((TENURE[i] ?? 0) * (MONTHLY[i] ?? 0) * 100 + i * 37) / 100,
  };
}

/** How strongly each feature moves the fixture's output (its rank, roughly). */
const WEIGHT: Record<Feature, number> = {
  tenure: 0,
  Contract: 0.091,
  InternetService: 0.073,
  MonthlyCharges: 0.052,
  PaymentMethod: 0.041,
  TotalCharges: 0.033,
  OnlineSecurity: 0.029,
  TechSupport: 0.023,
  OnlineBackup: 0.017,
  StreamingMovies: 0.013,
  StreamingTV: 0.011,
  DeviceProtection: 0.009,
  SeniorCitizen: 0.007,
  Dependents: 0.006,
  Partner: 0.005,
  MultipleLines: 0.004,
  PhoneService: 0.002,
  PaperlessBilling: 0.003,
};

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;
const PAPERLESS_EDGES = [0.0004, -0.0004, 0.0005, -0.0005, 0];

/** One explained customer's contribution of ``feature``: its own figure, deterministic. */
function contribution(i: number, feature: Feature, values: Values): number {
  if (feature === 'tenure') return round6(0.151337 - 0.004012 * Number(values.tenure));
  if (feature === 'PaperlessBilling' && i < PAPERLESS_EDGES.length) return PAPERLESS_EDGES[i] ?? 0;
  const j = FEATURES.indexOf(feature);
  const wave = (((i * 5 + j * 3) % 11) - 5) / 5 + 0.013 * ((i + j) % 3);
  return round6(WEIGHT[feature] * wave);
}

export const ROWS: readonly Values[] = TENURE.map((_, i) => row(i));
export const MATRIX: readonly (readonly number[])[] = ROWS.map((values, i) =>
  FEATURES.map((f) => contribution(i, f, values)),
);

function groupMean(members: readonly number[]): number | null {
  if (members.length < MIN_CUSTOMERS) return null;
  return round6(members.reduce((s, v) => s + v, 0) / members.length);
}

/** The server's summary of one column (see the header). */
function summarise(feature: Feature, j: number): Dependence {
  const pairs = ROWS.map((r, i) => [r[feature], MATRIX[i]?.[j] ?? 0] as const);
  const vocabulary = VOCABULARY[feature];
  if (vocabulary) {
    return {
      kind: 'categorical',
      feature,
      levels: vocabulary.map((category) => {
        const members = pairs.filter(([v]) => v === category).map(([, c]) => c);
        return { category, customers: members.length, mean: groupMean(members) };
      }),
    };
  }
  const numbers = pairs.map(([v]) => Number(v));
  const low = Math.min(...numbers);
  const high = Math.max(...numbers);
  const edges = Array.from({ length: BINS + 1 }, (_, k) => low + ((high - low) * k) / BINS);
  edges[BINS] = high;
  const binOf = (v: number) => {
    const k = edges.slice(1, -1).findIndex((edge) => v <= edge);
    return k === -1 ? BINS - 1 : k;
  };
  const bins = [];
  for (let k = 0; k < BINS; k++) {
    const members = pairs.filter(([v]) => binOf(Number(v)) === k).map(([, c]) => c);
    if (members.length === 0) continue;
    bins.push({
      low: edges[k] ?? low,
      high: edges[k + 1] ?? high,
      customers: members.length,
      mean: groupMean(members),
    });
  }
  return { kind: 'numeric', feature, bins };
}

export function globalExplanation(overrides: Partial<GlobalExplanation> = {}): GlobalExplanation {
  const meanAbs = FEATURES.map(
    (f, j) => [f, MATRIX.reduce((s, r) => s + Math.abs(r[j] ?? 0), 0) / MATRIX.length] as const,
  ).sort((a, b) => b[1] - a[1]);
  const total = meanAbs.reduce((s, [, m]) => s + m, 0);
  return {
    model_sha256: ARTIFACT,
    partition: 'validation',
    n_explained: ROWS.length,
    n_background: 100,
    permutations: 10,
    seed: 42,
    base_value: BASE,
    max_additivity_gap: 1.942890293094024e-15,
    features: [...FEATURES],
    labels: Object.fromEntries(FEATURES.map((f) => [f, FEATURE[f]])),
    importance: meanAbs.map(([feature, mean]) => ({
      feature,
      label: FEATURE[feature],
      mean_abs_shap: mean,
      share: mean / total,
    })),
    values: MATRIX.map((r) => [...r]),
    x: ROWS.map((r) => ({ ...r })),
    association_rank: {
      Contract: 1,
      OnlineSecurity: 2,
      tenure: 3,
      TechSupport: 4,
      InternetService: 5,
      PaymentMethod: 6,
      MonthlyCharges: 7,
    },
    dependence: FEATURES.map((f, j) => summarise(f, j)),
    dependence_min_customers: MIN_CUSTOMERS,
    total_charges_r: 0.8312,
    caveat: 'These are model attributions, not causal effects.',
    ...overrides,
  };
}

/** One book customer's contributions, strongest first: their own figures, not a row above. */
export function explanationOf(customer: CustomerDetail): Schemas['Explanation'] {
  const salt = Number(customer.id.replace(/\D/g, '')) % 7;
  const others = FEATURES.filter((f) => f !== 'tenure').map((feature) => {
    const j = FEATURES.indexOf(feature);
    const shap = round6(WEIGHT[feature] * ((((salt + j * 4) % 9) - 4) / 4 + 0.017));
    return {
      feature,
      label: FEATURE[feature],
      value: customer.features[feature],
      shap_value: shap,
    };
  });
  // Tenure takes what is left, so base + contributions is the customer's served probability.
  const rest = others.reduce((s, c) => s + c.shap_value, 0);
  const tenure = {
    feature: 'tenure',
    label: FEATURE.tenure,
    value: customer.features.tenure,
    shap_value: customer.churn_probability - BASE - rest,
  };
  return {
    customer_id: customer.id,
    base_value: BASE,
    churn_probability: customer.churn_probability,
    contributions: [tenure, ...others].sort(
      (a, b) => Math.abs(b.shap_value) - Math.abs(a.shap_value),
    ),
  };
}

export function explainResponse(
  explanations: Schemas['Explanation'][],
  overrides: Partial<ExplainResponse> = {},
): ExplainResponse {
  return {
    model_sha256: ARTIFACT,
    background_customers: 100,
    permutations: 10,
    seed: 42,
    explanations,
    caveat: 'These are model attributions, not causal effects.',
    ...overrides,
  };
}

/** The customers ``/api/explain`` knows: the examples and a test customer a link might open. */
export const KNOWN: readonly CustomerDetail[] = [...EXAMPLES, LINKED];

function notFound() {
  const body: Schemas['ErrorResponse'] = {
    error: 'not_found',
    detail: 'No customer with that ID.',
    problems: [],
  };
  return HttpResponse.json(body, { status: 404 });
}

export function explainApi({
  global = globalExplanation(),
  customers = EXAMPLES,
}: { global?: GlobalExplanation; customers?: readonly CustomerDetail[] } = {}) {
  return [
    answer('get', '/api/explain/global', global),
    answer('get', '/api/customers/examples', {
      readme_example: README_RECORD,
      customers: [...customers],
    }),
    http.post('/api/explain', async ({ request }) => {
      const body = (await request.json()) as Schemas['ExplainRequest'];
      const found = (body.customer_ids ?? []).map((id) => KNOWN.find((c) => c.id === id));
      if (found.length === 0 || found.some((c) => c === undefined)) return notFound();
      return HttpResponse.json(
        explainResponse(found.map((c) => explanationOf(c as CustomerDetail))),
      );
    }),
  ];
}

/** The numeric features, for tests that walk them. */
export { NUMERIC };
