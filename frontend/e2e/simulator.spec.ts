/**
 * Scenario E: the simulator shows the figures of the assumptions it sent. The
 * status names the assumptions of the response on screen; a late answer to
 * earlier assumptions never replaces a newer one; a value outside the served
 * bounds is never sent; and the address restores what was set.
 */
import type { Page } from '@playwright/test';
import type { components } from '../src/api/schema';
import { expect, hold, settled, test } from './fixtures';

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
