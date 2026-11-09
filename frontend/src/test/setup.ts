import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, vi } from 'vitest';
import { settleQueries } from './render';
import { server } from './server';
import { installMatchMedia, resetViewport } from './viewport';

installMatchMedia();
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
  await settleQueries();
  cleanup();
  vi.restoreAllMocks();
  server.resetHandlers();
  server.events.removeAllListeners();
  resetViewport();
  window.localStorage.clear();
  const missed = unanswered.splice(0);
  expect(missed, 'requests no test handler answered').toEqual([]);
});

afterAll(() => {
  server.close();
});
