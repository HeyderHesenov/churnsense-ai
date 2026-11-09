/**
 * axe on every page, at desktop width and on a phone with the navigation open,
 * plus the Not found page and an error state. Every impact fails: a finding is
 * fixed, or listed in axe-exceptions.ts with its reason.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { AXE_EXCEPTIONS } from './axe-exceptions';
import { expect, refuse, settled, test } from './fixtures';
import { PAGES } from './pages';

/** WCAG 2.2 level A and AA, and the earlier versions' rules it includes. */
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function findings(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return violations.flatMap((violation) =>
    violation.nodes
      .filter(
        (node) =>
          !AXE_EXCEPTIONS.some(
            (e) => e.rule === violation.id && node.target.join(' ') === e.target,
          ),
      )
      .map((node) => `${violation.id} (${violation.impact ?? 'n/a'}): ${node.target.join(' ')}`),
  );
}

for (const { path, label } of PAGES) {
  test(`${label} passes axe at 1440 px`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(path);
    await settled(page);
    expect(await findings(page)).toEqual([]);
  });

  test(`${label} passes axe at 375 px with the navigation open`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(path);
    await settled(page);
    expect(await findings(page), 'closed').toEqual([]);
    await page.getByRole('button', { name: 'Open navigation menu' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await findings(page), 'open').toEqual([]);
  });
}

test('the Not found page passes axe', async ({ page, guard }) => {
  guard.allowConsole(/status of 404/, /^\/no-such-page$/);
  await page.goto('/no-such-page');
  await settled(page);
  expect(await findings(page)).toEqual([]);
});

test('an error state passes axe', async ({ page, guard }) => {
  guard.allowConsole(/status of 503/, /^\/api\/overview$/);
  await refuse(page, '**/api/overview', 503, {
    error: 'model_unavailable',
    detail: 'Refused by a test.',
    problems: [],
  });
  await page.goto('/');
  await expect(page.getByRole('alert')).toBeVisible();
  expect(await findings(page)).toEqual([]);
});
