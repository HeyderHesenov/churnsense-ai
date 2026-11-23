/**
 * The served build - the one every deployment ships - has no V2 Preview: no
 * link to it, no page under /v2, and no request to /api/v2. The preview exists
 * only in a dev build made with VITE_CHURNSENSE_V2_PREVIEW=1 (v2-preview.spec.ts).
 */
import { expect, settled, test } from './fixtures';

test('the served build has no V2 Preview', async ({ page, guard }) => {
  const v2: string[] = [];
  page.on('request', (request) => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith('/api/v2')) v2.push(pathname);
  });

  await page.goto('/');
  await settled(page);
  const nav = page.getByRole('navigation', { name: 'Sections' });
  await expect(nav.getByRole('link').first()).toBeVisible();
  await expect(nav.getByRole('link', { name: /V2 Preview/ })).toHaveCount(0);

  guard.allowConsole(/status of 404/, /^\/v2(\/data-hub|\/portfolio|\/accounts)?$/);
  for (const path of ['/v2', '/v2/data-hub', '/v2/portfolio', '/v2/accounts']) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(404);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('There is no page here');
  }
  expect(v2).toEqual([]);
});
