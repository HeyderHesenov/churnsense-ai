import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { customerPage, customerRow } from '../test/overviewFixtures';
import { testQueryClient } from '../test/render';
import { answer, recordRequests, refuse, server } from '../test/server';
import { ApiError } from './client';
import {
  customersQuery,
  retryDelay,
  retryExplanation,
  shouldRetry,
  useCustomer,
  useCustomerCount,
  useCustomers,
  useExplanations,
  useSelection,
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
    expect(
      seen.map((url) => [url.searchParams.get('tenure_min'), url.searchParams.get('tenure_max')]),
    ).toEqual([
      ['6', '6'],
      ['0', null],
    ]);
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

  it('waits as long as the server asks, up to ten seconds', () => {
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3))).toBe(3000);
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3600))).toBe(10_000);
    expect(retryDelay(0, new ApiError(503, 'model_unavailable'))).toBe(500);
  });
});
