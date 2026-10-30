/**
 * The batch page's API, answered from fixtures: thirty scored rows of a
 * synthetic file (DEMO- IDs), a third of them flagged, every one labelled but
 * a held-out test customer's. The summary is worked out from the rows, so the
 * tiles and the table agree as the server's do.
 */
import type { BatchScore } from '../api/queries';
import { customerFeatures } from './fixtures';
import { respond } from './server';

export type BatchRow = BatchScore['rows'][number];

const CONTRACTS = ['Month-to-month', 'One year', 'Two year'] as const;

/** The fixture's served bands (``modelInfo``): 0.25, 0.5 and 0.75 apart. */
function band(p: number): string {
  return p < 0.25 ? 'Low' : p < 0.5 ? 'Moderate' : p < 0.75 ? 'High' : 'Critical';
}

/** The row whose customer is in the held-out test partition: its label is withheld. */
export const HELD_OUT = 7;

/** Row ``row`` of the file: probabilities spread over the scale, none repeated. */
export function batchRow(row: number, overrides: Partial<BatchRow> = {}): BatchRow {
  const p = ((row * 37) % 100) / 100 + 0.005;
  return {
    row,
    customer_id: `DEMO-${String(row).padStart(5, '0')}`,
    label: row === HELD_OUT ? null : row % 3 === 0 ? 1 : 0,
    features: customerFeatures({
      tenure: row + 1,
      Contract: CONTRACTS[row % 3] ?? 'Month-to-month',
      MonthlyCharges: 20 + row * 3.25,
      TotalCharges: (row + 1) * (20 + row * 3.25),
    }),
    churn_probability: p,
    risk_band: band(p),
    flagged: p >= 0.36,
    ...overrides,
  };
}

export const ROWS: BatchRow[] = Array.from({ length: 30 }, (_, row) => batchRow(row));

/** The server's scored file for ``rows``, as ``csv`` carries it. */
export function scoredCsv(rows: readonly BatchRow[]): string {
  const lines = rows.map((r) =>
    [
      r.row,
      r.customer_id ?? '',
      r.churn_probability,
      r.risk_band,
      r.flagged ? 'True' : 'False',
    ].join(','),
  );
  return ['row,customerID,churn_probability,risk_band,flagged', ...lines].join('\n') + '\n';
}

/** The server's label check (``_label_check``): no rate under 20 labelled rows, and why a rate is missing, in its words. */
function labelCheck(rows: readonly BatchRow[]): BatchScore['summary']['labels'] {
  const labelled = rows.filter((r) => r.label !== null);
  const excluded_test = rows.length - labelled.length;
  if (labelled.length < 20) {
    const reason = 'A rate needs at least 20 labelled rows.';
    return { labelled: labelled.length, excluded_test, precision: null, recall: null, reason };
  }
  const tp = labelled.filter((r) => r.flagged && r.label === 1).length;
  const flagged = labelled.filter((r) => r.flagged).length;
  const churned = labelled.filter((r) => r.label === 1).length;
  const precision = flagged ? tp / flagged : null;
  const recall = churned ? tp / churned : null;
  const reason =
    precision === null && recall === null
      ? 'No labelled customer was flagged or churned, so neither rate is defined.'
      : precision === null
        ? 'No labelled customer was flagged, so precision is not defined.'
        : recall === null
          ? 'No labelled customer churned, so recall is not defined.'
          : null;
  return { labelled: labelled.length, excluded_test, precision, recall, reason };
}

/** The response for ``rows``, its summary worked out from them. */
export function batchScore(
  rows: readonly BatchRow[] = ROWS,
  overrides: Partial<BatchScore> = {},
): BatchScore {
  const flagged = rows.filter((r) => r.flagged).length;
  const sum = rows.reduce((total, r) => total + r.churn_probability, 0);
  return {
    columns: 21,
    threshold: 0.36,
    rows: [...rows],
    summary: {
      rows: rows.length,
      flagged,
      flagged_share: rows.length ? flagged / rows.length : null,
      mean_probability: rows.length ? sum / rows.length : null,
      expected_churners: sum,
      at_risk: rows.reduce(
        (t, r) => t + r.churn_probability * Number(r.features.MonthlyCharges),
        0,
      ),
      bands: ['Low', 'Moderate', 'High', 'Critical'].map((name) => ({
        name,
        customers: rows.filter((r) => r.risk_band === name).length,
      })),
      labels: labelCheck(rows),
      labels_reason: null,
    },
    notes: [
      'gender is ignored: excluded from the model as a protected attribute',
      'Churn is never used to score; it is kept only to compare against the predictions',
    ],
    csv: scoredCsv(rows),
    ...overrides,
  };
}

/** ``POST /api/batch/score`` answered with ``response``, whatever the file. */
export function batchApi(response: BatchScore = batchScore()) {
  return [respond('post', '/api/batch/score', () => response)];
}
