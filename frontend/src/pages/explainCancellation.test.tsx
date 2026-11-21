/**
 * The pages other than the retention queue ask for explanations as they
 * always did: a request still unanswered when the reader leaves is aborted.
 * Only the queue lets its request outlive the reader (see the queue's tests
 * and src/api/queries.test.tsx).
 */
import { waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw/http';
import { App } from '../app/App';
import { customerApi, EXAMPLES } from '../test/customerApi';
import { explainApi } from '../test/explainApi';
import { renderAt } from '../test/render';
import { server } from '../test/server';

/** Explanation requests, held unanswered; each one's signal is kept to see if it was aborted. */
function holdExplain() {
  const signals: AbortSignal[] = [];
  let release = () => {};
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.post('/api/explain', async ({ request }) => {
      signals.push(request.signal);
      await opened;
      return HttpResponse.json(
        { error: 'internal_error', detail: '', problems: [] },
        { status: 500 },
      );
    }),
  );
  return { signals, release: () => release() };
}

const ID = EXAMPLES[0]?.id ?? '';
let release = () => {};
afterEach(() => release());

it.each([
  ['the single customer page', `/customer?id=${ID}`, () => customerApi()],
  ['the explainability page', `/explainability?id=${ID}`, () => explainApi()],
])('%s aborts an explanation still unanswered when the reader leaves', async (_name, path, api) => {
  server.use(...api());
  const held = holdExplain();
  release = held.release;
  const { unmount } = renderAt(<App />, path);
  await waitFor(() => expect(held.signals).toHaveLength(1));
  expect(held.signals[0]?.aborted).toBe(false);
  unmount();
  await waitFor(() => expect(held.signals[0]?.aborted).toBe(true));
});
