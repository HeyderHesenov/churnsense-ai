/**
 * The single-customer page's API, answered from fixtures: the README example
 * and four validation examples, one more customer in the book (a test one,
 * its outcome withheld), predictions of any record, and explanations of
 * book customers or of a record. Explanations add up: the base plus every
 * attribution is the probability served for the same customer.
 */
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../api/client';
import type { CustomerDetail, CustomerFeatures, ExplainResponse, Prediction } from '../api/queries';
import { customerDetail, customerFeatures } from './fixtures';
import { answer, respond } from './server';

/** The README's example customer (`src/churnsense/data/schema.py`), not in the book. */
export const README_RECORD: CustomerFeatures = customerFeatures({
  tenure: 3,
  StreamingTV: 'Yes',
  StreamingMovies: 'Yes',
  MonthlyCharges: 95,
  TotalCharges: 285,
});

/** What the served model gives the README example. */
export const README_PROBABILITY = 0.8029793735676088;

/** The average training customer the attributions start from. */
export const BASE = 0.25764;

/** The fixture's served bands (``modelInfo``): 0.25, 0.5 and 0.75 apart. */
function band(p: number): string {
  return p < 0.25 ? 'Low' : p < 0.5 ? 'Moderate' : p < 0.75 ? 'High' : 'Critical';
}

/** The four example cases, as the rule picks them: Low and Moderate unflagged, Moderate and High flagged. */
export const EXAMPLES: CustomerDetail[] = [
  ['DEMO-0005', 0.030126, 'stayed', { tenure: 65, Contract: 'Two year' }],
  ['DEMO-0006', 0.290672, 'stayed', { tenure: 38 }],
  ['DEMO-0007', 0.419123, 'churned', { tenure: 43 }],
  ['DEMO-0008', 0.626952, 'stayed', { tenure: 61 }],
].map(([id, p, outcome, features]) =>
  customerDetail(id as string, {
    features: customerFeatures(features as Partial<CustomerFeatures>),
    churn_probability: p as number,
    risk_band: band(p as number),
    flagged: (p as number) >= 0.36,
    partition: 'validation',
    outcome: outcome as CustomerDetail['outcome'],
  }),
);

/** A test customer an "Explain" link might open: in the book, but not an example. */
export const LINKED: CustomerDetail = customerDetail('DEMO-0042', {
  features: customerFeatures({ tenure: 9, MonthlyCharges: 70.35, TotalCharges: 633.15 }),
  churn_probability: 0.55,
  risk_band: 'High',
  flagged: true,
  partition: 'test',
  outcome: 'withheld',
});

const BOOK = [...EXAMPLES, LINKED];

/** The served probability of a record: the README example's, else a rule of its tenure. */
export function probabilityOf(record: CustomerFeatures): number {
  const book = BOOK.find((c) => JSON.stringify(c.features) === JSON.stringify(record));
  if (book) return book.churn_probability;
  if (JSON.stringify(record) === JSON.stringify(README_RECORD)) return README_PROBABILITY;
  return Math.min(0.95, Math.max(0.02, 0.9 - record.tenure / 100));
}

export function prediction(record: CustomerFeatures, threshold = 0.36): Prediction {
  const p = probabilityOf(record);
  return {
    churn_probability: p,
    risk_band: band(p),
    flagged: p >= threshold,
    threshold,
    model_key: 'logistic_regression',
    caveat:
      "A churn probability is an estimate of similarity to past churners, not a prediction of an individual's intent, and not a causal statement.",
  };
}

/** The artboard's README attributions, in percentage points, strongest first. */
const SHAPE: [keyof CustomerFeatures, string, number][] = [
  ['tenure', 'Tenure (months)', 30.8],
  ['MonthlyCharges', 'Monthly charges', -16.09],
  ['InternetService', 'Internet service', 16.05],
  ['TotalCharges', 'Total charges to date', -9.14],
  ['StreamingTV', 'Streaming TV', 7.7],
  ['StreamingMovies', 'Streaming movies', 7.57],
  ['Contract', 'Contract type', 7.42],
  ['MultipleLines', 'Multiple lines', -5.3],
  ['PaymentMethod', 'Payment method', 3.8],
  ['TechSupport', 'Tech support add-on', 2.9],
  ['OnlineSecurity', 'Online security add-on', 2.89],
  ['PaperlessBilling', 'Paperless billing', 2.81],
  ['Dependents', 'Has dependents', 1.52],
  ['OnlineBackup', 'Online backup add-on', 1.04],
  ['SeniorCitizen', 'Senior citizen', -0.65],
  ['DeviceProtection', 'Device protection add-on', 0.45],
  ['Partner', 'Has partner', 0.43],
  ['PhoneService', 'Phone service', 0.33],
];
const SHAPE_SUM = SHAPE.reduce((sum, [, , pp]) => sum + pp / 100, 0);

/** All 18 attributions of ``record``, scaled so that they add up to its probability. */
export function explained(
  record: CustomerFeatures,
  id: string | null,
  p = probabilityOf(record),
): Schemas['Explanation'] {
  const scale = (p - BASE) / SHAPE_SUM;
  return {
    customer_id: id,
    base_value: BASE,
    churn_probability: p,
    contributions: SHAPE.map(([feature, label, pp]) => ({
      feature,
      label,
      value: record[feature],
      shap_value: (pp / 100) * scale,
    })),
  };
}

export function explainResponse(explanations: Schemas['Explanation'][]): ExplainResponse {
  return {
    model_sha256: 'a'.repeat(64),
    background_customers: 100,
    background: 'training',
    permutations: 10,
    seed: 42,
    caveat: 'These are model attributions, not causal effects.',
    explanations,
  };
}

function notFound() {
  const body: Schemas['ErrorResponse'] = {
    error: 'not_found',
    detail: 'No customer with that ID.',
    problems: [],
  };
  return HttpResponse.json(body, { status: 404 });
}

export function customerApi({ customers = EXAMPLES }: { customers?: CustomerDetail[] } = {}) {
  return [
    answer('get', '/api/customers/examples', { readme_example: README_RECORD, customers }),
    http.get('/api/customers/:customer_id', ({ params }) => {
      const found = BOOK.find((c) => c.id === params.customer_id);
      return found ? HttpResponse.json(found) : notFound();
    }),
    respond('post', '/api/predict', async (request) =>
      prediction((await request.json()) as CustomerFeatures),
    ),
    http.post('/api/explain', async ({ request }) => {
      const body = (await request.json()) as Schemas['ExplainRequest'];
      if (body.record) return HttpResponse.json(explainResponse([explained(body.record, null)]));
      const ids = body.customer_ids ?? [];
      const found = ids.map((id) => BOOK.find((c) => c.id === id));
      if (found.some((c) => c === undefined)) return notFound();
      return HttpResponse.json(
        explainResponse(
          found.map((c) => explained((c as CustomerDetail).features, (c as CustomerDetail).id)),
        ),
      );
    }),
  ];
}
