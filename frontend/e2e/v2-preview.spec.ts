/**
 * The V2 Preview, end to end: the Vite dev server built with the preview on,
 * forwarding to an API server with its V2 routes on (playwright.config.ts,
 * project ``v2-preview``). One reader's path through both tools - a file
 * checked in the Data Hub, then the synthetic portfolio and one account - and
 * axe on each page. Every API request is to an M1 or M2 route.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { expect, settled, test } from './fixtures';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** M1's preview and M2's three reads: the only API routes the preview may ask for. */
const ALLOWED = [
  /^\/api\/v2\/imports\/preview$/,
  /^\/api\/v2\/portfolio\/summary$/,
  /^\/api\/v2\/accounts$/,
  /^\/api\/v2\/accounts\/[^/]+$/,
];

/** A file whose required columns the server cannot match by name, and one status to translate. */
const CSV = [
  'account_id,snapshot_date,plan_state,monthly_value,currency,notes',
  'UPLOAD-ONLY-1,2026-10-31,live,100.50,USD,first',
  'UPLOAD-ONLY-2,2026-10-31,active,200,EUR,',
].join('\n');

async function axe(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return violations.flatMap((v) =>
    v.nodes.map((node) => `${v.id} (${v.impact ?? 'n/a'}): ${node.target.join(' ')}`),
  );
}

function recordApi(page: Page): string[] {
  const paths: string[] = [];
  page.on('request', (request) => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith('/api/')) paths.push(pathname);
  });
  return paths;
}

test('a file checked in the Data Hub, then the synthetic portfolio and one account', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const api = recordApi(page);

  await page.goto('/v2');
  await settled(page);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Retention workspace preview');
  await expect(page.getByText('Two separate tools.')).toBeVisible();
  expect(await axe(page), 'home').toEqual([]);

  // --- the Data Hub: suggested mapping, then the reader's own --------------------------
  await page.getByRole('link', { name: /^Data Hub/ }).click();
  await settled(page);
  await expect(page.getByText('Checked, not saved.')).toBeVisible();
  await page.getByLabel('Choose a CSV file to check').setInputFiles({
    name: 'accounts.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(CSV),
  });
  await expect(page.getByText('Map the required fields')).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'currency (#5)' })).toHaveValue('currency');

  for (const [column, field] of [
    ['account_id (#1)', 'account_external_id'],
    ['snapshot_date (#2)', 'as_of_date'],
    ['plan_state (#3)', 'status'],
    ['monthly_value (#4)', 'mrr'],
  ] as const) {
    await page.getByRole('combobox', { name: column }).selectOption(field);
  }
  await page.getByRole('button', { name: 'Add a status translation' }).click();
  await page.getByRole('textbox', { name: 'Value in your file 1' }).fill('live');
  await page.getByRole('combobox', { name: 'Read as' }).selectOption('active');
  await page.getByRole('button', { name: 'Check the file with this mapping' }).click();

  await expect(page.getByText('Every row passed')).toBeVisible();
  const counts = page.getByRole('group', { name: 'Row counts' });
  await expect(counts.getByText('Accepted').locator('..')).toContainText('2');
  await expect(page.getByRole('cell', { name: '$100.50' }).first()).toBeVisible();
  await expect(page.getByRole('cell', { name: '€200.00' }).first()).toBeVisible();
  await expect(page.getByText(/Nothing was imported or stored/)).toBeVisible();
  expect(await axe(page), 'data hub').toEqual([]);

  // --- the portfolio: synthetic, as of its fixed date; the upload is not in it ----------
  await page.getByRole('link', { name: /^Portfolio/ }).click();
  await settled(page);
  const banner = page.getByRole('note', { name: 'About this data' });
  await expect(banner).toContainText('Synthetic demo data');
  await expect(banner).toContainText('Snapshot 31 October 2026');
  await expect(page.getByText(/not a forecast of lost revenue/)).toBeVisible();
  expect(await axe(page), 'portfolio').toEqual([]);

  // --- the accounts, filtered from the portfolio --------------------------------------
  await page
    .getByRole('group', { name: 'Accounts by rule level' })
    .getByRole('link', { name: /Attention/ })
    .click();
  await settled(page);
  await expect(page).toHaveURL(/\/v2\/accounts\?level=attention$/);
  await expect(page.getByRole('button', { name: 'Attention' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.getByRole('combobox', { name: 'Currency' }).selectOption('USD');
  await expect(page).toHaveURL(/currency=USD/);
  const rows = page.getByRole('table').getByRole('row');
  await expect(rows.nth(1)).toContainText('Attention');
  await expect(rows.nth(1)).toContainText('$');
  await page.getByRole('combobox', { name: 'Order' }).selectOption('mrr_desc');
  await expect(page).toHaveURL(/sort=mrr_desc/);
  expect(await axe(page), 'accounts').toEqual([]);
  await expect(page.getByText('UPLOAD-ONLY-1')).toHaveCount(0);

  // --- one account --------------------------------------------------------------------
  const first = rows.nth(1).getByRole('link');
  const name = (await first.textContent()) ?? '';
  await first.click();
  await settled(page);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
  await expect(page.getByRole('heading', { name: 'Why this level' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Snapshot history' })).toBeVisible();
  await expect(
    page.getByRole('list', { name: 'Signals' }).getByRole('listitem').first(),
  ).toBeVisible();
  expect(await axe(page), 'account').toEqual([]);

  // Every request the preview made, before the dashboard's own begin.
  const asked = [...api];
  expect(asked.length).toBeGreaterThan(0);
  expect(
    asked.filter((path) => !ALLOWED.some((pattern) => pattern.test(path))),
    'requests outside M1 and M2',
  ).toEqual([]);

  await page.getByRole('link', { name: 'Back to the dashboard' }).click();
  await expect(page).toHaveURL(/\/$/);
  await settled(page);
});

test('the preview on a phone: its navigation opens as a drawer', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/v2/portfolio');
  await settled(page);
  await page.getByRole('button', { name: 'Open navigation menu' }).click();
  const drawer = page.getByRole('dialog', { name: 'Navigation' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('link', { name: /^Accounts/ })).toBeVisible();
  expect(await axe(page), 'drawer').toEqual([]);
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(page.getByRole('button', { name: 'Open navigation menu' })).toBeFocused();
  await page.getByRole('button', { name: 'Open navigation menu' }).click();
  await drawer.getByRole('link', { name: /^Accounts/ }).click();
  await settled(page);
  await expect(page).toHaveURL(/\/v2\/accounts$/);
});
