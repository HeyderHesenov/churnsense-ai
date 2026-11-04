/**
 * The patterns page's responses, complete and typed by the schema. Small
 * round numbers, so a test states the expected text by hand. The cases are
 * kept on purpose: a cell hidden for being small, one held back beside it,
 * an empty one, a two-way view whose dimension the explorer cannot filter,
 * and a gender row the page must never name.
 */
import type { Schemas } from '../api/client';
import type { Cell, Segments } from '../api/queries';
import { respond } from './server';

/** A reported group; its interval is given, never derived, so tests can tell it apart. */
export function reported(
  row: string,
  column: string | null,
  n: number,
  churned: number,
  base = 0.25,
): Schemas['ReportedCell'] {
  const rate = churned / n;
  return {
    row,
    column,
    n,
    churned,
    rate,
    lift: rate / base,
    rate_low: Number((rate - 0.031).toFixed(3)),
    rate_high: Number((rate + 0.029).toFixed(3)),
    suppressed: false,
  };
}

export function hidden(
  row: string,
  column: string | null,
  n: number,
  reason: Schemas['SuppressedCell']['reason'] = n < 20 ? 'too_few' : 'complementary',
): Cell {
  return { row, column, n, reason, suppressed: true };
}

const ASSOCIATION: Schemas['Association'][] = [
  { feature: 'Contract', measure: "Cramer's V", strength: 0.419 },
  { feature: 'tenure', measure: 'point-biserial |r|', strength: 0.353 },
  { feature: 'OnlineBackup', measure: "Cramer's V", strength: 0.296 },
  { feature: 'MonthlyCharges', measure: 'point-biserial |r|', strength: 0.196 },
  { feature: 'gender', measure: "Cramer's V", strength: 0.0 },
];

/** Contract by tenure: 4,000 training customers, 1,000 churned, base 25.0%. */
export function contractByTenure(overrides: Partial<Segments> = {}): Segments {
  return {
    rows: 'Contract',
    columns: 'tenure',
    book_customers: 7000,
    partition_customers: 4000,
    partition_churned: 1000,
    min_customers: 20,
    base_rate: 0.25,
    row_totals: [
      reported('Month-to-month', null, 2000, 800),
      reported('One year', null, 1000, 150),
      reported('Two year', null, 1000, 50),
    ],
    column_totals: [reported('0-3', null, 1000, 420), reported('4-72', null, 3000, 580)],
    cells: [
      hidden('Month-to-month', '0-3', 960, 'complementary'),
      reported('Month-to-month', '4-72', 1040, 280),
      hidden('One year', '0-3', 12, 'too_few'),
      reported('One year', '4-72', 988, 148),
      hidden('Two year', '0-3', 0, 'too_few'),
      reported('Two year', '4-72', 1000, 50),
    ],
    ranked: [
      reported('Month-to-month', '4-72', 1040, 280),
      reported('One year', '4-72', 988, 148),
      reported('Two year', '4-72', 1000, 50),
    ],
    association: ASSOCIATION,
    ...overrides,
  };
}

/** Contract alone: no column dimension. */
export function contractAlone(overrides: Partial<Segments> = {}): Segments {
  const base = contractByTenure();
  return {
    ...base,
    columns: null,
    column_totals: [],
    cells: base.row_totals,
    ranked: [
      reported('Month-to-month', null, 2000, 800),
      reported('One year', null, 1000, 150),
      reported('Two year', null, 1000, 50),
    ],
    ...overrides,
  };
}

/** Monthly charges by streaming TV: neither filterable in the explorer. */
export function chargesByStreaming(): Segments {
  return {
    ...contractByTenure(),
    rows: 'monthly',
    columns: 'StreamingTV',
    row_totals: [reported('<$35', null, 1500, 150), reported('$35+', null, 2500, 850)],
    column_totals: [reported('No', null, 2400, 500), reported('Yes', null, 1600, 500)],
    cells: [
      reported('<$35', 'No', 1400, 140),
      hidden('<$35', 'Yes', 100, 'complementary'),
      hidden('$35+', 'No', 1000, 'complementary'),
      reported('$35+', 'Yes', 1500, 490),
    ],
    ranked: [reported('$35+', 'Yes', 1500, 490), reported('<$35', 'No', 1400, 140)],
  };
}

/**
 * Answers ``/api/segments`` by its ``rows`` and ``cols``: Contract by tenure,
 * Contract alone, monthly by streaming TV; anything else as Contract by tenure
 * relabelled, so every view has an answer.
 */
export function patternsApi(views: Record<string, Segments> = {}) {
  const known: Record<string, Segments> = {
    'Contract|tenure': contractByTenure(),
    'Contract|': contractAlone(),
    'monthly|StreamingTV': chargesByStreaming(),
    ...views,
  };
  return [
    respond('get', '/api/segments', (request) => {
      const url = new URL(request.url);
      const rows = url.searchParams.get('rows') ?? 'Contract';
      const cols = url.searchParams.get('cols') ?? '';
      return known[`${rows}|${cols}`] ?? { ...contractByTenure(), rows, columns: cols || null };
    }),
  ];
}
