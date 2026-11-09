/**
 * Scenario A: from the overview into the explorer. Every count the pages show
 * is compared with the API response that page received, never with a number
 * typed here (the synthetic model's figures are not stable across platforms).
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, settled, test } from './fixtures';

type Overview = components['schemas']['OverviewResponse'];
type CustomerPage = components['schemas']['CustomerPage'];

const int = (n: number) => n.toLocaleString('en-US');

/** "Showing 16–30 of 1,234": the range line of a listing page with ``shown`` rows. */
function showing(body: CustomerPage, shown = body.rows.length): string {
  const first = (body.page - 1) * body.page_size + 1;
  return `Showing ${int(first)}–${int(first + shown - 1)} of ${int(body.total)}`;
}

/**
 * The next /api/customers listing whose query ``matches``: a page of rows, not
 * the one-row request the selection summary makes for its figures.
 */
function listing(page: Page, matches: (query: URLSearchParams) => boolean) {
  return page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === '/api/customers' &&
      url.searchParams.get('page_size') !== '1' &&
      matches(url.searchParams)
    );
  });
}

async function overview(page: Page): Promise<Overview> {
  const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/overview');
  await page.goto('/');
  const body = (await (await served).json()) as Overview;
  await settled(page);
  return body;
}

test('the flagged count leads to an explorer holding exactly those customers', async ({ page }) => {
  const book = await overview(page);
  const flagged = book.flagged.customers;
  const seeAll = page.getByRole('link', {
    name: `See all ${int(flagged)} flagged, churned included, in the explorer`,
  });
  await expect(seeAll).toHaveAttribute('href', '/explorer?flagged=true');

  const listed = listing(page, (q) => q.get('flagged') === 'true' && q.get('page') === '1');
  await seeAll.click();
  const first = (await (await listed).json()) as CustomerPage;
  await settled(page);
  expect(first.total).toBe(flagged);
  await expect(page).toHaveURL(/\/explorer\?flagged=true$/);
  await expect(page.getByText(showing(first))).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Selection summary' }).getByRole('status'),
  ).toHaveText(`${int(flagged)} customers match, ${int(flagged)} flagged.`);
});

test('paging keeps the population, and the address alone restores what is shown', async ({
  page,
}) => {
  // The selection's own one-row request, for every flagged match: its figures are
  // what the summary must say, so it is waited on rather than read once.
  const chosen = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === '/api/customers' &&
      url.searchParams.get('page_size') === '1' &&
      url.searchParams.get('flagged') === 'true'
    );
  });
  await page.goto('/explorer?flagged=true');
  const figures = ((await (await chosen).json()) as CustomerPage).summary;
  await settled(page);
  const summary = page.getByRole('region', { name: 'Selection summary' }).getByRole('status');
  const before = `${int(figures.customers)} customers match, ${int(figures.flagged)} flagged.`;
  await expect(summary).toHaveText(before);

  const second = listing(page, (q) => q.get('flagged') === 'true' && q.get('page') === '2');
  await page.getByRole('button', { name: 'Next page' }).click();
  const body = (await (await second).json()) as CustomerPage;
  await settled(page);
  await expect(page).toHaveURL(/flagged=true/);
  await expect(page).toHaveURL(/page=2/);
  expect(body.page).toBe(2);
  await expect(page.getByText(showing(body))).toBeVisible();
  await expect(summary).toHaveText(before);
  const ids = await page
    .getByRole('region', { name: 'Customers' })
    .getByRole('button', {
      name: /^E2E-/,
    })
    .allTextContents();
  expect(ids.map((text) => text.split(' ')[0])).toEqual(body.rows.map((row) => row.id));

  await page.reload();
  await settled(page);
  await expect(page.getByText(showing(body))).toBeVisible();
  await expect(summary).toHaveText(before);
});

test('a key figure opens the overview queue on its bands alone', async ({ page }) => {
  await page.goto('/?q=E2E&page=2');
  await settled(page);
  const queued = listing(page, (q) => q.getAll('bands').join() === 'High,Critical');
  await page.getByRole('link', { name: /^High or Critical/ }).click();
  const body = (await (await queued).json()) as CustomerPage;
  await expect(page).toHaveURL(/bands=High&bands=Critical/);
  await expect(page).not.toHaveURL(/q=|page=/);
  await expect(page.getByRole('heading', { name: /^Retention queue/ })).toBeFocused();
  await expect(
    page.getByRole('region', { name: /^Retention queue/ }).getByRole('status'),
  ).toHaveText(
    `${int(body.total)} flagged: ${int(body.status_counts.active)} active, ${int(body.status_counts.unknown)} status unknown, in scope.`,
  );
});

test('reset brings the explorer back to the whole book, and Back returns', async ({ page }) => {
  const opened = listing(page, (q) => q.get('contract') === 'Two year');
  await page.goto('/explorer?contract=Two+year&flagged=false&page=2');
  // The page size the explorer asks for when the address names none.
  const { page_size: size } = (await (await opened).json()) as CustomerPage;
  await settled(page);
  // The whole book's first page may come from the cache: ask the API what it holds.
  const book = (await (
    await page.request.get(`/api/customers?page_size=${String(size)}`)
  ).json()) as CustomerPage;
  await page.getByRole('button', { name: 'Reset' }).click();
  await settled(page);
  await expect(page).toHaveURL(/\/explorer$/);
  await expect(page.getByText(showing(book))).toBeVisible();
  await page.goBack();
  await settled(page);
  await expect(page).toHaveURL(/contract=Two\+year/);
});
