/**
 * Scenario F: the performance page shows validation figures as validation
 * figures and no test figure at all (none is recorded for the synthetic model);
 * its tab, partition and explored cut live in the address (they survive a
 * reload and Back), and moving through them asks the server nothing more.
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, settled, test } from './fixtures';
import { thumbPixels } from './thumb';

type Performance = components['schemas']['PerformanceResponse'];

const int = (n: number) => n.toLocaleString('en-US');

async function open(
  page: Page,
  path = '/performance',
): Promise<
  Performance & {
    validation: NonNullable<Performance['validation']>;
    validation_evaluation: NonNullable<Performance['validation_evaluation']>;
  }
> {
  const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/performance');
  await page.goto(path);
  const body = (await (await served).json()) as Performance;
  await settled(page);
  if (!body.validation || !body.validation_evaluation)
    throw new Error('the e2e server serves no validation figures');
  return {
    ...body,
    validation: body.validation,
    validation_evaluation: body.validation_evaluation,
  };
}

test('the summary’s figures are validation’s, and the test partition is withheld', async ({
  page,
}) => {
  const body = await open(page);
  expect(body.test).toBeNull();
  const group = page.getByRole('group', { name: 'Validation figures' });
  await expect(group).toContainText('VALIDATION');
  await expect(group).toContainText(
    `${int(body.validation_evaluation.metrics.n)} customers, measured live`,
  );
  await expect(group).toContainText(body.validation_evaluation.metrics.roc_auc.toFixed(3));
  await expect(group).toContainText(body.validation_evaluation.ece.toFixed(3));
  await expect(group).not.toContainText(/Recall|Precision|Accuracy/);

  await page
    .getByRole('group', { name: 'Partition' })
    .getByRole('button', { name: 'Test' })
    .click();
  await expect(page).toHaveURL((url) => url.searchParams.get('part') === 'test');
  const note = page.getByRole('note', { name: 'Test evaluation' });
  await expect(note).toContainText('No test figures shown');
  await expect(note).toContainText('No test evaluation is recorded for this model.');
  await expect(page.getByRole('group', { name: 'Test figures' })).toHaveCount(0);
  await expect(page.getByRole('img', { name: /one square each/ })).toHaveCount(0);
});

test('the explored cut is the address’s, and its figures are the API’s at that cut', async ({
  page,
}) => {
  const body = await open(page, '/performance?tab=errors');
  const slider = page.getByRole('slider', { name: 'Explored cut' });
  await expect(slider).toHaveAttribute('aria-valuetext', body.threshold.toFixed(2));
  await expect(page.getByRole('table', { name: /^Errors at / })).toHaveCount(0);
  await slider.focus();
  await page.keyboard.press('ArrowRight');
  const label = ((Math.round(body.threshold * 100) + 1) / 100).toFixed(2);
  await expect(page).toHaveURL((url) => url.searchParams.get('cut') === label);
  const point = body.validation.points.find((p) => p.threshold.toFixed(2) === label);
  expect(point).toBeDefined();
  const readout = page.getByText(`At ${label}: `, { exact: false });
  await expect(readout).toContainText(`${int(point?.flagged ?? -1)} flagged`);
  await expect(readout).toContainText(`recall ${(point?.recall ?? -1).toFixed(3)}`);
  await expect(slider).toBeFocused();

  await page.reload();
  await settled(page);
  await expect(page.getByRole('slider', { name: 'Explored cut' })).toHaveAttribute(
    'aria-valuetext',
    label,
  );

  await page
    .getByRole('navigation', { name: 'Sections' })
    .getByRole('link', { name: 'Customer explorer' })
    .click();
  await settled(page);
  await page.goBack();
  await settled(page);
  await expect(page).toHaveURL((url) => url.searchParams.get('cut') === label);
  await expect(page.getByRole('slider', { name: 'Explored cut' })).toHaveAttribute(
    'aria-valuetext',
    label,
  );
});

test('the tabs move with the keyboard and Back, and ask the server nothing more', async ({
  page,
}) => {
  let asked = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/performance') asked += 1;
  });
  await open(page);
  const tabs = page.getByRole('tablist', { name: 'Performance sections' });
  await tabs.getByRole('tab', { name: /Summary/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'choice');
  await expect(tabs.getByRole('tab', { name: /Model choice/ })).toBeFocused();
  await expect(
    page.getByRole('region', { name: 'Model selection: the one-standard-error rule' }),
  ).toBeVisible();
  await page.keyboard.press('End');
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'calibration');
  await expect(page.getByRole('region', { name: /^Performance by segment at / })).toBeVisible();
  await page.keyboard.press('ArrowLeft');
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'errors');
  await page
    .getByRole('group', { name: 'Partition' })
    .getByRole('button', { name: 'Test' })
    .click();
  await expect(page.getByRole('note', { name: 'Test evaluation' })).toBeVisible();

  await page.goBack();
  await expect(page.getByRole('region', { name: 'Explore a cut' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'calibration');
  await page.goForward();
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'errors');
  await settled(page);
  expect(asked).toBe(1);
});

test('the explored cut keeps its thumb: a fader with a value is drawn as before', async ({
  page,
}) => {
  await open(page, '/performance?tab=errors');
  const slider = page.getByRole('slider', { name: 'Explored cut' });
  await expect(slider).not.toHaveAttribute('data-empty');
  expect(await thumbPixels(page, slider)).toBeGreaterThan(50);
});
