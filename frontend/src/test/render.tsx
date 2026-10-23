/**
 * Render under the providers the app gives every page, at a given address.
 * The query client is the app's own (same caching), with retries off so a
 * test that answers 500 sees the failure at once.
 */
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { render, waitFor, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createQueryClient } from '../api/queries';

const live = new Set<QueryClient>();

export function testQueryClient(): QueryClient {
  const client = createQueryClient();
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({
    queries: { ...defaults.queries, retry: false },
    mutations: { ...defaults.mutations, retry: false },
  });
  live.add(client);
  return client;
}

/**
 * Wait until no request a test started is still in flight. Unmounting first
 * would abort them after MSW has answered, and their state updates would
 * land after the test.
 */
export async function settleQueries(): Promise<void> {
  await waitFor(() => {
    for (const client of live) {
      if (client.isFetching() + client.isMutating() > 0) throw new Error('requests in flight');
    }
  });
  live.clear();
}

export function renderAt(ui: ReactElement, path = '/'): RenderResult & { client: QueryClient } {
  window.history.replaceState(null, '', path);
  const client = testQueryClient();
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return { ...result, client };
}
