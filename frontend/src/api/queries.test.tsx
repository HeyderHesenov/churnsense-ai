import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { customerPage, customerRow } from '../test/overviewFixtures';
import { testQueryClient } from '../test/render';
import { answer, recordRequests, server } from '../test/server';
import { ApiError } from './client';
import {
  retryDelay,
  shouldRetry,
  useCustomer,
  useCustomerCount,
  useCustomers,
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

describe('retries', () => {
  it('retries a failure of the server or the network once, and never a refusal', () => {
    expect(shouldRetry(0, new ApiError(503, 'model_unavailable'))).toBe(true);
    expect(shouldRetry(0, new ApiError(0, 'network_error'))).toBe(true);
    expect(shouldRetry(1, new ApiError(503, 'model_unavailable'))).toBe(false);
    expect(shouldRetry(0, new ApiError(422, 'validation_error'))).toBe(false);
    expect(shouldRetry(0, new ApiError(429, 'rate_limited'))).toBe(false);
    expect(shouldRetry(0, new Error('not from the API'))).toBe(false);
  });

  it('waits as long as the server asks, up to ten seconds', () => {
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3))).toBe(3000);
    expect(retryDelay(0, new ApiError(503, 'explanations_busy', [], 3600))).toBe(10_000);
    expect(retryDelay(0, new ApiError(503, 'model_unavailable'))).toBe(500);
  });
});
