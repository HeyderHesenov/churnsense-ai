/**
 * The app as the deployed demo serves it (the ``public-demo`` project: a
 * server started with ``--public``, its "dataset" deleted before it served).
 *
 * Every page loads, says its customers are synthetic, and marks each one; the
 * observed figures are still there, as aggregates; and nothing a visitor can
 * reach - a page, an API route, an export, a lookup by ID - holds a customer
 * of the dataset. The guard every test carries (fixtures.ts) checks that last
 * part on every response and every page as well.
 */
import type { APIRequestContext, Download } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { components } from '../src/api/schema';
import { AXE_EXCEPTIONS } from './axe-exceptions';
import { cells, expect, openReported, settled, test } from './fixtures';
import { DATASET_PREFIX, DEMO_PREFIX } from './global-setup';
import { PAGES } from './pages';

type CustomerPage = components['schemas']['CustomerPage'];
type ModelInfo = components['schemas']['ModelInfoResponse'];
type DataSource = components['schemas']['DataSource'];
type ErrorResponse = components['schemas']['ErrorResponse'];

const DEMO_ID = /^DEMO-\d{6}$/;
const NO_OUTCOME = 'Synthetic · no outcome';
/** Which of the service's sentences each page prints. */
const NOTES: Record<string, readonly (keyof DataSource)[]> = {
  '/': ['notice', 'figures_note', 'evidence_note'],
  '/explorer': ['notice', 'figures_note'],
  '/patterns': ['evidence_note'],
  '/performance': ['evidence_note'],
  '/explainability': ['notice', 'evidence_note'],
  '/customer': ['notice'],
  '/simulator': ['evidence_note'],
};

async function source(request: APIRequestContext): Promise<DataSource> {
  const info = (await (await request.get('/api/model-info')).json()) as ModelInfo;
  if (!info.data_source) throw new Error('the service names no data source');
  return info.data_source;
}

async function text(download: Download): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

function table(csv: string): Record<string, string>[] {
  const [header = '', ...lines] = csv.trim().split(/\r?\n/);
  const columns = cells(header);
  return lines.map((line) => {
    const values = cells(line);
    return Object.fromEntries(columns.map((column, i) => [column, values[i] ?? '']));
  });
}

test('the service says its customers are synthetic, in sentences of its own', async ({
  request,
}) => {
  const stated = await source(request);
  expect(stated.customer_records).toBe('synthetic');
  expect(stated.customers).toBeGreaterThan(0);
  for (const key of ['title', 'notice', 'figures_note', 'evidence_note'] as const) {
    expect(stated[key], key).toBeTruthy();
  }
});

for (const { path, label } of PAGES) {
  test(`${label} loads and prints the service's sentences about its data`, async ({
    page,
    request,
  }) => {
    const stated = await source(request);
    await page.goto(path);
    await settled(page);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const wanted = NOTES[path];
    if (!wanted) {
      // The batch page lists no customer and shows no observed figure of the dataset.
      await expect(page.getByRole('note', { name: stated.title })).toHaveCount(0);
      return;
    }
    const note = page.getByRole('note', { name: stated.title });
    await expect(note).toBeVisible();
    for (const key of wanted) await expect(note).toContainText(String(stated[key]));
  });
}

test('the explorer lists generated customers only, each marked, with no partition to pick', async ({
  page,
}) => {
  await page.goto('/explorer');
  await settled(page);
  const customers = page.getByRole('table', { name: 'Customers' });
  const ids = await customers.getByRole('button', { name: /^DEMO-/ }).allTextContents();
  expect(ids.length).toBeGreaterThan(5);
  await expect(customers.getByText(NO_OUTCOME)).toHaveCount(ids.length);
  await expect(customers.getByText(/Churned ·|Stayed ·|Withheld ·/)).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Partition' })).toHaveCount(0);
  await expect(page.getByText('synthetic customers have no outcome')).toBeVisible();
});

test('the retention queue is of generated customers and says its figures are illustrative', async ({
  page,
  request,
}) => {
  const stated = await source(request);
  await page.goto('/');
  await settled(page);
  const queue = page.getByRole('region', { name: /^Retention queue/ });
  await expect(queue.getByRole('heading', { name: /^Retention queue/ })).toContainText(
    stated.title,
  );
  await expect(queue).toContainText(String(stated.figures_note));
  await expect(queue.getByText(NO_OUTCOME).first()).toBeVisible();
  await expect(queue.getByRole('combobox', { name: 'Partition' })).toHaveCount(0);
  await expect(page.getByText(`${stated.title} ·`).last()).toContainText('generated customers');
});

test('the scored export holds generated customers only, and no outcome', async ({ page }) => {
  await page.goto('/explorer');
  await settled(page);
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: /^Export/ }).click(),
  ]);
  expect(download.suggestedFilename()).toBe('churnsense_demo_customers.csv');
  const csv = await text(download);
  expect(csv).not.toContain(DATASET_PREFIX);
  const rows = table(csv);
  expect(rows.length).toBeGreaterThan(100);
  for (const row of rows) {
    expect(row['customerID']).toMatch(DEMO_ID);
    expect(row['partition']).toBe('synthetic');
    expect(row['outcome']).toBe('withheld');
    expect(row['status']).toBe('active');
  }
  expect(Object.keys(rows[0] ?? {})).not.toContain('Churn');
});

test('no route hands out a customer of the dataset', async ({ request }) => {
  const first = (await (await request.get('/api/customers?page_size=100')).json()) as CustomerPage;
  const paths = [
    '/api/overview',
    '/api/performance',
    '/api/threshold/sweep',
    '/api/segments?rows=Contract&cols=tenure',
    '/api/explain/global',
    '/api/customers/examples',
    '/api/customers/export',
    '/api/model-info',
    '/openapi.json',
    ...Array.from({ length: first.pages }, (_, i) => `/api/customers?page_size=100&page=${i + 1}`),
  ];
  let listed = 0;
  for (const path of paths) {
    const response = await request.get(path);
    expect(response.ok(), path).toBe(true);
    const body = await response.text();
    expect(body.includes(DATASET_PREFIX), `${path} names a dataset customer`).toBe(false);
    if (path.startsWith('/api/customers?')) {
      for (const row of (JSON.parse(body) as CustomerPage).rows) {
        expect(row.id).toMatch(DEMO_ID);
        expect([row.partition, row.outcome, row.status]).toEqual([
          'synthetic',
          'withheld',
          'active',
        ]);
        listed += 1;
      }
    }
  }
  expect(listed).toBe(first.total);
});

test('a customer of the dataset cannot be looked up, listed, exported or explained', async ({
  request,
  baseURL,
}) => {
  const ids = [
    `${DATASET_PREFIX}00001`,
    `${DATASET_PREFIX}00042`,
    `${DATASET_PREFIX}${'X'.repeat(28)}`,
  ];
  for (const id of ids) {
    const lookup = await request.get(`/api/customers/${id}`);
    expect(lookup.status(), id).toBe(404);
    expect(((await lookup.json()) as ErrorResponse).detail).not.toContain(id);
    const listed = (await (await request.get(`/api/customers?ids=${id}`)).json()) as CustomerPage;
    expect(listed.total).toBe(0);
    const exported = await (await request.get(`/api/customers/export?ids=${id}`)).text();
    expect(exported.trim().split(/\r?\n/)).toHaveLength(1);
    const explained = await request.post('/api/explain', {
      data: { customer_ids: [id] },
      headers: { Origin: baseURL ?? '' },
    });
    expect(explained.status()).toBe(404);
  }
  const searched = (await (
    await request.get(`/api/customers?q=${DATASET_PREFIX}`)
  ).json()) as CustomerPage;
  expect(searched.total).toBe(0);
});

test('a dataset customer opened by address is simply not there', async ({ page, guard }) => {
  guard.allowConsole(/status of 404/, /^\/api\/customers\//);
  await page.goto('/customer?id=NOPE-00001');
  await settled(page);
  await expect(
    page.getByRole('paragraph').filter({ hasText: 'There is no customer NOPE-00001 in the book.' }),
  ).toBeVisible();
});

test('explainability names its sample and its background as synthetic', async ({ page }) => {
  await page.goto('/explainability');
  await settled(page);
  const method = page.getByRole('region', { name: 'Method' });
  await expect(method).toContainText('synthetic demo customers, the same on every page');
  await expect(method).not.toContainText(/training customers|validation partition/);
});

test('a pinned pattern links to no customers: the dataset is not there to open', async ({
  page,
}) => {
  await openReported(page);
  // The first group whose churn is reported (a hidden one shows no rate).
  const reported = page.getByRole('table').getByRole('button').filter({ hasText: '%' }).first();
  await reported.click();
  await expect(reported).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('link', { name: /Open these/ })).toHaveCount(0);
  await expect(page.getByText(/customer explorer/)).toHaveCount(0);
});

test('the batch page compares no label, whoever is uploaded', async ({ page }) => {
  await page.goto('/batch');
  await settled(page);
  await page.getByRole('button', { name: /demo/i }).first().click();
  const score = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/batch/score');
  await page.getByRole('button', { name: /^Score/ }).click();
  const body = (await (await score).json()) as components['schemas']['BatchScoreResponse'];
  expect(body.summary.labels).toBeNull();
  expect(body.summary.labels_reason).toContain('Labels are not compared or shown');
  expect(body.rows.every((row) => row.label === null)).toBe(true);
  await expect(page.getByText(body.summary.labels_reason ?? '')).toBeVisible();
});

test('the sweep is published only at cuts with twenty churners and twenty stayers between them', async ({
  request,
}) => {
  // Two neighbouring cuts difference into the customers between them (E8).
  const sweep = (await (
    await request.get('/api/threshold/sweep')
  ).json()) as components['schemas']['SweepResponse'];
  const points = sweep.points;
  expect(points.length).toBeGreaterThan(1);
  expect(points.length).toBeLessThan(101);
  expect(points[0]?.threshold).toBe(0);
  points.forEach((point, i) => {
    const next = points[i + 1];
    expect(point.true_positives - (next?.true_positives ?? 0)).toBeGreaterThanOrEqual(20);
    expect(point.false_positives - (next?.false_positives ?? 0)).toBeGreaterThanOrEqual(20);
  });
  const performance = (await (
    await request.get('/api/performance')
  ).json()) as components['schemas']['PerformanceResponse'];
  const cuts = points.map((point) => point.threshold);
  for (const bin of performance.validation_evaluation?.reliability ?? []) {
    expect(cuts).toContain(bin.low);
  }
  expect(performance.calibration.uncalibrated).toBeNull();
});

/** WCAG 2.2 level A and AA, and the earlier versions' rules it includes. */
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

for (const { path, label } of PAGES) {
  test(`${label} passes axe with its notice, at 1440 and 375 px`, async ({ page }) => {
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await settled(page);
      const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      const found = violations.flatMap((violation) =>
        violation.nodes
          .filter(
            (node) =>
              !AXE_EXCEPTIONS.some(
                (e) => e.rule === violation.id && node.target.join(' ') === e.target,
              ),
          )
          .map((node) => `${violation.id}: ${node.target.join(' ')}`),
      );
      expect(found, `${String(width)} px`).toEqual([]);
      // The notice never makes the page scroll sideways.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `sideways scroll at ${String(width)} px`).toBeLessThanOrEqual(0);
    }
  });
}

test('the public server has no dataset customer even where the other server has thousands', async ({
  request,
}) => {
  const page = (await (await request.get('/api/customers?page_size=1')).json()) as CustomerPage;
  expect(page.rows[0]?.id.startsWith(DEMO_PREFIX)).toBe(true);
  expect(page.withheld_reason).toBeNull();
  expect(page.summary.observed).toBeNull();
});
