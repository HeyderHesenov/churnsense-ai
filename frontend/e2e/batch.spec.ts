/**
 * Scenario C: a file of customers through the real contract. The demo file is
 * scored once however often the button is pressed, the page and the scored
 * CSV hold the API's rows with the API's row numbers, a refused file lists the
 * API's own problems, and no exported cell can run as a spreadsheet formula.
 */
import type { Download, Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { cells, expect, settled, test } from './fixtures';

type BatchScoreResponse = components['schemas']['BatchScoreResponse'];
type ErrorResponse = components['schemas']['ErrorResponse'];
type ModelInfo = components['schemas']['ModelInfoResponse'];

const isScore = (url: string) => new URL(url).pathname === '/api/batch/score';

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

/** One customer the contract accepts, by column. */
const VALID: Record<string, string> = {
  SeniorCitizen: '0',
  Partner: 'Yes',
  Dependents: 'No',
  tenure: '12',
  PhoneService: 'Yes',
  MultipleLines: 'No',
  InternetService: 'DSL',
  OnlineSecurity: 'No',
  OnlineBackup: 'Yes',
  DeviceProtection: 'No',
  TechSupport: 'No',
  StreamingTV: 'No',
  StreamingMovies: 'No',
  Contract: 'One year',
  PaperlessBilling: 'Yes',
  PaymentMethod: 'Mailed check',
  MonthlyCharges: '50.00',
  TotalCharges: '600.00',
};

/** A CSV of ``rows`` under ``columns``, every cell quoted. */
function csvOf(columns: readonly string[], rows: readonly Record<string, string>[]): string {
  const quoted = (value: string) => `"${value.replaceAll('"', '""')}"`;
  return [columns, ...rows.map((row) => columns.map((c) => row[c] ?? ''))]
    .map((line) => line.map(quoted).join(','))
    .join('\n');
}

async function featureColumns(page: Page): Promise<string[]> {
  const info = (await (await page.request.get('/model-info')).json()) as ModelInfo;
  return info.feature_columns;
}

async function upload(page: Page, name: string, csv: string): Promise<void> {
  await page.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: 'text/csv',
    buffer: Buffer.from(csv),
  });
  await expect(page.getByRole('region', { name: 'Validation' }).getByRole('status')).toHaveText(
    `Ready to score ${name}`,
  );
}

async function scoreDemo(page: Page): Promise<BatchScoreResponse> {
  await page.goto('/batch');
  await settled(page);
  await page.getByRole('button', { name: 'Use the demo file' }).click();
  const scored = page.waitForResponse((r) => isScore(r.url()));
  await page.getByRole('button', { name: 'Score file' }).dblclick();
  const body = (await (await scored).json()) as BatchScoreResponse;
  await expect(page.getByRole('region', { name: 'Scored rows' })).toBeVisible();
  return body;
}

test('the demo file is scored once, and the page shows the API’s rows', async ({ page }) => {
  const sent: string[] = [];
  page.on('request', (request) => {
    if (isScore(request.url())) sent.push(request.method());
  });
  const body = await scoreDemo(page);
  expect(sent).toEqual(['POST']);

  expect(body.summary.rows).toBe(120);
  await expect(page.getByRole('region', { name: 'Validation' }).getByRole('status')).toHaveText(
    `Accepted: ${String(body.summary.rows)} rows, ${String(body.columns)} columns`,
  );
  // Row numbers are each row's 0-based position in the file; the demo's IDs count the same way.
  for (const row of body.rows) expect(row.row).toBe(Number(row.customer_id?.slice('DEMO-'.length)));
  // Each band's count on the page is the response's, in the response's order.
  const bands = page.getByRole('list', { name: 'Rows by band' }).getByRole('listitem');
  await expect(bands).toHaveCount(body.summary.bands.length);
  for (const [i, band] of body.summary.bands.entries()) {
    const shown = ((await bands.nth(i).textContent()) ?? '').trim();
    expect(shown.toLowerCase().startsWith(band.name.toLowerCase()), shown).toBe(true);
    expect(shown.split(/\s+/).at(-1), shown).toBe(String(band.customers));
  }
  const first = body.rows.toSorted((a, b) => b.churn_probability - a.churn_probability)[0];
  const top = page.getByRole('table', { name: 'Scored rows' }).getByRole('row').nth(1);
  await expect(top.getByRole('cell').nth(0)).toHaveText(String(first?.row));
  await expect(top.getByRole('cell').nth(1)).toHaveText(first?.customer_id ?? '');
});

test('the scored CSV is the API’s, row for row, with no cell a spreadsheet would run', async ({
  page,
}) => {
  const body = await scoreDemo(page);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Scored CSV' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.csv$/);
  const csv = await text(file);
  expect(csv).toBe(body.csv);

  const rows = table(csv);
  expect(rows).toHaveLength(body.rows.length);
  const byRow = new Map(body.rows.map((row) => [String(row.row), row]));
  for (const row of rows) {
    const served = byRow.get(row['row'] ?? '');
    expect(served?.customer_id).toBe(row['customerID']);
    expect(Number(row['churn_probability'])).toBeCloseTo(served?.churn_probability ?? NaN, 12);
  }
  for (const row of rows) {
    for (const cell of Object.values(row)) expect(cell).not.toMatch(/^[=+@\t\r]|^-(?!\d)/);
  }
});

test('a formula-shaped value comes back in the scored CSV as plain text', async ({ page }) => {
  await page.goto('/batch');
  await settled(page);
  const columns = ['customerID', ...(await featureColumns(page))];
  // What src/churnsense/exports.py makes of each: a leading ' wherever a
  // spreadsheet (one splitting cells at ';' too) would start a formula.
  const defused: Record<string, string> = {
    '=HYPERLINK("http://e2e.invalid")': `'=HYPERLINK("http://e2e.invalid")`,
    '+1': "'+1",
    '@SUM(A1)': "'@SUM(A1)",
    '-cmd': "'-cmd",
    'E2E\t=1': "E2E\t'=1",
    'E2E;=1': "E2E;'=1",
  };
  const ids = Object.keys(defused);
  await upload(
    page,
    'formulas.csv',
    csvOf(
      columns,
      ids.map((id) => ({ ...VALID, customerID: id })),
    ),
  );
  const scored = page.waitForResponse((r) => isScore(r.url()));
  await page.getByRole('button', { name: 'Score file' }).click();
  const body = (await (await scored).json()) as BatchScoreResponse;
  // The response keeps each value as it was typed (a leading space or tab
  // aside: the contract trims those, so they are tested inside the value).
  expect(body.rows.map((row) => row.customer_id)).toEqual(ids);

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Scored CSV' }).click();
  const rows = table(await text(await download));
  expect(rows.map((row) => row['customerID'])).toEqual(ids.map((id) => defused[id]));
});

test('a refused file lists the API’s own problems, and nothing is scored', async ({
  page,
  guard,
}) => {
  guard.allowConsole(/status of 422/, /^\/api\/batch\/score$/);
  await page.goto('/batch');
  await settled(page);
  const columns = await featureColumns(page);
  const rows = [VALID, { ...VALID, tenure: 'twelve' }, VALID, { ...VALID, Contract: 'Weekly' }];
  const refused = page.waitForResponse((r) => isScore(r.url()));
  await upload(page, 'broken.csv', csvOf(columns, rows));
  await page.getByRole('button', { name: 'Score file' }).click();
  const response = await refused;
  expect(response.status()).toBe(422);
  const problems = ((await response.json()) as ErrorResponse).problems ?? [];
  expect(problems.length).toBeGreaterThanOrEqual(2);
  const validation = page.getByRole('region', { name: 'Validation' });
  for (const problem of problems) await expect(validation).toContainText(problem);
  await expect(page.getByRole('region', { name: 'Scored rows' })).toHaveCount(0);
});

test('the template carries exactly the served contract’s columns', async ({ page }) => {
  await page.goto('/batch');
  await settled(page);
  const columns = await featureColumns(page);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download template' }).click();
  const header = cells((await text(await download)).trim().split(/\r?\n/)[0] ?? '');
  for (const column of columns) expect(header).toContain(column);
});
