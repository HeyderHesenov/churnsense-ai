/**
 * Scenario H: the patterns page reports observed churn of training customers
 * only, shows exactly what the API reported and nothing it suppressed (not in
 * a cell, its sentence, the focus line or the ranking), and a pinned group
 * opens the explorer on exactly that group's training customers.
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, openReported, settled, test } from './fixtures';

type Segments = components['schemas']['SegmentsResponse'];

const int = (n: number) => n.toLocaleString('en-US');
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

async function open(page: Page, path = '/patterns'): Promise<Segments> {
  const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/segments');
  await page.goto(path);
  const body = (await (await served).json()) as Segments;
  await settled(page);
  return body;
}

const grid = (page: Page) => page.getByRole('table').first();
const cells = (page: Page) => grid(page).getByRole('cell').getByRole('button');

test('the figures are the training partition’s observed churn, as the API reported them', async ({
  page,
}) => {
  const body = await open(page);
  if (body.base_rate === null) throw new Error('the synthetic book withholds its outcomes');
  await expect(
    page.getByText(
      `Training partition · ${int(body.partition_customers)} of ${int(body.book_customers)} customers · ` +
        `${int(body.partition_churned)} churned · ${pct(body.base_rate)} observed churn`,
    ),
  ).toBeVisible();
  // Each column's margin, in the API's order, after the overall "All" column.
  const headers = grid(page).getByRole('columnheader');
  for (const [i, total] of body.column_totals.entries()) {
    const name =
      (await headers.nth(i + 1).getAttribute('aria-label')) ??
      (await headers.nth(i + 1).textContent());
    expect(name).toContain(int(total.n));
    if (!total.suppressed) expect(name).toContain(pct(total.rate));
  }
  await expect(page.getByRole('main')).not.toContainText(/probabilit|risk band|predicted/i);
});

test('a suppressed group’s churn appears nowhere: cell, sentence, focus line or ranking', async ({
  page,
}) => {
  const body = await open(page);
  const hidden = body.cells.filter((cell) => cell.suppressed);
  expect(hidden.length).toBeGreaterThan(0);
  const shown = cells(page);
  await expect(shown).toHaveCount(body.cells.length);

  // The ranking lists reported groups only; a hidden group is named there by no line.
  const ranked = page.getByRole('region', { name: 'Highest observed churn in this view' });
  let dashes = 0;
  for (let i = 0; i < body.cells.length; i += 1) {
    const cell = shown.nth(i);
    // The grid lists the API's cells in its order: a dash exactly where it suppressed one.
    const dash = ((await cell.textContent()) ?? '').startsWith('—');
    expect(
      dash,
      `cell ${String(i)} (${body.cells[i]?.row ?? ''} × ${body.cells[i]?.column ?? ''})`,
    ).toBe(body.cells[i]?.suppressed === true);
    if (!dash) continue;
    dashes += 1;
    await expect(cell).not.toHaveAccessibleName(/%|churned/);
    await expect(cell).not.toHaveAccessibleDescription(/%|churned/);
    // Nor in any attribute of the cell or inside it (a title, a data-*, an aria-*).
    const attributes = await cell.evaluate((button) =>
      [button, ...Array.from(button.querySelectorAll('*'))].flatMap((el) =>
        Array.from(el.attributes).map((a) => `${a.name}="${a.value}"`),
      ),
    );
    expect(attributes.filter((a) => /\d%|churned/.test(a))).toEqual([]);
    // The cell's sentence starts with the group's name: "Contract Month-to-month, tenure 0–3 months".
    const description = await cell.evaluate(
      (button) =>
        document.getElementById(button.getAttribute('aria-describedby') ?? '')?.textContent ?? '',
    );
    const group = description.split(':')[0] ?? '';
    expect(group).not.toBe('');
    await expect(ranked).not.toContainText(group);
    await cell.hover();
    const line = page.locator('p[aria-hidden="true"]').filter({ hasText: group });
    await expect(line).toHaveCount(1);
    await expect(line).not.toContainText(/\d%|churned/);
  }
  expect(dashes).toBe(hidden.length);
});

test('a pinned group opens the explorer on exactly its training customers', async ({ page }) => {
  const body = await openReported(page);
  // The first cell showing a rate: a reported group of a dimension the explorer filters.
  const shown = cells(page).filter({ hasText: '%' }).first();
  await shown.click();
  await expect(page).toHaveURL(/pr=.*&pc=/);
  const pinned = new URL(page.url()).searchParams;
  const group = body.cells.find(
    (cell) => cell.row === pinned.get('pr') && cell.column === pinned.get('pc'),
  );
  if (!group || group.suppressed) throw new Error('the pinned cell is not a reported group');

  const link = page.getByRole('link', {
    name: `Open these ${int(group.n)} training customers in the customer explorer`,
  });
  await expect(link).toHaveAttribute('href', /part=train/);
  await link.click();
  await settled(page);
  await expect(page).toHaveURL(/\/explorer\?.*part=train/);
  const summary = page.getByRole('region', { name: 'Selection summary' }).getByRole('status');
  await expect(summary).toContainText('customers match');
  expect((await summary.textContent())?.split(' ')[0]).toBe(int(group.n));
});
