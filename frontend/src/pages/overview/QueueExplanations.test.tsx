/**
 * The retention queue's explanation requests as the reader pages, sorts and
 * leaves: a page or sort the reader moved on from has its request aborted and
 * its answer never shown on the rows that replaced it; quick changes ask only
 * for the page the reader stops at; a request left behind by leaving the page
 * still outlives it (src/api/queries.ts, ``outlive``).
 *
 * Each page's explanation carries its own tenure attribution (page 1: +10.0 pp,
 * page 2: +20.0 pp…), so an answer shown on the wrong rows would be seen.
 *
 * No test waits a fixed time: each step waits for what it needs to see (a
 * request received, rows shown, the client idle), so a slow machine makes a
 * step slower, never another outcome. Where a test asserts what quick paging
 * leaves unasked, the queue's settle timer fires only when the test fires it
 * (``holdSettling``). Where a request is aborted, the timer runs as in the
 * app, so the next request starts ``SETTLE_MS`` after the abort, as for a
 * reader: under MSW's Node interception, one sent the instant another is
 * aborted can open a socket that is passed through to the real, closed port
 * and be cut off with it - a fault of the harness, not of the queue.
 */
import type { QueryClient } from '@tanstack/react-query';
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

/**
 * Holds the queue's settle timer (its only timer of ``SETTLE_MS``) until the
 * test fires it, so whether rows settle between two clicks is the test's
 * choice, never the machine's speed. Every other timer runs as usual; the
 * spies go with ``vi.restoreAllMocks`` after the test.
 */
function holdSettling() {
  const { setTimeout: realSet, clearTimeout: realClear } = globalThis;
  const waiting = new Map<number, () => void>();
  let last = 0;
  vi.spyOn(globalThis, 'setTimeout').mockImplementation(((
    callback: (...args: unknown[]) => void,
    ms?: number,
    ...args: unknown[]
  ) => {
    if (ms !== SETTLE_MS) return realSet(callback, ms, ...args);
    // Negative: never the id of a real timer.
    last -= 1;
    waiting.set(last, () => {
      callback(...args);
    });
    return last;
  }) as unknown as typeof setTimeout);
  vi.spyOn(globalThis, 'clearTimeout').mockImplementation((handle) => {
    if (typeof handle === 'number' && handle < 0) waiting.delete(handle);
    else realClear(handle);
  });
  return {
    /** Lets the rows shown now settle, as ``SETTLE_MS`` passing would. */
    fire() {
      const due = [...waiting.values()];
      waiting.clear();
      expect(due, 'a settle timer waiting').not.toHaveLength(0);
      act(() => {
        for (const run of due) run();
      });
    },
  };
}

/** Until no request is in flight: any a step started has been received and answered. */
async function idle(client: QueryClient) {
  await waitFor(() => expect(client.isFetching()).toBe(0));
}

const explainsIn = (seen: readonly URL[]) => seen.filter((url) => url.pathname === '/api/explain');

async function next() {
  await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
}

describe('Retention queue: explanations as the reader pages and sorts', () => {
  it('aborts the last page’s request on paging, and shows only the new page’s answer on its rows', async () => {
    const held = queueApi({ hold: true });
    try {
      renderAt(<App />, '/?tab=queue');
      await shown('at_risk', 1);
      await waitFor(() => expect(held.requests).toHaveLength(1));
      await next();
      await shown('at_risk', 2);
      // The new rows wait for their own answer, never borrowing one.
      expect(within(table()).getAllByText('Loading attributions')).toHaveLength(8);
      // Once they settle, they ask for it.
      await waitFor(() => expect(held.requests).toHaveLength(2));
      expect(held.requests.map((r) => r.ids)).toEqual([idsOf('at_risk', 1), idsOf('at_risk', 2)]);
      expect(held.live()).toEqual([idsOf('at_risk', 2)]);
      act(() => held.release());
      expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
      expect(within(table()).queryByText('Tenure +10.0')).toBeNull();
      expect(held.requests).toHaveLength(2);
    } finally {
      // Answered even when an assertion failed, so nothing is left in flight after the test.
      held.release();
    }
  });

  it('aborts the last order’s request on sorting, and asks once for the rows sorted', async () => {
    const held = queueApi({ hold: true });
    try {
      renderAt(<App />, '/?tab=queue');
      await shown('at_risk', 1);
      await waitFor(() => expect(held.requests).toHaveLength(1));
      await userEvent.click(within(table()).getByRole('button', { name: 'Tenure' }));
      await shown('tenure', 1);
      await waitFor(() => expect(held.requests).toHaveLength(2));
      expect(held.requests.map((r) => r.ids)).toEqual([idsOf('at_risk', 1), idsOf('tenure', 1)]);
      expect(held.live()).toEqual([idsOf('tenure', 1)]);
      act(() => held.release());
      expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
      expect(within(table()).getByText('DEMO-T1-01')).toBeInTheDocument();
      expect(held.requests).toHaveLength(2);
    } finally {
      held.release();
    }
  });

  it('asks only for the page the reader stops at when paging quickly', async () => {
    const seen = recordRequests();
    const settling = holdSettling();
    queueApi();
    const { client } = renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
    // Quickly: no page stays long enough to settle, however long each click takes.
    await next();
    await shown('at_risk', 2);
    await next();
    await shown('at_risk', 3);
    await next();
    await shown('at_risk', 4);
    expect(explainsIn(seen)).toHaveLength(1);
    settling.fire();
    expect(await within(table()).findAllByText('Tenure +40.0')).toHaveLength(8);
    await idle(client);
    // The first page, then the fourth: the second and third were passed on the way.
    expect(explainsIn(seen)).toHaveLength(2);
    expect(within(table()).queryByText(/Tenure \+(10|20|30)\.0/)).toBeNull();
  });

  it('shows a page already answered at once on returning to it, asking nothing', async () => {
    const seen = recordRequests();
    const settling = holdSettling();
    queueApi();
    const { client } = renderAt(<App />, '/?tab=queue');
    await shown('at_risk', 1);
    expect(await within(table()).findAllByText('Tenure +10.0')).toHaveLength(8);
    await next();
    await shown('at_risk', 2);
    settling.fire();
    expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    await shown('at_risk', 1);
    expect(within(table()).getAllByText('Tenure +10.0')).toHaveLength(8);
    expect(within(table()).queryByText('Loading attributions')).toBeNull();
    // Nor once the rows settle: the answer kept is all they need.
    settling.fire();
    await idle(client);
    expect(explainsIn(seen)).toHaveLength(2);
  });

  it('lets the new page’s request outlive a reader who leaves the page, and shows its answer on Back', async () => {
    const seen = recordRequests();
    const held = queueApi({ hold: true });
    try {
      const { client } = renderAt(<App />, '/?tab=queue');
      await shown('at_risk', 1);
      await next();
      await shown('at_risk', 2);
      await waitFor(() => expect(held.live()).toEqual([idsOf('at_risk', 2)]));
      await userEvent.click(
        screen.getAllByRole('link', { name: /^Model performance/ })[0] as HTMLElement,
      );
      await waitFor(() => expect(window.location.pathname).toBe('/performance'));
      // The queue is gone (unmounted), and its request still runs.
      await waitFor(() =>
        expect(screen.queryByRole('table', { name: 'Flagged customers' })).toBeNull(),
      );
      expect(held.live()).toEqual([idsOf('at_risk', 2)]);
      act(() => window.history.back());
      await shown('at_risk', 2);
      act(() => held.release());
      expect(await within(table()).findAllByText('Tenure +20.0')).toHaveLength(8);
      await idle(client);
      // The request left behind answered the page on Back: nothing was asked again.
      expect(explainsIn(seen)).toHaveLength(2);
    } finally {
      held.release();
    }
  });
});
