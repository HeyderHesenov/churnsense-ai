/**
 * Scenario B: from the explorer to one customer. The customer keeps its
 * identity through the link and Back, the figures are the API's, and an
 * explanation that arrives late never lands on the customer shown next.
 */
import type { Page, Request } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, hold, settled, test } from './fixtures';

type CustomerDetail = components['schemas']['CustomerDetail'];
type ExplainResponse = components['schemas']['ExplainResponse'];

/** "72.2%": the pages' one-decimal probability (inside 0.1%–99.9%). */
const percent = (p: number) => `${(p * 100).toFixed(1)}%`;
/** "+14.9" / "−4.4": the signed percentage points the attributions show. */
const points = (v: number) => {
  const shown = Number((v * 100).toFixed(1));
  return `${shown > 0 ? '+' : shown < 0 ? '−' : ''}${Math.abs(shown).toFixed(1)}`;
};

function detailOf(page: Page, id: string) {
  return page.waitForResponse((r) => new URL(r.url()).pathname === `/api/customers/${id}`);
}

/** An explanation request for exactly the customer ``id``. */
const explains = (id: string) => (r: Request) =>
  new URL(r.url()).pathname === '/api/explain' &&
  JSON.stringify((r.postDataJSON() as { customer_ids?: unknown } | null)?.customer_ids) ===
    JSON.stringify([id]);

const detailPanel = (page: Page) => page.getByRole('complementary', { name: 'Customer detail' });

/** The listing's first customer, as the explorer shows it. */
async function firstRow(page: Page): Promise<string> {
  const row = page
    .getByRole('region', { name: 'Customers' })
    .getByRole('button', { name: /^E2E-/ });
  return ((await row.first().textContent()) ?? '').split(' ')[0] ?? '';
}

test('a customer keeps its identity and its figures from the explorer to its own page', async ({
  page,
}) => {
  await page.goto('/explorer');
  await settled(page);
  const id = await firstRow(page);
  const served = detailOf(page, id);
  await page.getByRole('region', { name: 'Customers' }).getByRole('button', { name: id }).click();
  const detail = (await (await served).json()) as CustomerDetail;
  await expect(detailPanel(page).getByRole('heading', { level: 2 })).toHaveText(id);
  await expect(detailPanel(page).getByText(percent(detail.churn_probability))).toBeVisible();

  const link = detailPanel(page).getByRole('link', {
    name: `Open ${id} on the Single customer page`,
  });
  await expect(link).toHaveAttribute('href', `/customer?id=${id}`);
  await link.click();
  await settled(page);
  await expect(page).toHaveURL(`/customer?id=${id}`);
  await expect(
    page.getByText(`${id}: ${percent(detail.churn_probability)}, ${detail.risk_band} band`),
  ).toBeVisible();
  const prediction = page.getByRole('region', { name: 'Prediction' });
  await expect(prediction).toContainText(percent(detail.churn_probability));

  await page.goBack();
  await settled(page);
  await expect(page).toHaveURL(`/explorer?id=${id}`);
  await expect(detailPanel(page).getByRole('heading', { level: 2 })).toHaveText(id);
});

test('a late explanation of one customer never shows on the next', async ({ page }) => {
  await page.goto('/explorer');
  await settled(page);
  const first = await firstRow(page);
  await page
    .getByRole('region', { name: 'Customers' })
    .getByRole('button', { name: first })
    .click();
  await expect(detailPanel(page).getByRole('heading', { level: 2 })).toHaveText(first);

  // The first customer's explanation is held back; the reader moves on meanwhile.
  const release = await hold(page, '**/api/explain');
  const sent = page.waitForRequest(explains(first));
  const dropped = page.waitForEvent('requestfailed', explains(first));
  await detailPanel(page).getByRole('button', { name: 'Explain this customer' }).click();
  await sent;
  await detailPanel(page).getByRole('button', { name: 'Next customer in the list' }).click();
  const heading = detailPanel(page).getByRole('heading', { level: 2 });
  await expect(heading).not.toHaveText(first);
  const second = (await heading.textContent()) ?? '';
  // Moving on cancels the first request, so its answer can never land.
  expect((await dropped).failure()?.errorText).toBe('net::ERR_ABORTED');

  const explained = page.waitForResponse((r) => explains(second)(r.request()));
  await detailPanel(page).getByRole('button', { name: 'Explain this customer' }).click();
  const body = (await (await explained).json()) as ExplainResponse;
  release();

  const own = body.explanations.find((e) => e.customer_id === second);
  expect(own).toBeDefined();
  const bars = detailPanel(page).getByRole('list', { name: 'Attributions' }).getByRole('listitem');
  await expect(bars.first()).toBeVisible();
  const shown = (await bars.allTextContents()).map((text) => text.match(/[+−]?\d+\.\d$/)?.[0]);
  expect(shown).toEqual(own?.contributions.slice(0, shown.length).map((c) => points(c.shap_value)));
  await expect(heading).toHaveText(second);
});
