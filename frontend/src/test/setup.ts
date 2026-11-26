import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, vi } from 'vitest';
import { settleQueries } from './render';
import { server } from './server';
import { installMatchMedia, resetViewport } from './viewport';

installMatchMedia();
// A findBy or waitFor gives up after this long. Testing Library's default, one second, is
// spent by a lazily loaded page and a mocked response alone when the machine is busy (43
// workers start at once, or another suite runs beside this one): 5 of 30 runs then failed
// a test somewhere, each time a different one, none of them wrong. A real failure still
// fails, three seconds later.
configure({ asyncUtilTimeout: 3000 });
// jsdom does not scroll: the shell scrolls to the top on every page change,
// and the overview brings its queue into view.
window.scrollTo = () => undefined;
Element.prototype.scrollIntoView = () => undefined;

/**
 * Requests no handler answered. MSW's own "error" strategy only logs (the
 * request then fails like a network error, which a page handles quietly), so
 * each one is recorded here, failed, and fails its test afterwards.
 */
const unanswered: string[] = [];

beforeAll(() => {
  server.listen({
    onUnhandledFrame({ frame }) {
      const data: unknown = frame.data;
      const request =
        typeof data === 'object' && data !== null && 'request' in data ? data.request : null;
      unanswered.push(
        request instanceof Request
          ? `${request.method} ${new URL(request.url).pathname}`
          : 'a non-HTTP frame',
      );
      throw new Error('No test handler answers this request');
    },
  });
});

afterEach(async () => {
  // Requests still in flight fail the test, but only once everything below is reset: a
  // test failing here must leave nothing (its page, handlers, spies, storage) to the next.
  let inFlight: Error | null = null;
  try {
    await settleQueries();
  } catch (error) {
    inFlight = error instanceof Error ? error : new Error(String(error));
  }
  cleanup();
  vi.restoreAllMocks();
  server.resetHandlers();
  server.events.removeAllListeners();
  resetViewport();
  window.localStorage.clear();
  const missed = unanswered.splice(0);
  expect(missed, 'requests no test handler answered').toEqual([]);
  if (inFlight !== null) throw inFlight;
});

afterAll(() => {
  server.close();
});
