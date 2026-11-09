/**
 * The states a healthy synthetic server never reaches, injected one response
 * at a time with the API's own error shapes: each page says what happened,
 * retries exactly what the client's policy retries (and nothing it must not),
 * and recovers once the server answers again.
 */
import type { Page, Request } from '@playwright/test';
import { expect, refuse, settled, test, type ErrorResponse } from './fixtures';

const refusal = (error: string): ErrorResponse => ({
  error,
  detail: 'Refused by a test.',
  problems: [],
});

/** Every request to ``path`` from now on, for counting and timing. */
function requestsTo(page: Page, path: string): { at: number; request: Request }[] {
  const seen: { at: number; request: Request }[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === path) seen.push({ at: Date.now(), request });
  });
  return seen;
}

test('a book that cannot be loaded says so, and recovers on retry', async ({ page, guard }) => {
  guard.allowConsole(/status of 500/, /^\/api\/customers$/);
  const allow = await refuse(
    page,
    (url) => url.pathname === '/api/customers',
    500,
    refusal('internal_error'),
  );
  await page.goto('/explorer');
  const alert = page.getByRole('region', { name: 'Customers' }).getByRole('alert');
  await expect(alert).toContainText('The customer book could not be loaded.');
  await allow();
  await alert.getByRole('button', { name: 'Try again' }).click();
  await settled(page);
  await expect(page.getByText(/^Showing 1–[\d,]+ of /)).toBeVisible();
});

test('withheld outcomes are said to be withheld, and never asked for again', async ({
  page,
  guard,
}) => {
  guard.allowConsole(/status of 503/, /^\/api\/segments$/);
  const seen = requestsTo(page, '/api/segments');
  await refuse(page, '**/api/segments?*', 503, refusal('outcomes_withheld'));
  await page.goto('/patterns');
  await expect(page.getByText('Observed outcomes are withheld')).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  expect(seen).toHaveLength(1);
});

test('attributions not yet cached say how they are made, and are never asked for again', async ({
  page,
  guard,
}) => {
  guard.allowConsole(/status of 503/, /^\/api\/explain\/global$/);
  const seen = requestsTo(page, '/api/explain/global');
  await refuse(page, '**/api/explain/global', 503, refusal('explanations_not_cached'));
  await page.goto('/explainability');
  await expect(page.getByText('No attributions are cached for the served model')).toBeVisible();
  expect(seen).toHaveLength(1);
});

for (const [status, error, headers, logged] of [
  [503, 'explanations_busy', {}, /status of 503/],
  [429, 'rate_limited', { 'Retry-After': '1' }, /status of 429/],
] as const) {
  test(`an explanation refused with ${String(status)} ${error} is asked once more, by itself`, async ({
    page,
    guard,
  }) => {
    guard.allowConsole(logged, /^\/api\/explain$/);
    const seen = requestsTo(page, '/api/explain');
    await page.route(
      '**/api/explain',
      (route) => route.fulfill({ status, json: refusal(error), headers }),
      { times: 1 },
    );
    await page.goto('/customer');
    await settled(page);
    await expect(
      page.getByRole('region', { name: 'Contribution waterfall' }).getByRole('list', {
        name: 'Contributions',
      }),
    ).toBeVisible();
    expect(seen).toHaveLength(2);
    if (status === 429) {
      // The second ask waits as long as the server said to.
      expect((seen[1]?.at ?? 0) - (seen[0]?.at ?? 0)).toBeGreaterThanOrEqual(900);
    }
  });
}

test('a dropped connection is said to have failed, and recovers on retry', async ({
  page,
  guard,
}) => {
  guard.allowFailedRequest(/\/api\/segments/);
  guard.allowConsole(/net::ERR_FAILED/, /^\/api\/segments$/);
  const handler = (route: import('@playwright/test').Route) => route.abort('failed');
  await page.route('**/api/segments?*', handler);
  await page.goto('/patterns');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('The breakdown could not be loaded.');
  await page.unroute('**/api/segments?*', handler);
  await alert.getByRole('button', { name: 'Try again' }).click();
  await settled(page);
  await expect(page.getByRole('table').first()).toBeVisible();
});
