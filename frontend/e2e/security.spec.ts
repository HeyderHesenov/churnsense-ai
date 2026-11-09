/**
 * The browser boundary: every page is served under the strict policy, an inline
 * script cannot run, and an unknown address gets the app's own Not found page.
 */
import { expect, test } from './fixtures';
import { PAGES } from './pages';

/** The reviewed policy, written out: any loosening has to change this file too. */
const REVIEWED: Record<string, string[]> = {
  'default-src': ["'self'"],
  'script-src': ["'self'"],
  'style-src': ["'self'"],
  'img-src': ["'self'", 'data:'],
  'font-src': ["'self'"],
  'connect-src': ["'self'"],
  'frame-ancestors': ["'none'"],
  'base-uri': ["'none'"],
  'form-action': ["'self'"],
};

/** A policy as directive -> sorted sources, order and spacing aside. */
function directives(policy: string | undefined): Record<string, string[]> {
  const parts = (policy ?? '').split(';').map((part) => part.trim().split(/\s+/));
  return Object.fromEntries(
    parts.filter(([name]) => name).map(([name = '', ...sources]) => [name, sources.toSorted()]),
  );
}
const reviewed = Object.fromEntries(
  Object.entries(REVIEWED).map(([name, sources]) => [name, sources.toSorted()]),
);

for (const { path, label } of PAGES) {
  test(`${label} is served under the strict policy`, async ({ page }) => {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    const headers = response?.headers() ?? {};
    expect(directives(headers['content-security-policy'])).toEqual(reviewed);
    expect(headers['x-frame-options']).toBe('DENY');
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['cache-control']).toBe('no-store');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  });
}

test('an inline script is refused, and the guard sees the refusal', async ({ page, guard }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  guard.allowConsole(/Content Security Policy/, null);
  guard.allowCspViolation('script-src-elem');
  const refused = page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        document.addEventListener('securitypolicyviolation', (event) => {
          resolve(event.effectiveDirective);
        });
        const script = document.createElement('script');
        script.textContent = 'document.title = "ran"';
        document.body.append(script);
      }),
  );
  expect(await refused).toBe('script-src-elem');
  expect(await page.title()).not.toBe('ran');
});

test('an unknown address gets the app’s own Not found page, with a 404', async ({
  page,
  guard,
}) => {
  guard.allowConsole(/status of 404/, /^\/no-such\/page$/);
  const response = await page.goto('/no-such/page');
  expect(response?.status()).toBe(404);
  expect(directives(response?.headers()['content-security-policy'])).toEqual(reviewed);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Go to the executive overview' })).toBeVisible();
});

test('an address that only looks like another site is no page, and stays on this origin', async ({
  page,
  guard,
  baseURL,
}) => {
  const address = '//evil.example/explorer';
  guard.allowConsole(/status of 404/, /^\/\/evil\.example\/explorer$/);
  const response = await page.goto(`${baseURL ?? ''}${address}`);
  expect(response?.status()).toBe(404);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('There is no page here');
  await page.getByRole('link', { name: 'Go to the executive overview' }).click();
  await expect(page).toHaveURL(`${baseURL ?? ''}/`);
});

test('an unknown API address stays a JSON 404 even to a browser', async ({ page, guard }) => {
  guard.allowConsole(/status of 404/, /^\/api\/no-such-thing$/);
  const response = await page.goto('/api/no-such-thing');
  expect(response?.status()).toBe(404);
  expect(response?.headers()['content-type']).toContain('application/json');
  expect(await response?.json()).toMatchObject({ error: 'not_found' });
});
