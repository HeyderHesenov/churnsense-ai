/**
 * The test object every spec imports. Besides the page, each test gets a guard
 * that fails it for what no page may ever do, whatever the test itself checks:
 *
 * - an uncaught page error, or a console error the test did not provoke;
 * - a Content-Security-Policy violation;
 * - a request to another origin, or a failed one (navigation and the app's own
 *   cancellation abort requests; those are expected);
 * - the same request sent again by one document while the first is in flight;
 * - an outcome of a held-out (test or unverified) customer, a figure of a
 *   suppressed group, or a final-evaluation figure in any API response, or an
 *   IBM Telco customer ID in any response or in the final page;
 * - on the public-demo server, a customer of its "dataset" (an ``E2E-`` ID) in
 *   any response or in the final page: that server may know none.
 */
import { expect, test as base, type Page, type Request, type Route } from '@playwright/test';
import type { components } from '../src/api/schema';
import { DATASET_PREFIX, PUBLIC_PROJECT } from './global-setup';

export type ErrorResponse = components['schemas']['ErrorResponse'];

/** The widths every page is laid out at: a narrow phone up to a desktop. */
export const WIDTHS = [320, 375, 768, 1024, 1440] as const;

/** The IBM Telco customer-ID format (tests/test_governance.py). */
const DATASET_ID = /\b[0-9]{4}-[A-Z]{5}\b/g;
/** The format hint the ID search box shows: the format, not a customer. */
const ID_FORMAT_HINT = '1234-ABCDE';

/** IBM Telco customer IDs in ``text``, the search box's format hint aside. */
export function datasetIds(text: string): string[] {
  return [...text.matchAll(DATASET_ID)].map(([id]) => id).filter((id) => id !== ID_FORMAT_HINT);
}

export interface Guard {
  /**
   * A console error this test provokes on purpose (a refusal it injects, say):
   * its text, and the path of the address it is about, so the same text about
   * any other address (a missing script, say) still fails the test. ``null``
   * allows it only from a message that names no address (an inline script).
   */
  allowConsole: (text: RegExp, path: RegExp | null) => void;
  /** A request this test makes fail on purpose. */
  allowFailedRequest: (pattern: RegExp) => void;
  /** A policy violation this test provokes on purpose (by its effective directive). */
  allowCspViolation: (directive: string) => void;
}

interface Body {
  url: string;
  type: string;
  text: string;
}

/** Splits one CSV line into its cells, honouring quotes ("" is a quote inside one). */
export function cells(line: string): string[] {
  const out: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line.charAt(i);
    if (quoted && char === '"' && line.charAt(i + 1) === '"') {
      cell += '"';
      i += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === ',' && !quoted) {
      out.push(cell);
      cell = '';
    } else {
      cell += char;
    }
  }
  out.push(cell);
  return out;
}

/** Partitions whose customers' outcomes are never shown (src/churnsense/book.py). */
const HELD_OUT = new Set(['test', 'unknown']);
/** All a suppressed cell may say: where it is, how many, and why (SuppressedCell). */
const SUPPRESSED_KEYS = new Set(['row', 'column', 'n', 'reason', 'suppressed']);

function leaksInJson(value: unknown, at: string, found: string[]): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      leaksInJson(item, `${at}[${String(index)}]`, found);
    });
    return;
  }
  if (value === null || typeof value !== 'object') return;
  const record = value as Record<string, unknown>;
  const outcome = record['outcome'];
  if (HELD_OUT.has(String(record['partition'])) && outcome != null && outcome !== 'withheld') {
    found.push(`${at}: a held-out customer's outcome (${JSON.stringify(outcome)})`);
  }
  if (record['suppressed'] === true) {
    const extra = Object.keys(record).filter((key) => !SUPPRESSED_KEYS.has(key));
    if (extra.length > 0) found.push(`${at}: a suppressed group's ${extra.join(', ')}`);
  }
  for (const key of ['test_metrics', 'test_evaluation']) {
    if (record[key] != null) found.push(`${at}.${key} is set`);
  }
  for (const [key, child] of Object.entries(record)) leaksInJson(child, `${at}.${key}`, found);
}

/**
 * What a response body must never hold: a held-out customer's outcome, a
 * suppressed group's figures, a final-evaluation figure (none is recorded for
 * the synthetic model), or an IBM Telco customer ID.
 */
export function leaks({ url, type, text }: Body): string[] {
  const found: string[] = [];
  const path = new URL(url).pathname;
  if (datasetIds(text).length > 0) found.push(`${path}: an IBM Telco customer ID`);
  if (type.includes('application/json')) {
    const body: unknown = JSON.parse(text);
    leaksInJson(body, path, found);
    if (path === '/api/performance' && (body as { test?: unknown }).test != null) {
      found.push(`${path}: a test evaluation`);
    }
  } else if (type.includes('text/csv')) {
    const [header = '', ...rows] = text.trim().split(/\r?\n/);
    const columns = cells(header);
    const partition = columns.indexOf('partition');
    const outcome = columns.indexOf('outcome');
    if (partition >= 0 && outcome >= 0) {
      for (const row of rows.map(cells)) {
        if (HELD_OUT.has(row[partition] ?? '') && row[outcome] !== 'withheld') {
          found.push(`${path}: a held-out customer's outcome in the CSV`);
        }
      }
    }
  }
  return found;
}

/** What ``page.route`` matches: a glob, a pattern or a test of the URL. */
export type Match = string | RegExp | ((url: URL) => boolean);

/**
 * Waits until the page has its heading and no placeholder is left: a Skeleton
 * (src/components/ui/States.tsx) is a status whose blocks are hidden from
 * assistive technology, whatever it says.
 */
export async function settled(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.locator('[role="status"] > [aria-hidden="true"]')).toHaveCount(0);
}

type Segments = components['schemas']['SegmentsResponse'];

/** The suggested cross-tabs, in the order the patterns page offers them (src/pages/patterns/patterns.ts). */
const SUGGESTED = ['Internet × payment', 'Price × tenure', 'Tech support × contract'] as const;

/**
 * Opens the patterns page on the first suggested view that reports a group's churn.
 *
 * The suppression rule may hide every cell of a view on a small synthetic book
 * (the default Contract × tenure loses Two year to the cross-partition rule and
 * the rest to small or complementary groups), so a test of a reported group must
 * find one rather than assume the default shows it.
 */
export async function openReported(page: Page): Promise<Segments> {
  const isSegments = (r: { url(): string }) => new URL(r.url()).pathname === '/api/segments';
  const first = page.waitForResponse(isSegments);
  await page.goto('/patterns');
  let body = (await (await first).json()) as Segments;
  await settled(page);
  for (const label of SUGGESTED) {
    if (body.cells.some((cell) => !cell.suppressed)) return body;
    const next = page.waitForResponse(isSegments);
    await page.getByRole('button', { name: label, exact: true }).click();
    body = (await (await next).json()) as Segments;
    await settled(page);
  }
  if (body.cells.some((cell) => !cell.suppressed)) return body;
  throw new Error('no suggested view reports a group on this book');
}

/**
 * Answers every request ``url`` matches with ``status`` and ``body`` (the app
 * retries some failures by itself) until the returned function is called.
 */
export async function refuse(
  page: Page,
  url: Match,
  status: number,
  body: ErrorResponse,
  headers: Record<string, string> = {},
): Promise<() => Promise<void>> {
  const handler = (route: Route) => route.fulfill({ status, json: body, headers });
  await page.route(url, handler);
  return () => page.unroute(url, handler);
}

/**
 * Holds the next response ``url`` matches until the returned function is
 * called; the request itself reaches the server at once.
 */
export async function hold(page: Page, url: Match): Promise<() => void> {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    url,
    async (route) => {
      const response = await route.fetch();
      await gate;
      await route.fulfill({ response });
    },
    { times: 1 },
  );
  return () => {
    release();
  };
}

export const test = base.extend<{ guard: Guard }>({
  guard: [
    async ({ page, baseURL }, use, testInfo) => {
      const origin = new URL(baseURL ?? '').origin;
      const problems: string[] = [];
      const allowedConsole: { text: RegExp; path: RegExp | null }[] = [];
      const allowedFailures: RegExp[] = [];
      const allowedCsp: string[] = [];
      const inFlight = new Map<string, number>();
      const keys = new WeakMap<Request, string>();
      const bodies: Promise<Body | null>[] = [];
      const key = (method: string, url: string, data: string | null) =>
        `${method} ${url} ${data ?? ''}`;

      page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
      page.on('console', (message) => {
        if (message.type() !== 'error') return;
        const text = message.text();
        const { url } = message.location();
        const path = URL.canParse(url) ? new URL(url).pathname : null;
        const allowed = allowedConsole.some(
          (allow) =>
            allow.text.test(text) &&
            (allow.path === null ? path === null : path !== null && allow.path.test(path)),
        );
        if (!allowed) problems.push(`console error: ${text} (${url || 'no address'})`);
      });
      await page.exposeBinding('__e2eCspViolation', (_source, directive: string) => {
        if (!allowedCsp.includes(directive)) problems.push(`CSP violation: ${directive}`);
      });
      await page.addInitScript(() => {
        document.addEventListener('securitypolicyviolation', (event) => {
          const report = (window as unknown as { __e2eCspViolation: (text: string) => void })
            .__e2eCspViolation;
          report(event.effectiveDirective);
        });
      });

      // A duplicate is two of one document's requests at once. Each request is
      // counted under the document that sent it: a new one begins when the main
      // frame commits the address its navigation request asked for. The app
      // writing its state to the address (pushState) fires framenavigated too,
      // but sends no navigation request, so its requests stay in one document.
      let generation = 0;
      let navigation: string | null = null;
      const withoutHash = (url: string) => url.split('#')[0] ?? url;
      page.on('framenavigated', (frame) => {
        if (frame !== page.mainFrame() || withoutHash(frame.url()) !== navigation) return;
        navigation = null;
        generation += 1;
      });
      page.on('request', (request) => {
        const url = request.url();
        if (url.startsWith('data:') || url.startsWith('blob:')) return;
        if (new URL(url).origin !== origin) problems.push(`request to another origin: ${url}`);
        if (request.isNavigationRequest()) {
          if (request.frame() === page.mainFrame()) navigation = withoutHash(url);
          return;
        }
        const sent = key(request.method(), url, request.postData());
        const counted = `${String(generation)} ${sent}`;
        if (inFlight.has(counted)) problems.push(`sent again while in flight: ${sent}`);
        inFlight.set(counted, (inFlight.get(counted) ?? 0) + 1);
        keys.set(request, counted);
      });
      const settle = (request: Request) => {
        const counted = keys.get(request);
        if (counted === undefined) return;
        keys.delete(request);
        const left = (inFlight.get(counted) ?? 1) - 1;
        if (left > 0) inFlight.set(counted, left);
        else inFlight.delete(counted);
      };
      page.on('requestfinished', settle);
      page.on('requestfailed', (request) => {
        settle(request);
        const url = request.url();
        const failure = request.failure()?.errorText ?? 'failed';
        // Navigating away, or the app cancelling a superseded query, aborts a request.
        const aborted = failure === 'net::ERR_ABORTED' && url.startsWith(origin);
        if (!aborted && !allowedFailures.some((p) => p.test(url))) {
          problems.push(`request failed (${failure}): ${url}`);
        }
      });
      page.on('response', (response) => {
        if (!new URL(response.url()).pathname.startsWith('/api/')) return;
        bodies.push(
          response.text().then(
            (text) => ({
              url: response.url(),
              type: response.headers()['content-type'] ?? '',
              text,
            }),
            // A body is gone once the page has navigated past it.
            () => null,
          ),
        );
      });

      await use({
        allowConsole: (text, path) => allowedConsole.push({ text, path }),
        allowFailedRequest: (pattern) => allowedFailures.push(pattern),
        allowCspViolation: (directive) => allowedCsp.push(directive),
      });

      const publicDemo = testInfo.project.name === PUBLIC_PROJECT;
      for (const body of await Promise.all(bodies)) {
        if (!body) continue;
        problems.push(...leaks(body));
        if (publicDemo && body.text.includes(DATASET_PREFIX)) {
          problems.push(`${new URL(body.url).pathname}: a customer of the dataset`);
        }
      }
      if (!page.isClosed()) {
        const content = await page.content();
        if (datasetIds(content).length > 0)
          problems.push('the page shows an IBM Telco customer ID');
        if (publicDemo && content.includes(DATASET_PREFIX)) {
          problems.push('the page shows a customer of the dataset');
        }
      }
      expect(problems, 'what no page may do').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
