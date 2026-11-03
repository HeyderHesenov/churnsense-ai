/**
 * The customer explorer's API, answered from a small synthetic book: the
 * listing applies every filter, sorts (ties by ID, as the server does),
 * clamps the page and summarises the matches; one customer's detail; and
 * explanations. Thirty-seven customers make three pages of 15 (15, 15, 7).
 */
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../api/client';
import type { CustomerPage, CustomerRow, ExplainResponse } from '../api/queries';
import { customerDetail, customerFeatures } from './fixtures';
import { customerRow } from './overviewFixtures';
import { respond } from './server';

const CONTRACTS = ['Month-to-month', 'One year', 'Two year'] as const;
const INTERNET = ['Fiber optic', 'DSL', 'No'] as const;
const PAYMENTS = [
  'Electronic check',
  'Mailed check',
  'Bank transfer (automatic)',
  'Credit card (automatic)',
] as const;
const PARTITIONS = ['train', 'train', 'train', 'validation', 'test'] as const;

/** The fixture's served bands (``modelInfo``): 0.25, 0.5 and 0.75 apart. */
function band(p: number): string {
  return p < 0.25 ? 'Low' : p < 0.5 ? 'Moderate' : p < 0.75 ? 'High' : 'Critical';
}

/** DEMO-0001 is the riskiest, DEMO-0037 the least; the threshold (0.36) flags the first 24. */
export const BOOK: CustomerRow[] = Array.from({ length: 37 }, (_, i) => {
  const p = Number(((37 - i) / 38).toFixed(4));
  const monthly = 20 + ((i * 13) % 90);
  const partition = PARTITIONS[i % 5] ?? 'train';
  return customerRow(`DEMO-${String(i + 1).padStart(4, '0')}`, {
    contract: CONTRACTS[i % 3] ?? 'Month-to-month',
    internet_service: INTERNET[(i + 1) % 3] ?? 'DSL',
    payment_method: PAYMENTS[i % 4] ?? 'Mailed check',
    tenure: (i * 7) % 73,
    monthly_charges: monthly,
    churn_probability: p,
    risk_band: band(p),
    flagged: p >= 0.36,
    partition,
    outcome: partition === 'test' ? 'withheld' : p >= 0.5 ? 'churned' : 'stayed',
    at_risk: Number((p * monthly).toFixed(2)),
  });
});

const SORT_KEY = {
  id: (row: CustomerRow) => row.id,
  probability: (row: CustomerRow) => row.churn_probability,
  tenure: (row: CustomerRow) => row.tenure,
  monthly: (row: CustomerRow) => row.monthly_charges,
  at_risk: (row: CustomerRow) => row.at_risk,
} as const;

function matches(row: CustomerRow, query: URLSearchParams): boolean {
  const q = query.get('q');
  const bands = query.getAll('bands');
  const ids = query.getAll('ids');
  const min = query.get('tenure_min');
  const max = query.get('tenure_max');
  const flagged = query.get('flagged');
  return (
    (!q || row.id.toLowerCase().includes(q.toLowerCase())) &&
    (bands.length === 0 || bands.includes(row.risk_band)) &&
    (ids.length === 0 || ids.includes(row.id)) &&
    (!query.get('contract') || row.contract === query.get('contract')) &&
    (!query.get('internet') || row.internet_service === query.get('internet')) &&
    (!query.get('payment') || row.payment_method === query.get('payment')) &&
    (min === null || row.tenure >= Number(min)) &&
    (max === null || row.tenure <= Number(max)) &&
    (flagged === null || row.flagged === (flagged === 'true')) &&
    (!query.get('partition') || row.partition === query.get('partition'))
  );
}

function summarise(rows: CustomerRow[], book: CustomerRow[], withheld: boolean) {
  const n = rows.length;
  const flagged = rows.filter((r) => r.flagged).length;
  const seen = withheld
    ? []
    : rows.filter((r) => r.partition === 'train' || r.partition === 'validation');
  const churned = seen.filter((r) => r.outcome === 'churned').length;
  const sum = (pick: (r: CustomerRow) => number) => rows.reduce((total, r) => total + pick(r), 0);
  const histogram = Array.from({ length: 20 }, () => 0);
  for (const r of rows) {
    const bin = Math.min(19, Math.floor(r.churn_probability / 0.05));
    histogram[bin] = (histogram[bin] ?? 0) + 1;
  }
  return {
    customers: n,
    share_of_book: n / book.length,
    flagged,
    flagged_share: n ? flagged / n : null,
    mean_probability: n ? sum((r) => r.churn_probability) / n : null,
    expected_churners: sum((r) => r.churn_probability),
    observed:
      seen.length === 0
        ? null
        : seen.length < 20
          ? { customers: seen.length, churned: null, rate: null }
          : { customers: seen.length, churned, rate: churned / seen.length },
    monthly_charges: sum((r) => r.monthly_charges),
    at_risk: sum((r) => r.at_risk),
    histogram,
  };
}

/** The listing the API would give for ``url``. */
export function listing(url: URL, book = BOOK, withheld: string | null = null): CustomerPage {
  const query = url.searchParams;
  const sort = (query.get('sort') ?? 'probability') as keyof typeof SORT_KEY;
  const key = SORT_KEY[sort];
  const sign = query.get('dir') === 'asc' ? 1 : -1;
  const rows = book
    .filter((row) => matches(row, query))
    .sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      if (ka < kb) return -sign;
      if (ka > kb) return sign;
      return a.id < b.id ? -1 : 1;
    });
  const size = Number(query.get('page_size') ?? 25);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.min(Math.max(1, Number(query.get('page') ?? 1)), pages);
  return {
    total: rows.length,
    page,
    page_size: size,
    pages,
    rows: rows.slice((page - 1) * size, page * size),
    summary: summarise(rows, book, withheld !== null),
    withheld_reason: withheld,
  };
}

/** Nine attributions, strongest first, as the API orders them. */
export const CONTRIBUTIONS: Schemas['Explanation']['contributions'] = [
  { feature: 'tenure', label: 'Tenure (months)', value: 2, shap_value: 0.317 },
  { feature: 'MonthlyCharges', label: 'Monthly charges', value: 90, shap_value: -0.209 },
  { feature: 'Contract', label: 'Contract type', value: 'Month-to-month', shap_value: 0.12 },
  { feature: 'InternetService', label: 'Internet service', value: 'Fiber optic', shap_value: 0.08 },
  {
    feature: 'PaymentMethod',
    label: 'Payment method',
    value: 'Electronic check',
    shap_value: 0.05,
  },
  { feature: 'TechSupport', label: 'Tech support', value: 'No', shap_value: 0.03 },
  { feature: 'OnlineSecurity', label: 'Online security', value: 'No', shap_value: -0.02 },
  { feature: 'PaperlessBilling', label: 'Paperless billing', value: 'Yes', shap_value: 0.01 },
  { feature: 'SeniorCitizen', label: 'Senior citizen', value: '0', shap_value: -0.004 },
];

export function explanation(ids: readonly string[]): ExplainResponse {
  return {
    model_sha256: 'a'.repeat(64),
    background_customers: 100,
    permutations: 10,
    seed: 42,
    caveat: 'These are model attributions, not causal effects.',
    explanations: ids.map((id) => ({
      customer_id: id,
      base_value: 0.265,
      churn_probability: BOOK.find((row) => row.id === id)?.churn_probability ?? 0.8,
      contributions: CONTRIBUTIONS,
    })),
  };
}

interface ExplorerApiOptions {
  book?: CustomerRow[];
  /** The provenance reason every outcome is withheld for, if it is. */
  withheld?: string | null;
}

export function explorerApi({ book = BOOK, withheld = null }: ExplorerApiOptions = {}) {
  return [
    respond('get', '/api/customers', (request) => listing(new URL(request.url), book, withheld)),
    http.get('/api/customers/:customer_id', ({ params }) => {
      const row = book.find((r) => r.id === params.customer_id);
      if (!row) {
        const body: Schemas['ErrorResponse'] = {
          error: 'not_found',
          detail: 'No customer with that ID.',
          problems: [],
        };
        return HttpResponse.json(body, { status: 404 });
      }
      return HttpResponse.json(
        customerDetail(row.id, {
          features: customerFeatures({
            tenure: row.tenure,
            Contract: row.contract as Schemas['CustomerFeatures']['Contract'],
            InternetService: row.internet_service as Schemas['CustomerFeatures']['InternetService'],
            PaymentMethod: row.payment_method as Schemas['CustomerFeatures']['PaymentMethod'],
            MonthlyCharges: row.monthly_charges,
            TotalCharges: Number((row.monthly_charges * Math.max(row.tenure, 1)).toFixed(2)),
          }),
          churn_probability: row.churn_probability,
          risk_band: row.risk_band,
          flagged: row.flagged,
          partition: row.partition,
          outcome: row.outcome,
        }),
      );
    }),
    respond('post', '/api/explain', async (request) => {
      const body = (await request.json()) as { customer_ids: string[] };
      return explanation(body.customer_ids);
    }),
  ];
}
