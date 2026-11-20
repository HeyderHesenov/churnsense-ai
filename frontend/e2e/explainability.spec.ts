/**
 * Scenario G: the global figures and one customer's explanation are on the same
 * scale of the same model (the same base, base + contributions = the served
 * probability), the page shows exactly those figures, and switching customers
 * quickly never leaves the earlier customer's attributions on screen.
 */
import type { Locator, Page, Request } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, hold, settled, test } from './fixtures';

type GlobalExplanation = components['schemas']['GlobalExplanation'];
type ExplainResponse = components['schemas']['ExplainResponse'];

/** "+1.9" / "−18.8": the signed percentage points the page shows. */
const points = (v: number) => {
  const shown = Number((v * 100).toFixed(1));
  return `${shown > 0 ? '+' : shown < 0 ? '−' : ''}${Math.abs(shown).toFixed(1)}`;
};

const isExplain = (url: string) => new URL(url).pathname === '/api/explain';
const explainedFor = (page: Page, id: string) =>
  page.waitForResponse((r) => isExplain(r.url()) && (r.request().postData() ?? '').includes(id));
/** The customer an example link places: its address's ``id``. */
async function placedBy(link: Locator): Promise<string> {
  const href = (await link.getAttribute('href')) ?? '';
  return new URL(href, 'http://e2e').searchParams.get('id') ?? '';
}

const card = (page: Page) => page.getByRole('region', { name: 'One customer' });
const examples = (page: Page) => card(page).getByRole('navigation', { name: 'Example customers' });

async function open(page: Page): Promise<GlobalExplanation> {
  const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/explain/global');
  await page.goto('/explainability');
  const body = (await (await served).json()) as GlobalExplanation;
  await settled(page);
  return body;
}

test('the swarm shows the served ranking, in the served order, with the served figures', async ({
  page,
}) => {
  const global = await open(page);
  const rows = page.getByRole('group', { name: 'Attributes' }).getByRole('button');
  await expect(rows).toHaveCount(global.importance.length);
  for (const [i, row] of global.importance.entries()) {
    await expect(rows.nth(i)).toHaveAccessibleName(
      `${row.label} ${(row.mean_abs_shap * 100).toFixed(1)}`,
    );
  }
});

test('the swarm is as tall as its rows: every attribute sits on its own dots, above the legend', async ({
  page,
}) => {
  const global = await open(page);
  const rows = page.getByRole('group', { name: 'Attributes' }).getByRole('button');
  const plot = page.locator('[class*="swarmArea"] > svg[class*="swarmSvg"]');
  const area = await plot.boundingBox();
  if (!area) throw new Error('no swarm');
  expect(area.height).toBeCloseTo(global.importance.length * 26, 0);
  const last = await rows.last().boundingBox();
  if (!last) throw new Error('no last row');
  expect(last.y + last.height).toBeLessThanOrEqual(area.y + area.height + 1);
  const legend = await page.getByText('high value').boundingBox();
  if (!legend) throw new Error('no legend');
  expect(legend.y).toBeGreaterThanOrEqual(last.y + last.height);
});

test('one customer’s explanation shares the global base and adds up to its probability', async ({
  page,
}) => {
  const global = await open(page);
  const first = examples(page).getByRole('link').first();
  const id = await placedBy(first);
  const answered = explainedFor(page, id);
  await first.click();
  const body = (await (await answered).json()) as ExplainResponse;
  const own = body.explanations.find((e) => e.customer_id === id);
  if (!own) throw new Error(`no explanation of ${id}`);

  expect(body.model_sha256).toBe(global.model_sha256);
  expect(own.base_value).toBeCloseTo(global.base_value, 9);
  const sum = own.contributions.reduce((total, c) => total + c.shap_value, own.base_value);
  expect(sum).toBeCloseTo(own.churn_probability, 6);

  await expect(page).toHaveURL((url) => url.searchParams.get('id') === id);
  const table = card(page).getByRole('table', { name: `${id}: every contribution` });
  const shown = table.getByRole('row').filter({ has: page.getByRole('rowheader') });
  await expect(shown).toHaveCount(own.contributions.length);
  for (const [i, c] of own.contributions.entries()) {
    await expect(shown.nth(i).getByRole('rowheader')).toHaveText(c.label);
    await expect(shown.nth(i).getByRole('cell').last()).toHaveText(points(c.shap_value));
  }
});

test('switching customers quickly never leaves the earlier one’s attributions', async ({
  page,
}) => {
  await open(page);
  const links = examples(page).getByRole('link');
  const [a, b] = await Promise.all([placedBy(links.nth(0)), placedBy(links.nth(1))]);
  const release = await hold(page, (url) => isExplain(url.href));
  const ofA = (r: Request) => isExplain(r.url()) && (r.postData() ?? '').includes(a);
  const sent = page.waitForRequest(ofA);
  const dropped = page.waitForEvent('requestfailed', ofA);
  await links.nth(0).click();
  await sent;

  const second = explainedFor(page, b);
  await links.nth(1).click();
  const body = (await (await second).json()) as ExplainResponse;
  // Choosing another customer cancels the first request, so its answer can never land.
  expect((await dropped).failure()?.errorText).toBe('net::ERR_ABORTED');
  release();
  const own = body.explanations.find((e) => e.customer_id === b);
  const table = card(page).getByRole('table', { name: `${b}: every contribution` });
  await expect(table).toBeVisible();
  await expect(card(page).getByRole('table', { name: `${a}: every contribution` })).toHaveCount(0);
  const firstRow = table
    .getByRole('row')
    .filter({ has: page.getByRole('rowheader') })
    .first();
  await expect(firstRow.getByRole('cell').last()).toHaveText(
    points(own?.contributions[0]?.shap_value ?? NaN),
  );
  await expect(page).toHaveURL((url) => url.searchParams.get('id') === b);
});

test('the two tabs, by keyboard, ask for the cached figures once and the customer once', async ({
  page,
}) => {
  const sent: Request[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/explain')) sent.push(request);
  });
  await open(page);
  const first = examples(page).getByRole('link').first();
  const id = await placedBy(first);
  const answered = explainedFor(page, id);
  await first.click();
  await answered;
  await expect(card(page).getByRole('table')).toBeVisible();
  const strip = page.getByRole('tablist', { name: 'Explainability sections' });
  await strip.getByRole('tab', { name: /^Global attributions/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page).toHaveURL(/[?&]tab=rankings/);
  await expect(page.getByRole('region', { name: 'Two rankings, both published' })).toBeVisible();
  await expect(card(page)).toHaveCount(0);
  await page.goBack();
  await expect(card(page).getByRole('table')).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('region', { name: 'Two rankings, both published' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  const paths = sent.map((r) => `${r.method()} ${new URL(r.url()).pathname}`);
  expect(paths.filter((p) => p === 'GET /api/explain/global')).toHaveLength(1);
  expect(paths.filter((p) => p === 'POST /api/explain')).toHaveLength(1);
});
