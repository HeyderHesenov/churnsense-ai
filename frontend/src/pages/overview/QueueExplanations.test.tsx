/**
 * The retention queue's explanation requests as the reader pages, sorts and
 * leaves: a page or sort the reader moved on from has its request aborted and
 * its answer never shown on the rows that replaced it; quick changes ask only
 * for the page the reader stops at; a request left behind by leaving the page
 * still outlives it (src/api/queries.ts, ``outlive``).
 *
 * Each page's explanation carries its own tenure attribution (page 1: +10.0 pp,
 * page 2: +20.0 pp…), so an answer shown on the wrong rows would be seen.
 */
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../../app/App';
import { overviewApi } from '../../test/overviewApi';
import { customerPage, customerRow, explanations } from '../../test/overviewFixtures';
import { renderAt } from '../../test/render';
import { recordRequests, respond, server } from '../../test/server';
import { SETTLE_MS } from './RetentionQueue';

const PAGES = 5;
const SORT_CODE: Record<string, string> = {
  at_risk: 'R',
  tenure: 'T',
  monthly: 'M',
  probability: 'P',
};

/** Eight rows per page and sort, their ids naming both: "DEMO-R2-03" is row 3 of page 2 by revenue at risk. */
function rowsOf(sort: string, page: number) {
  return Array.from({ length: 8 }, (_, i) =>
    customerRow(`DEMO-${SORT_CODE[sort] ?? 'X'}${String(page)}-0${String(i + 1)}`, {
      at_risk: 80 - i,
    }),
  );
}

/** The page an explanation request is for, read from its first id. */
function pageOf(ids: readonly string[]) {
  return Number(/^DEMO-\w(\d)-/.exec(ids[0] ?? '')?.[1] ?? 0);
}

interface Held {
  ids: string;
  signal: AbortSignal;
}

/** The queue's API with a listing per page and sort; ``hold`` keeps explanations unanswered until released. */
function queueApi({ hold = false } = {}) {
  const requests: Held[] = [];
  let release = () => {};
  const opened = hold
    ? new Promise<void>((resolve) => {
        release = resolve;
      })
    : Promise.resolve();
  server.use(...overviewApi());
  server.use(
    respond('get', '/api/customers', (request) => {
      const query = new URL(request.url).searchParams;
      const page = Number(query.get('page') ?? '1');
      return customerPage(rowsOf(query.get('sort') ?? 'at_risk', page), {
        page,
        pages: PAGES,
        total: PAGES * 8,
        band_counts: { High: PAGES * 4, Critical: PAGES * 4 },
      });
    }),
    respond('post', '/api/explain', async (request) => {
      const body = (await request.clone().json()) as { customer_ids: string[] };
      requests.push({ ids: body.customer_ids.join(','), signal: request.signal });
      await opened;
      const answer = explanations(body.customer_ids);
      const tenure = pageOf(body.customer_ids) / 10;
      return {
        ...answer,
        explanations: answer.explanations.map((e) => ({
          ...e,
          contributions: e.contributions.map((c) =>
            c.feature === 'tenure' ? { ...c, shap_value: tenure } : c,
          ),
        })),
      };
    }),
  );
  const live = () => requests.filter((r) => !r.signal.aborted).map((r) => r.ids);
  return { requests, live, release: () => release() };
}

const idsOf = (sort: string, page: number) =>
  rowsOf(sort, page)
    .map((row) => row.id)
    .join(',');

function table() {
  return screen.getByRole('table', { name: 'Flagged customers' });
}

async function shown(sort: string, page: number) {
  await waitFor(() =>
    expect(
      within(table()).getByText(`DEMO-${SORT_CODE[sort] ?? 'X'}${String(page)}-01`),
    ).toBeInTheDocument(),
  );
}

/** Longer than the queue waits for a page to settle, with room for its requests to start. */
async function settle() {
  await act(() => new Promise((resolve) => setTimeout(resolve, SETTLE_MS + 200)));
}

async function next() {
  await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
}

describe('Retention queue: explanations as the reader pages and sorts', () => {
  it('aborts the last page’s request on paging, and shows only the new page’s answer on its rows', async () => {
    const held = queueApi({ hold: true });
    renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    await next();
    await shown('at_risk', 2);
    // The new rows wait for their own answer, never borrowing one.
    expect(within(table()).getAllByText('Loading attributions')).toHaveLength(8);
    await settle();
    expect(held.requests.map((r) => r.ids)).toEqual([idsOf('at_risk', 1), idsOf('at_risk', 2)]);
    expect(held.live()).toEqual([idsOf('at_risk', 2)]);
    act(() => held.release());
    expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
    expect(within(table()).queryByText('Tenure +10.0')).toBeNull();
  });

  it('aborts the last order’s request on sorting, and asks once for the rows sorted', async () => {
    const held = queueApi({ hold: true });
    renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    await userEvent.click(within(table()).getByRole('button', { name: 'Tenure' }));
    await shown('tenure', 1);
    await settle();
    expect(held.requests.map((r) => r.ids)).toEqual([idsOf('at_risk', 1), idsOf('tenure', 1)]);
    expect(held.live()).toEqual([idsOf('tenure', 1)]);
    act(() => held.release());
    expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
    expect(within(table()).getByText('DEMO-T1-01')).toBeInTheDocument();
  });

  it('asks only for the page the reader stops at when paging quickly', async () => {
    const seen = recordRequests();
    queueApi();
    renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
    await next();
    await next();
    await next();
    await shown('at_risk', 4);
    await settle();
    expect(await within(table()).findAllByText('Tenure +40.0')).toHaveLength(8);
    const explains = seen.filter((url) => url.pathname === '/api/explain');
    // The first page, then the fourth: the second and third were passed on the way.
    expect(explains).toHaveLength(2);
    expect(within(table()).queryByText(/Tenure \+(10|20|30)\.0/)).toBeNull();
  });

  it('shows a page already answered at once on returning to it, asking nothing', async () => {
    const seen = recordRequests();
    queueApi();
    renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
    await next();
    await settle();
    expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    await shown('at_risk', 1);
    expect(within(table()).getAllByText('Tenure +10.0')).toHaveLength(8);
    expect(within(table()).queryByText('Loading attributions')).toBeNull();
    await settle();
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(2);
  });

  it('lets the new page’s request outlive a reader who leaves the page, and shows its answer on Back', async () => {
    const seen = recordRequests();
    const held = queueApi({ hold: true });
    renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    await next();
    await shown('at_risk', 2);
    await settle();
    expect(held.live()).toEqual([idsOf('at_risk', 2)]);
    await userEvent.click(
      screen.getAllByRole('link', { name: /^Model performance/ })[0] as HTMLElement,
    );
    await waitFor(() => expect(window.location.pathname).toBe('/performance'));
    await settle();
    expect(held.live()).toEqual([idsOf('at_risk', 2)]);
    act(() => window.history.back());
    await shown('at_risk', 2);
    act(() => held.release());
    expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
    await settle();
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(2);
  });
});
