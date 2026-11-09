/**
 * Scenario F: the performance page shows validation figures as validation
 * figures and no test figure at all (none is recorded for the synthetic model),
 * and the explored cut lives in the address: it survives a reload and Back.
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, settled, test } from './fixtures';

type Performance = components['schemas']['PerformanceResponse'];

const int = (n: number) => n.toLocaleString('en-US');

async function open(
  page: Page,
  path = '/performance',
): Promise<Performance & { validation: NonNullable<Performance['validation']> }> {
  const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/performance');
  await page.goto(path);
  const body = (await (await served).json()) as Performance;
  await settled(page);
  if (!body.validation) throw new Error('the e2e server serves no validation curve');
  return { ...body, validation: body.validation };
}

test('the figures are the validation partition’s, and the test column is withheld', async ({
  page,
}) => {
  const body = await open(page);
  expect(body.test).toBeNull();
  const metrics = page.getByRole('table', { name: 'Metrics by partition' });
  // Accessible names, which keep the words apart that the markup's text runs together.
  const headers = metrics.getByRole('columnheader');
  await expect(headers).toHaveCount(2);
  await expect(headers.nth(0)).toHaveAccessibleName('Metric');
  await expect(headers.nth(1)).toHaveAccessibleName(
    `Validation ${int(body.validation.customers)} customers`,
  );
  await expect(metrics).not.toContainText(/\btest\b/i);
  const note = page.getByRole('note', { name: 'Test evaluation' });
  await expect(note).toContainText('No test figures shown');
  await expect(note).toContainText('No test evaluation is recorded for this model.');
});

test('the explored cut is the address’s, and its errors are the API’s at that cut', async ({
  page,
}) => {
  const body = await open(page);
  const slider = page.getByRole('slider', { name: 'Explored cut' });
  const start = body.threshold;
  await slider.focus();
  await page.keyboard.press('ArrowRight');
  const cut = (Math.round(start * 100) + 1) / 100;
  const label = cut.toFixed(2);
  await expect(page).toHaveURL((url) => url.searchParams.get('cut') === label);
  const point = body.validation.points.find((p) => p.threshold.toFixed(2) === label);
  expect(point).toBeDefined();
  const errors = page.getByRole('table', { name: `Errors at ${label}` });
  await expect(
    errors.getByRole('cell', { name: `${int(point?.true_positives ?? -1)} flagged, churned` }),
  ).toBeVisible();
  await expect(
    errors.getByRole('cell', { name: `${int(point?.false_negatives ?? -1)} not flagged, churned` }),
  ).toBeVisible();
  await expect(slider).toBeFocused();

  await page.reload();
  await settled(page);
  await expect(page.getByRole('table', { name: `Errors at ${label}` })).toBeVisible();

  await page
    .getByRole('navigation', { name: 'Sections' })
    .getByRole('link', { name: 'Customer explorer' })
    .click();
  await settled(page);
  await page.goBack();
  await settled(page);
  await expect(page).toHaveURL((url) => url.searchParams.get('cut') === label);
  await expect(page.getByRole('table', { name: `Errors at ${label}` })).toBeVisible();
});
