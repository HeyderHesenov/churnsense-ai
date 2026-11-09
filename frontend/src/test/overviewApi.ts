/**
 * The executive overview's API, answered from the fixtures: the overview,
 * the queue's listing (and a selection by ids, whose total is a figure the
 * test can name), and explanations of whichever customers are asked for.
 */
import { matches } from './explorerApi';
import type { CustomerRow, Overview } from '../api/queries';
import { customerPage, customerRow, explanations, overview } from './overviewFixtures';
import { answer, respond } from './server';

/** Eight flagged customers, most revenue at risk first. */
export const QUEUE_ROWS: CustomerRow[] = Array.from({ length: 8 }, (_, i) =>
  customerRow(`DEMO-000${i + 1}`, { at_risk: 80 - i, churn_probability: 0.9 - i / 100 }),
);

/** What a selection's summary reports as its monthly revenue at risk. */
export const SELECTION_AT_RISK = 123.45;

/**
 * A queue listing that keeps the server's promises: the filters apply, and a band's
 * count is what choosing it would list. The fixture's eight rows stand for the whole
 * of a band the overview counts, until a partition or a search narrows it to them.
 */
function queuePage(book: Overview, query: URLSearchParams) {
  const chosen = query.getAll('bands');
  const others = new URLSearchParams(query);
  others.delete('bands');
  const scope = QUEUE_ROWS.filter((row) => matches(row, others));
  const narrowed = query.has('partition') || Boolean(query.get('q'));
  const counts = Object.fromEntries(
    book.bands.map((band) => [
      band.name,
      narrowed
        ? scope.filter((row) => row.risk_band === band.name).length
        : band.flagged_not_churned,
    ]),
  );
  const asked = chosen.length > 0 ? chosen : Object.keys(counts);
  const total = asked.reduce((sum, band) => sum + (counts[band] ?? 0), 0);
  const listed = narrowed
    ? scope.filter((row) => asked.includes(row.risk_band))
    : total === 0
      ? []
      : scope.map((row) => ({
          ...row,
          risk_band: asked.includes(row.risk_band) ? row.risk_band : (asked[0] ?? row.risk_band),
        }));
  return customerPage(listed, {
    total,
    pages: Math.max(1, Math.ceil(total / 8)),
    band_counts: counts,
    // A quarter status unknown: the page shows what the API says, whatever the split.
    status_counts: {
      active: total - Math.floor(total / 4),
      churned: 0,
      unknown: Math.floor(total / 4),
    },
  });
}

export function overviewApi(book: Overview = overview()) {
  return [
    answer('get', '/api/overview', book),
    respond('get', '/api/customers', (request) => {
      const query = new URL(request.url).searchParams;
      const ids = query.getAll('ids');
      if (ids.length === 0) return queuePage(book, query);
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
