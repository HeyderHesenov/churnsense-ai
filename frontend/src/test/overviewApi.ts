/**
 * The executive overview's API, answered from the fixtures: the overview,
 * the queue's listing (and a selection by ids, whose total is a figure the
 * test can name), and explanations of whichever customers are asked for.
 */
import type { CustomerRow, Overview } from '../api/queries';
import { customerPage, customerRow, explanations, overview } from './overviewFixtures';
import { answer, respond } from './server';

/** Eight flagged customers, most revenue at risk first. */
export const QUEUE_ROWS: CustomerRow[] = Array.from({ length: 8 }, (_, i) =>
  customerRow(`DEMO-000${i + 1}`, { at_risk: 80 - i, churn_probability: 0.9 - i / 100 }),
);

/** What a selection's summary reports as its monthly revenue at risk. */
export const SELECTION_AT_RISK = 123.45;

export function overviewApi(book: Overview = overview()) {
  return [
    answer('get', '/api/overview', book),
    respond('get', '/api/customers', (request) => {
      const query = new URL(request.url).searchParams;
      const ids = query.getAll('ids');
      if (ids.length === 0) {
        // The fixture's eight rows are all Critical; unfiltered, they stand
        // for the 2,300 flagged customers of the fixture's overview.
        const bands = query.getAll('bands');
        const rows = QUEUE_ROWS.filter(
          (row) => bands.length === 0 || bands.includes(row.risk_band),
        );
        return rows.length === QUEUE_ROWS.length
          ? customerPage(QUEUE_ROWS, { total: 2300, pages: 288 })
          : customerPage(rows);
      }
      const chosen = QUEUE_ROWS.filter((row) => ids.includes(row.id));
      const page = customerPage(chosen);
      return { ...page, summary: { ...page.summary, at_risk: SELECTION_AT_RISK } };
    }),
    respond('post', '/api/explain', async (request) => {
      const body = (await request.json()) as { customer_ids: string[] };
      return explanations(body.customer_ids);
    }),
  ];
}
