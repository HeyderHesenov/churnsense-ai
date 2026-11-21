/**
 * Scenario E: the simulator shows the figures of the assumptions it sent. The
 * status names the assumptions of the response on screen; a late answer to
 * earlier assumptions never replaces a newer one; a value outside the served
 * bounds is never sent; and the address restores what was set. The cut is a
 * fader over the response's own cuts, by keyboard as by pointer; the Advanced
 * controls hold the horizon and the margin, closed until opened.
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, hold, settled, test } from './fixtures';
import { thumbPixels } from './thumb';

type Sweep = components['schemas']['SweepResponse'];

const isSweep = (url: string) => new URL(url).pathname === '/api/threshold/sweep';
/** The offer cost a sweep request was sent for. */
const costOf = (url: string) => new URL(url).searchParams.get('cost');

function sweepFor(page: Page, cost: string) {
  return page.waitForResponse((r) => isSweep(r.url()) && costOf(r.url()) === cost);
}

/** The line that says which assumptions the figures on screen are for. */
const figuresFor = (page: Page) =>
  page.getByRole('region', { name: /^At 0\.\d\d$/ }).getByRole('status');
const cost = (page: Page) => page.getByRole('textbox', { name: 'Cost of one offer' });

async function open(page: Page, path = '/simulator'): Promise<void> {
  await page.goto(path);
  await settled(page);
  await expect(figuresFor(page)).toHaveText(/^Figures for a \$/);
}

test('the figures are the response to the assumptions just set, and the address keeps them', async ({
  page,
}) => {
  await open(page);
  const answered = sweepFor(page, '80');
  await cost(page).fill('80');
  const body = (await (await answered).json()) as Sweep;
  expect(body.assumptions.retention_offer_cost).toBe(80);
  const {
    offer_success_rate: success,
    expected_horizon_months: horizon,
    gross_margin: margin,
  } = body.assumptions;
  await expect(figuresFor(page)).toHaveText(
    `Figures for a $80 offer, ${String(Math.round(success * 100))}% success, ` +
      `a ${String(horizon)}-month horizon and ${String(Math.round(margin * 100))}% margin, ` +
      `on ${String(body.customers)} validation customers (${String(body.churners)} churned).`,
  );
  await expect(page).toHaveURL(/cost=80/);

  await page.reload();
  await settled(page);
  await expect(cost(page)).toHaveValue('80');
  await expect(figuresFor(page)).toHaveText(/^Figures for a \$80 offer/);
});

test('a request for earlier assumptions is dropped, so its answer never lands', async ({
  page,
}) => {
  await open(page);
  const release = await hold(page, (url) => isSweep(url.href) && costOf(url.href) === '80');
  const sent = page.waitForRequest((r) => isSweep(r.url()) && costOf(r.url()) === '80');
  await cost(page).fill('80');
  await sent;

  // The newer assumptions supersede the request still waiting for its answer.
  const dropped = page.waitForEvent(
    'requestfailed',
    (r) => isSweep(r.url()) && costOf(r.url()) === '80',
  );
  const newer = sweepFor(page, '90');
  await cost(page).fill('90');
  await newer;
  expect((await dropped).failure()?.errorText).toBe('net::ERR_ABORTED');
  release();
  await expect(figuresFor(page)).toHaveText(/^Figures for a \$90 offer/);
  await expect(page).toHaveURL(/cost=90/);
});

test('a value outside the served bounds is refused before it is sent', async ({ page }) => {
  await open(page);
  const sent: string[] = [];
  page.on('request', (request) => {
    if (isSweep(request.url())) sent.push(costOf(request.url()) ?? '');
  });
  const before = await figuresFor(page).textContent();
  await cost(page).fill('20000');
  await expect(cost(page)).toHaveAttribute('aria-invalid', 'true');
  // The settled draft is written to the address whatever its value
  // (src/app/useSettledDraft.ts); a request from it would be sent in the
  // render that follows, so once the address has it and a frame has passed,
  // anything it would send has been sent.
  await expect(page).toHaveURL(/cost=20000/);
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0))),
  );
  expect(sent).not.toContain('20000');
  // The figures stay, named for the assumptions they were computed for.
  await expect(figuresFor(page)).toHaveText(/^Not sent: correct the marked field\./);
  await expect(figuresFor(page)).toContainText(before ?? '');
});

test('the cut fader steps over the response’s cuts by keyboard, and the address keeps it', async ({
  page,
}) => {
  const served = page.waitForResponse((r) => isSweep(r.url()));
  await page.goto('/simulator');
  const sweep = (await (await served).json()) as Sweep;
  await settled(page);
  const slider = page.getByRole('slider', { name: 'Decision threshold' });
  const labels = sweep.points.map((point) => point.threshold.toFixed(2));
  const start = labels.indexOf(sweep.recommended.threshold.toFixed(2));
  expect(start).toBeGreaterThanOrEqual(0);
  await expect(slider).toHaveAttribute('aria-valuetext', labels[start] ?? '');
  await slider.focus();
  await page.keyboard.press('ArrowRight');
  const next = labels[start + 1] ?? '';
  await expect(page).toHaveURL((url) => url.searchParams.get('t') === next);
  const point = sweep.points[start + 1];
  await expect(page.getByText(`At ${next}: `, { exact: false })).toContainText(
    `${(point?.flagged ?? -1).toLocaleString('en-US')} flagged`,
  );
  await page.keyboard.press('Shift+ArrowRight');
  const far = labels[Math.min(labels.length - 1, start + 11)] ?? '';
  await expect(slider).toHaveAttribute('aria-valuetext', far);
  await page.keyboard.press('End');
  await expect(slider).toHaveAttribute('aria-valuetext', labels.at(-1) ?? '');
  await expect(slider).toBeFocused();

  await page.reload();
  await settled(page);
  await expect(page.getByRole('slider', { name: 'Decision threshold' })).toHaveAttribute(
    'aria-valuetext',
    labels.at(-1) ?? '',
  );
});

test('a cut typed with a decimal comma is taken when the API sent it', async ({ page }) => {
  const served = page.waitForResponse((r) => isSweep(r.url()));
  await page.goto('/simulator');
  const sweep = (await (await served).json()) as Sweep;
  await settled(page);
  const target = sweep.points[Math.floor(sweep.points.length / 3)]?.threshold.toFixed(2) ?? '';
  const field = page.getByRole('textbox', { name: 'Decision threshold value' });
  await field.fill(target.replace('.', ','));
  await field.press('Enter');
  await expect(page).toHaveURL((url) => url.searchParams.get('t') === target);
  await expect(page.getByRole('slider', { name: 'Decision threshold' })).toHaveAttribute(
    'aria-valuetext',
    target,
  );
});

test('the Advanced controls open, and an edit inside them is sent and echoed', async ({ page }) => {
  await open(page);
  const summary = page.locator('summary', { hasText: 'Advanced controls' });
  const horizon = page.getByRole('textbox', { name: 'Revenue horizon' });
  await expect(horizon).toBeHidden();
  await summary.click();
  await expect(horizon).toBeVisible();
  const answered = page.waitForResponse(
    (r) => isSweep(r.url()) && new URL(r.url()).searchParams.get('horizon') === '24',
  );
  await horizon.fill('24');
  const body = (await (await answered).json()) as Sweep;
  expect(body.assumptions.expected_horizon_months).toBe(24);
  await expect(figuresFor(page)).toContainText('a 24-month horizon');
});

test('the tabs keep the assumptions and the pins, and ask the server nothing more', async ({
  page,
}) => {
  let asked = 0;
  page.on('request', (request) => {
    if (isSweep(request.url())) asked += 1;
  });
  await open(page, '/simulator?cost=80');
  await page.getByRole('button', { name: 'Pin these figures' }).click();
  const tabs = page.getByRole('tablist', { name: 'Simulator sections' });
  await tabs.getByRole('tab', { name: /^Compare/ }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'compare');
  await expect(
    page.getByRole('table', { name: 'Pinned scenarios' }).getByRole('button', { name: /^Remove/ }),
  ).toHaveCount(1);
  await expect(page.getByRole('table', { name: 'The response at three cuts' })).toBeVisible();
  await tabs.getByRole('tab', { name: /^Compare/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page).toHaveURL((url) => url.searchParams.get('tab') === 'method');
  await expect(page.getByRole('region', { name: 'The served cut-off' })).toBeVisible();
  await page.goBack();
  await page.goBack();
  await expect(cost(page)).toHaveValue('80');
  await settled(page);
  expect(asked).toBe(1);
});

test('a rate the fader cannot place shows no thumb, not one at its first percent', async ({
  page,
}) => {
  await page.goto('/simulator?success=0.305');
  await settled(page);
  const rate = page.getByRole('slider', { name: 'Offer success rate' });
  await expect(rate).toHaveAttribute('aria-valuetext', 'no value');
  await expect(page.getByText('no value chosen')).toBeVisible();
  // The first percent's place, at the rail's start: empty.
  expect(await thumbPixels(page, rate, 40)).toBe(0);
  await rate.focus();
  await page.keyboard.press('Home');
  await expect(rate).toHaveAttribute('aria-valuetext', '0%');
  await page.getByRole('heading', { level: 1 }).click();
  expect(await thumbPixels(page, rate, 40)).toBeGreaterThan(50);
});

test('the cut shows no thumb until the sweep gives it one', async ({ page }) => {
  const release = await hold(page, (url) => isSweep(url.href));
  await page.goto('/simulator');
  const slider = page.getByRole('slider', { name: 'Decision threshold' });
  await expect(slider).toBeDisabled();
  expect(await thumbPixels(page, slider)).toBe(0);
  release();
  await expect(slider).toBeEnabled();
  await settled(page);
  await page.getByRole('heading', { level: 1 }).click();
  expect(await thumbPixels(page, slider)).toBeGreaterThan(50);
});

test('an empty rate fader takes a click at its first position, and a click elsewhere as that value', async ({
  page,
}) => {
  const rate = page.getByRole('slider', { name: 'Offer success rate' });
  const empty = async () => {
    await page.goto('/simulator?success=0.305');
    await settled(page);
    await expect(rate).toHaveAttribute('aria-valuetext', 'no value');
  };
  await empty();
  // The thumb's centre at the first value is 8 px in (Fader.module.css).
  await rate.click({ position: { x: 8, y: 17 } });
  await expect(rate).toHaveAttribute('aria-valuetext', '0%');
  await expect(page).toHaveURL((url) => url.searchParams.get('success') === '0');

  await empty();
  const box = await rate.boundingBox();
  if (!box) throw new Error('the range has no box');
  await rate.click({ position: { x: box.width / 2, y: 17 } });
  await expect(rate).not.toHaveAttribute('aria-valuetext', 'no value');
  await expect(rate).not.toHaveAttribute('aria-valuetext', '0%');
});

test('a rate’s problem sits on its own line under the bounds note, at 375 and 1440 px', async ({
  page,
}) => {
  for (const width of [375, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/simulator?success=0.305');
    await settled(page);
    const caption = page
      .getByRole('slider', { name: 'Offer success rate' })
      .locator('xpath=ancestor::*[@aria-busy or contains(@class, "fader")][1]')
      .locator('p')
      .first();
    const bounds = caption.getByText('0% to 100% · default 30%', { exact: true });
    const problem = caption.getByText('The success rate is in whole percents.', { exact: true });
    await expect(problem).toBeVisible();
    const [above, below] = [await bounds.boundingBox(), await problem.boundingBox()];
    if (!above || !below) throw new Error('the caption’s parts have no box');
    // A clear gap below the bounds note, and the problem starts a line of its own.
    expect(below.y - (above.y + above.height), `${String(width)} px`).toBeGreaterThanOrEqual(4);
    expect(below.x, `${String(width)} px`).toBeLessThanOrEqual(above.x + 1);
  }
});
