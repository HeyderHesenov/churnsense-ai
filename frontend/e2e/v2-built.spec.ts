/**
 * The V2 Preview as built (``make v2-preview-build``) and served
 * (``make v2-preview-serve``): every V2 address opens and reloads directly, the
 * dashboard is still there, and ``/api`` reaches the API - with the API's own
 * checks in front of it, which the forwarding must not weaken. The journey through
 * the preview is v2-preview.spec.ts, which runs against this build as well.
 */
import type { components } from '../src/api/v2.schema';
import { expect, settled, test } from './fixtures';

type AccountPage = components['schemas']['AccountPage'];
type PortfolioSummary = components['schemas']['PortfolioSummary'];

const PAGES = [
  { path: '/v2', heading: 'Retention workspace preview' },
  { path: '/v2/data-hub', heading: 'Data Hub' },
  { path: '/v2/portfolio', heading: 'Portfolio' },
  { path: '/v2/accounts', heading: 'Accounts' },
] as const;

const CSV = 'account_external_id,as_of_date,status,mrr,currency\nA-1,2026-10-31,active,10.50,USD\n';

test('every V2 address opens and reloads directly', async ({ page }) => {
  for (const { path, heading } of PAGES) {
    const opened = await page.goto(path);
    expect(opened?.status(), path).toBe(200);
    await settled(page);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(heading);

    const reloaded = await page.reload();
    expect(reloaded?.status(), `${path}, reloaded`).toBe(200);
    await settled(page);
    await expect(page).toHaveURL(path);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(heading);
  }
});

test('an account address opens and reloads directly', async ({ page, request }) => {
  const list = (await (await request.get('/api/v2/accounts?page_size=1')).json()) as AccountPage;
  const id = list.rows[0]?.account_id ?? '';
  expect(id).not.toBe('');
  await page.goto(`/v2/account?id=${id}`);
  await settled(page);
  await page.reload();
  await settled(page);
  await expect(page.getByRole('heading', { name: 'Snapshot history' })).toBeVisible();
});

test('the synthetic data is labelled with its own date', async ({ page }) => {
  for (const path of ['/v2/portfolio', '/v2/accounts']) {
    await page.goto(path);
    await settled(page);
    const banner = page.getByRole('note', { name: 'About this data' });
    await expect(banner).toContainText('Synthetic demo data');
    await expect(banner).toContainText('Snapshot 31 October 2026');
  }
});

test('the dashboard opens, and links to the preview', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  const nav = page.getByRole('navigation', { name: 'Sections' });
  await nav.getByRole('link', { name: /V2 Preview/ }).click();
  await settled(page);
  await expect(page).toHaveURL('/v2');
});

test('/api reaches the API, and its own checks still stand in front', async ({
  request,
  baseURL,
}) => {
  const summary = await request.get('/api/v2/portfolio/summary');
  expect(summary.status()).toBe(200);
  const { dataset } = (await summary.json()) as PortfolioSummary;
  expect(dataset).toMatchObject({ source: 'synthetic', as_of_date: '2026-10-31' });

  const upload = (headers: Record<string, string>) =>
    request.post('/api/v2/imports/preview', {
      headers,
      multipart: {
        file: { name: 'accounts.csv', mimeType: 'text/csv', buffer: Buffer.from(CSV) },
      },
    });
  const own = await upload({ Origin: baseURL ?? '' });
  expect(own.status()).toBe(200);
  expect(await own.json()).toMatchObject({ status: 'valid', committed: false });

  expect((await upload({ Origin: 'https://evil.example' })).status()).toBe(403);
  expect((await upload({ Origin: 'http://127.0.0.1:1' })).status()).toBe(403);
  expect((await upload({ 'Sec-Fetch-Site': 'cross-site' })).status()).toBe(403);
  const unknown = await request.get('/api/v2/nothing');
  expect(unknown.status()).toBe(404);
});

test('a page of another host name is not answered', async ({ request }) => {
  const answer = await request.get('/v2', { headers: { Host: 'evil.example' } });
  expect(answer.status()).toBe(403);
  expect(await answer.text()).not.toContain('id="root"');
});
