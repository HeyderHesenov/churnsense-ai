import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw/http';
import type { ReactNode } from 'react';
import { batchScore } from '../test/batchApi';
import { customerPage, customerRow, explanations } from '../test/overviewFixtures';
import { testQueryClient } from '../test/render';
import { answer, recordRequests, refuse, respond, server } from '../test/server';
import { ApiError } from './client';
import {
  createQueryClient,
  customersQuery,
  explanationsKey,
  retryDelay,
  retryExplanation,
  retryGlobal,
  retryOutcomes,
  scoreBatch,
  shouldRetry,
  useCustomer,
  useCustomerCount,
  useCustomers,
  useExplanations,
  useGlobalExplanation,
  useSegments,
  useSelection,
  useSweep,
  type CustomerQuery,
} from './queries';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
}

const QUERY: CustomerQuery = { sort: 'at_risk', dir: 'desc', page: 1, page_size: 8, flagged: true };

describe('customer listings', () => {
  it('asks for nothing when no band or no customer is chosen, rather than for all of them', async () => {
    const seen = recordRequests();
    const { result } = renderHook(() => useCustomers({ ...QUERY, bands: [] }), { wrapper });
    const count = renderHook(() => useCustomerCount({ bands: [] }), { wrapper });
    const none = renderHook(() => useCustomers({ ...QUERY, ids: [], flagged: null }), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(seen).toEqual([]);
    expect(result.current.fetchStatus).toBe('idle');
    expect(count.result.current.fetchStatus).toBe('idle');
    expect(none.result.current.fetchStatus).toBe('idle');
  });

  it('sends the chosen bands, the filters and the page', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0001')])));
    const seen = recordRequests();
    const { result } = renderHook(() => useCustomers({ ...QUERY, bands: ['High', 'Critical'] }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = seen[0];
    expect(url?.pathname).toBe('/api/customers');
    expect(url?.searchParams.getAll('bands')).toEqual(['High', 'Critical']);
    expect(url?.searchParams.get('flagged')).toBe('true');
    expect(url?.searchParams.get('sort')).toBe('at_risk');
    expect(url?.searchParams.get('page_size')).toBe('8');
  });

  it('asks for nothing for a tenure range whose minimum is above its maximum', async () => {
    const seen = recordRequests();
    const listing = renderHook(() => useCustomers({ ...QUERY, tenure_min: 10, tenure_max: 9 }), {
      wrapper,
    });
    const count = renderHook(() => useCustomerCount({ tenure_min: 10, tenure_max: 9 }), {
      wrapper,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(seen).toEqual([]);
    expect(listing.result.current.fetchStatus).toBe('idle');
    expect(count.result.current.fetchStatus).toBe('idle');
  });

  it('sends a range of one tenure, and an open bound', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0001')])));
    const seen = recordRequests();
    const one = renderHook(() => useCustomers({ ...QUERY, tenure_min: 6, tenure_max: 6 }), {
      wrapper,
    });
    const open = renderHook(() => useCustomers({ ...QUERY, tenure_min: 0, tenure_max: null }), {
      wrapper,
    });
    await waitFor(() => expect(one.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(open.result.current.isSuccess).toBe(true));
    // The two listings fetch at the same time, so the order their requests start
    // in is not fixed. Compared as a sorted whole: each request exactly once, and
    // nothing missing or extra.
    const sent = seen.map((url) =>
      JSON.stringify([url.searchParams.get('tenure_min'), url.searchParams.get('tenure_max')]),
    );
    expect(sent.sort()).toEqual([JSON.stringify(['0', null]), JSON.stringify(['6', '6'])].sort());
  });

  it('shares its cache with a listing fetched ahead of time', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0001')])));
    const seen = recordRequests();
    const client = testQueryClient();
    const query: CustomerQuery = { ...QUERY, page: 2 };
    await client.query(customersQuery(query));
    const { result } = renderHook(() => useCustomers(query), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    expect(result.current.data?.rows[0]?.id).toBe('DEMO-0001');
    expect(seen).toHaveLength(1);
  });

  it.each([
    ['no band', { bands: [] }],
    ['no customer', { ids: [] }],
    ['a range that holds nobody', { tenure_min: 10, tenure_max: 9 }],
  ])('refuses a listing fetched ahead of time for %s, and sends nothing', async (_, filter) => {
    const seen = recordRequests();
    const client = testQueryClient();
    await expect(client.query(customersQuery({ ...QUERY, ...filter }))).rejects.toThrow();
    expect(seen).toEqual([]);
  });

  it('lists exactly the chosen customers by id', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0002')])));
    const seen = recordRequests();
    const { result } = renderHook(
      () => useCustomers({ ...QUERY, ids: ['DEMO-0002', 'DEMO-0001'], flagged: null }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(seen[0]?.searchParams.getAll('ids')).toEqual(['DEMO-0002', 'DEMO-0001']);
  });
});

describe('a selection', () => {
  it('asks for one row, and keeps the summary of every match', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0001')])));
    const seen = recordRequests();
    const { result } = renderHook(() => useSelection({ contract: 'Two year' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(seen).toHaveLength(1);
    expect(seen[0]?.searchParams.get('page_size')).toBe('1');
    expect(seen[0]?.searchParams.get('contract')).toBe('Two year');
    expect(result.current.data?.summary.customers).toBe(1);
  });

  it('shares one request with the count of the same filter', async () => {
    server.use(answer('get', '/api/customers', customerPage([customerRow('DEMO-0001')])));
    const seen = recordRequests();
    const client = testQueryClient();
    const shared = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const selection = renderHook(() => useSelection({ flagged: true }), { wrapper: shared });
    const count = renderHook(() => useCustomerCount({ flagged: true }), { wrapper: shared });
    await waitFor(() => expect(count.result.current.data).toBe(1));
    expect(selection.result.current.data?.total).toBe(1);
    expect(seen).toHaveLength(1);
  });

  it('asks for nothing when nothing is chosen', async () => {
    const seen = recordRequests();
    const none = renderHook(() => useSelection({ bands: [] }), { wrapper });
    const empty = renderHook(() => useSelection({ tenure_min: 3, tenure_max: 2 }), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(seen).toEqual([]);
    expect(none.result.current.fetchStatus).toBe('idle');
    expect(empty.result.current.fetchStatus).toBe('idle');
  });
});

describe('one customer', () => {
  it.each(['..', '.', '', 'DEMO/0001', 'x'.repeat(33)])(
    'never asks for the malformed id %j',
    async (id) => {
      const seen = recordRequests();
      const { result } = renderHook(() => useCustomer(id), { wrapper });
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(seen).toEqual([]);
      expect(result.current.fetchStatus).toBe('idle');
    },
  );
});

/**
 * An explanation request held unanswered until ``release``; each request's
 * signal is kept, to see whether the page aborted it.
 */
function holdExplain() {
  const signals: AbortSignal[] = [];
  let release = () => {};
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    respond('post', '/api/explain', async (request) => {
      signals.push(request.signal);
      const body = (await request.clone().json()) as { customer_ids: string[] };
      await opened;
      return explanations(body.customer_ids);
    }),
  );
  return { signals, release: () => release() };
}

describe('explanations: what a reader leaving does to a request in flight', () => {
  const IDS = ['DEMO-0001', 'DEMO-0002'];

  it('by default, as every page but the retention queue asks: aborted on leaving, asked again on return', async () => {
    const held = holdExplain();
    const client = testQueryClient();
    const shared = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const first = renderHook(() => useExplanations(IDS), { wrapper: shared });
    await waitFor(() => expect(held.signals).toHaveLength(1));
    first.unmount();
    await waitFor(() => expect(held.signals[0]?.aborted).toBe(true));
    const back = renderHook(() => useExplanations(IDS), { wrapper: shared });
    await waitFor(() => expect(held.signals).toHaveLength(2));
    act(() => held.release());
    await waitFor(() => expect(back.result.current.isSuccess).toBe(true));
    // Asked again: how many times depends on when the abort settles, never fewer than twice.
    expect(held.signals.length).toBeGreaterThanOrEqual(2);
  });

  it('with outlive, as the retention queue asks: runs to its answer, which the return reads', async () => {
    const held = holdExplain();
    const client = testQueryClient();
    const shared = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const first = renderHook(() => useExplanations(IDS, { outlive: true }), { wrapper: shared });
    await waitFor(() => expect(held.signals).toHaveLength(1));
    first.unmount();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(held.signals[0]?.aborted).toBe(false);
    const back = renderHook(() => useExplanations(IDS, { outlive: true }), { wrapper: shared });
    // Still unanswered after its reader left, and not aborted.
    expect(held.signals[0]?.aborted).toBe(false);
    act(() => held.release());
    await waitFor(() => expect(back.result.current.isSuccess).toBe(true));
    expect(held.signals).toHaveLength(1);
  });

  it.each([
    ['by default', undefined],
    ['with outlive', { outlive: true }],
  ] as const)(
    '%s, keeps the cache rules every query has: the same key, staleness, lifetime and retries',
    async (_name, options) => {
      server.use(
        respond('post', '/api/explain', async (request) => {
          const body = (await request.json()) as { customer_ids: string[] };
          return explanations(body.customer_ids);
        }),
      );
      const client = createQueryClient();
      const shared = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const { result } = renderHook(() => useExplanations(IDS, options), { wrapper: shared });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      const query = client.getQueryCache().find({ queryKey: ['explain', 'ids', IDS] });
      // As the hook's observer resolved them against the client's defaults.
      const resolved = query?.observers[0]?.options;
      expect(resolved).toBeDefined();
      expect(resolved?.staleTime).toBe(Infinity);
      expect(resolved?.gcTime).toBe(10 * 60_000);
      expect(resolved?.refetchOnWindowFocus).toBe(false);
      expect(resolved?.retryDelay).toBe(retryDelay);
      const retry = resolved?.retry as (failures: number, error: Error) => boolean;
      const limited = new ApiError(429, 'rate_limited');
      const never = new ApiError(503, 'explanations_unavailable');
      expect([retry(0, limited), retry(1, limited), retry(0, never)]).toEqual([
        retryExplanation(0, limited),
        retryExplanation(1, limited),
        retryExplanation(0, never),
      ]);
      client.clear();
    },
  );

  it('leaves the defaults every query starts from as they were', () => {
    const queries = createQueryClient().getDefaultOptions().queries;
    expect(queries?.staleTime).toBe(Infinity);
    expect(queries?.gcTime).toBe(10 * 60_000);
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(queries?.retry).toBe(shouldRetry);
    expect(queries?.retryDelay).toBe(retryDelay);
  });
});

/**
 * Every explanation request, held unanswered until ``release``: the ids it
 * asked for and its signal, to see which ones the hook aborted.
 */
function holdEachExplain() {
  const requests: { ids: string; signal: AbortSignal }[] = [];
  let release = () => {};
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    respond('post', '/api/explain', async (request) => {
      const body = (await request.clone().json()) as { customer_ids: string[] };
      requests.push({ ids: body.customer_ids.join(','), signal: request.signal });
      await opened;
      return explanations(body.customer_ids);
    }),
  );
  const live = () => requests.filter((r) => !r.signal.aborted).map((r) => r.ids);
  return { requests, live, release: () => release() };
}

describe('explanations: what a reader who stays but moves to other ids does to a request in flight', () => {
  const A = ['DEMO-0001', 'DEMO-0002'];
  const B = ['DEMO-0003', 'DEMO-0004'];
  const C = ['DEMO-0005', 'DEMO-0006'];

  function shared() {
    const client = testQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    return { client, wrapper };
  }

  function outliving(wrapper: (props: { children: ReactNode }) => ReactNode) {
    return renderHook(({ ids }) => useExplanations(ids, { outlive: true }), {
      wrapper,
      initialProps: { ids: A },
    });
  }

  it('with outlive, aborts the old ids’ request and puts their query back as it was', async () => {
    const held = holdEachExplain();
    const { client, wrapper } = shared();
    const hook = outliving(wrapper);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    hook.rerender({ ids: B });
    await waitFor(() => expect(held.requests).toHaveLength(2));
    await waitFor(() => expect(held.requests[0]?.signal.aborted).toBe(true));
    expect(held.live()).toEqual([B.join(',')]);
    // Put back, not failed: shown again, those ids would simply be asked for again.
    const old = client.getQueryCache().find({ queryKey: explanationsKey(A), exact: true });
    expect(old?.state).toMatchObject({
      status: 'pending',
      fetchStatus: 'idle',
      error: null,
      data: undefined,
    });
    act(() => held.release());
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.explanations.map((e) => e.customer_id)).toEqual(B);
    expect(old?.state.data).toBeUndefined();
    expect(held.requests).toHaveLength(2);
  });

  it('with outlive, never gives the new ids the old ids’ answer, however recently it came', async () => {
    server.use(
      respond('post', '/api/explain', async (request) => {
        const body = (await request.json()) as { customer_ids: string[] };
        return explanations(body.customer_ids);
      }),
    );
    const { wrapper } = shared();
    const hook = outliving(wrapper);
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    hook.rerender({ ids: B });
    expect(hook.result.current.data).toBeUndefined();
    expect(hook.result.current.isPending).toBe(true);
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.explanations.map((e) => e.customer_id)).toEqual(B);
  });

  it('with outlive, keeps only the last of quick changes in flight', async () => {
    const held = holdEachExplain();
    const { wrapper } = shared();
    const hook = outliving(wrapper);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    hook.rerender({ ids: B });
    hook.rerender({ ids: C });
    await waitFor(() => expect(held.live()).toEqual([C.join(',')]));
    // However many reached the server on the way, each is aborted but the last.
    expect(held.requests.filter((r) => r.ids === C.join(','))).toHaveLength(1);
    act(() => held.release());
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.explanations.map((e) => e.customer_id)).toEqual(C);
  });

  it('with outlive, asks again for aborted ids that are shown again', async () => {
    const held = holdEachExplain();
    const { wrapper } = shared();
    const hook = outliving(wrapper);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    hook.rerender({ ids: B });
    await waitFor(() => expect(held.requests[0]?.signal.aborted).toBe(true));
    hook.rerender({ ids: A });
    await waitFor(() => expect(held.live()).toEqual([A.join(',')]));
    act(() => held.release());
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.explanations.map((e) => e.customer_id)).toEqual(A);
    expect(held.requests.slice(0, 3).map((r) => r.ids)).toEqual(
      [A, B, A].map((ids) => ids.join(',')),
    );
    // Asked again once in a browser (e2e/overview-tabs.spec.ts). Here MSW sometimes passes a request
    // identical to one aborted mid-answer through to the network (ECONNREFUSED), and the retry of
    // that network error asks for the same ids once more.
    expect(held.requests.slice(2).every((r) => r.ids === A.join(','))).toBe(true);
  });

  it('with outlive, still lets the last request outlive a reader who then leaves, and the return reads it', async () => {
    const held = holdEachExplain();
    const { wrapper } = shared();
    const hook = outliving(wrapper);
    await waitFor(() => expect(held.requests).toHaveLength(1));
    hook.rerender({ ids: B });
    await waitFor(() => expect(held.live()).toEqual([B.join(',')]));
    hook.unmount();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(held.live()).toEqual([B.join(',')]);
    const back = renderHook(() => useExplanations(B, { outlive: true }), { wrapper });
    act(() => held.release());
    await waitFor(() => expect(back.result.current.isSuccess).toBe(true));
    expect(held.requests).toHaveLength(2);
  });

  it('with outlive, never sends the old ids again after a refusal, once the reader moved on during the wait', async () => {
    const asked: string[] = [];
    server.use(
      http.post('/api/explain', async ({ request }) => {
        const body = (await request.json()) as { customer_ids: string[] };
        asked.push(body.customer_ids.join(','));
        if (body.customer_ids[0] === A[0]) {
          return HttpResponse.json(
            { error: 'explanations_busy', detail: '', problems: [] },
            { status: 503 },
          );
        }
        return HttpResponse.json(explanations(body.customer_ids));
      }),
    );
    const { wrapper } = shared();
    const hook = outliving(wrapper);
    // Refused once; the retry waits (half a second) before asking again.
    await waitFor(() => expect(hook.result.current.failureCount).toBe(1));
    hook.rerender({ ids: B });
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));
    expect(asked).toEqual([A.join(','), B.join(',')]);
  });

  it('with outlive, never aborts the request for the ids shown, even as StrictMode runs its effects twice', async () => {
    const held = holdEachExplain();
    const { wrapper } = shared();
    const hook = renderHook(({ ids }) => useExplanations(ids, { outlive: true }), {
      wrapper,
      initialProps: { ids: A },
      reactStrictMode: true,
    });
    await waitFor(() => expect(held.requests).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    // Asked once and still in flight: not aborted and asked again.
    expect(held.requests.map((r) => r.ids)).toEqual([A.join(',')]);
    expect(held.live()).toEqual([A.join(',')]);
    hook.rerender({ ids: B });
    await waitFor(() => expect(held.live()).toEqual([B.join(',')]));
    act(() => held.release());
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data?.explanations.map((e) => e.customer_id)).toEqual(B);
  });

  it('by default, as every page but the retention queue asks, aborts on moving and on leaving, as before', async () => {
    const held = holdEachExplain();
    const { wrapper } = shared();
    const hook = renderHook(({ ids }) => useExplanations(ids), {
      wrapper,
      initialProps: { ids: A },
    });
    await waitFor(() => expect(held.requests).toHaveLength(1));
    hook.rerender({ ids: B });
    await waitFor(() => expect(held.live()).toEqual([B.join(',')]));
    hook.unmount();
    await waitFor(() => expect(held.live()).toEqual([]));
  });
});

/**
 * Every call of ``useExplanations`` in the app, read from the source: the
 * retention queue alone outlives its reader; every other page asks with the
 * ids alone, the default, so its requests are aborted as they always were.
 */
const sources = import.meta.glob<string>('../**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
});

describe('who asks for explanations that outlive the reader', () => {
  const calls = Object.entries(sources)
    .filter(([path]) => !path.includes('.test.'))
    .flatMap(([path, source]) =>
      [...source.matchAll(/useExplanations\(([\s\S]*?)\);/g)].map(
        (m) => [path, m[1] ?? ''] as const,
      ),
    );

  it('finds every page that asks', () => {
    expect([...new Set(calls.map(([path]) => path))].sort()).toEqual([
      '../pages/customer/CustomerPage.tsx',
      '../pages/explainability/ExplainabilityPage.tsx',
      '../pages/explorer/Detail.tsx',
      '../pages/overview/RetentionQueue.tsx',
    ]);
  });

  it.each(calls)('%s asks with outlive only if it is the retention queue', (path, args) => {
    if (path === '../pages/overview/RetentionQueue.tsx') {
      expect(args).toBe('asked, { outlive: true }');
    } else {
      expect(args).not.toMatch(/outlive/);
      // One argument: the ids, so the default holds.
      expect(args.split(',').filter((part) => part.includes('{'))).toEqual([]);
    }
  });
});

describe('explanations of customers in the book', () => {
  it.each([
    [429, 'rate_limited', 2],
    [503, 'explanations_busy', 2],
    [503, 'explanations_unavailable', 1],
  ] as const)('after a %i %s, asks %i time(s) in all', async (status, error, times) => {
    const seen = recordRequests();
    server.use(refuse('post', '/api/explain', status, error));
    const { result } = renderHook(() => useExplanations(['DEMO-0001']), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(times);
  });
});

describe('retries', () => {
  it('retries a failure of the server or the network once, and never a refusal', () => {
    expect(shouldRetry(0, new ApiError(503, 'model_unavailable'))).toBe(true);
    expect(shouldRetry(0, new ApiError(0, 'network_error'))).toBe(true);
    expect(shouldRetry(1, new ApiError(503, 'model_unavailable'))).toBe(false);
    expect(shouldRetry(0, new ApiError(422, 'validation_error'))).toBe(false);
    expect(shouldRetry(0, new ApiError(429, 'rate_limited'))).toBe(false);
    expect(shouldRetry(0, new Error('not from the API'))).toBe(false);
  });

  it('asks for an explanation again once after a rate limit, never when it cannot be had', () => {
    expect(retryExplanation(0, new ApiError(429, 'rate_limited', [], 2))).toBe(true);
    expect(retryExplanation(1, new ApiError(429, 'rate_limited', [], 2))).toBe(false);
    expect(retryExplanation(0, new ApiError(503, 'explanations_busy', [], 2))).toBe(true);
    expect(retryExplanation(0, new ApiError(503, 'explanations_unavailable'))).toBe(false);
    expect(retryExplanation(0, new ApiError(422, 'validation_error'))).toBe(false);
    expect(retryExplanation(0, new Error('not from the API'))).toBe(false);
  });

  it('asks for the cached attributions again once after a failure, never while none are cached', () => {
    expect(retryGlobal(0, new ApiError(503, 'model_unavailable'))).toBe(true);
    expect(retryGlobal(0, new ApiError(0, 'network_error'))).toBe(true);
    expect(retryGlobal(1, new ApiError(503, 'model_unavailable'))).toBe(false);
    expect(retryGlobal(0, new ApiError(503, 'explanations_not_cached'))).toBe(false);
    expect(retryGlobal(0, new ApiError(422, 'validation_error'))).toBe(false);
    expect(retryGlobal(0, new Error('not from the API'))).toBe(false);
  });

  it('asks for the cached attributions again once after a server failure', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/explain/global', 500, 'internal_error'));
    const { result } = renderHook(() => useGlobalExplanation(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(seen.filter((u) => u.pathname === '/api/explain/global')).toHaveLength(2);
  });

  it('asks for a breakdown again once after a server failure', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/segments', 500, 'internal_error'));
    const { result } = renderHook(() => useSegments('Contract', 'tenure'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(seen.filter((u) => u.pathname === '/api/segments')).toHaveLength(2);
  });

  it('never asks for a breakdown again while outcomes are withheld', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/segments', 503, 'outcomes_withheld'));
    const { result } = renderHook(() => useSegments('Contract', null), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(seen.filter((u) => u.pathname === '/api/segments')).toHaveLength(1);
  });

  it('asks for figures resting on outcomes again once after a failure, never while they are withheld', () => {
    expect(retryOutcomes(0, new ApiError(503, 'dataset_unavailable'))).toBe(true);
    expect(retryOutcomes(0, new ApiError(0, 'network_error'))).toBe(true);
    expect(retryOutcomes(1, new ApiError(503, 'dataset_unavailable'))).toBe(false);
    expect(retryOutcomes(0, new ApiError(503, 'outcomes_withheld'))).toBe(false);
    // No sweep is published: as lasting as withheld outcomes, never asked for again.
    expect(retryOutcomes(0, new ApiError(503, 'sweep_withheld'))).toBe(false);
    expect(retryOutcomes(0, new ApiError(429, 'rate_limited', [], 2))).toBe(false);
    expect(retryOutcomes(0, new ApiError(422, 'validation_error'))).toBe(false);
    expect(retryOutcomes(0, new Error('not from the API'))).toBe(false);
  });

  it('waits as long as the server asks, up to ten seconds', () => {
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3))).toBe(3000);
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3600))).toBe(10_000);
    expect(retryDelay(0, new ApiError(503, 'model_unavailable'))).toBe(500);
  });
});

describe('scoring a file', () => {
  const file = new File(['customerID\nDEMO-00001\n'], 'crm.csv', { type: 'text/csv' });

  it('sends the file as the form field "file" and returns the answer', async () => {
    let sent: unknown = null;
    server.use(
      respond('post', '/api/batch/score', async (request) => {
        const part = (await request.formData()).get('file');
        // MSW's File is another realm's, so it is read, not checked with instanceof.
        sent = typeof part === 'string' || part === null ? part : await part.text();
        return batchScore();
      }),
    );
    await expect(scoreBatch(file, new AbortController().signal)).resolves.toEqual(batchScore());
    expect(sent).toBe('customerID\nDEMO-00001\n');
  });

  it('keeps the first 50 problems of a refusal and counts the ones left out', async () => {
    const problems = Array.from({ length: 53 }, (_, i) => `problem ${String(i)}`);
    server.use(
      http.post('/api/batch/score', () =>
        HttpResponse.json({ error: 'invalid_input', detail: 'x', problems }, { status: 422 }),
      ),
    );
    const refusal: unknown = await scoreBatch(file, new AbortController().signal).catch(
      (error: unknown) => error,
    );
    expect(refusal).toBeInstanceOf(ApiError);
    expect((refusal as ApiError).problems).toEqual(problems.slice(0, 50));
    expect((refusal as ApiError).problemsOmitted).toBe(3);
  });
});

describe('the threshold sweep', () => {
  it('asks for nothing while there are no assumptions to send', async () => {
    const seen = recordRequests();
    const { result } = renderHook(() => useSweep(null), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(seen).toEqual([]);
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('sends every assumption it is given', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/threshold/sweep', 422, 'validation_error'));
    const { result } = renderHook(
      () => useSweep({ cost: 12.5, success: 0.3, horizon: 12, margin: 0.65 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(seen.map((url) => [...url.searchParams])).toEqual([
      [
        ['cost', '12.5'],
        ['success', '0.3'],
        ['horizon', '12'],
        ['margin', '0.65'],
      ],
    ]);
  });
});
