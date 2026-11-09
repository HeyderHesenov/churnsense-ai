/**
 * Keyboard-only use in a real browser, where jsdom cannot go: the skip link,
 * the phone navigation drawer (focus trap, ``inert``, Escape, focus return) and
 * where focus lands after a failed load is retried.
 */
import { expect, refuse, settled, test } from './fixtures';

const focusedId = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.activeElement?.id ?? '');

test('the first Tab offers the skip link, and it moves focus to the page', async ({ page }) => {
  await page.goto('/explorer');
  await settled(page);
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect.poll(() => focusedId(page)).toBe('main');
});

test('on a phone, the drawer holds focus, shuts the page out, and gives focus back', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto('/');
  await settled(page);
  const menu = page.getByRole('button', { name: 'Open navigation menu' });
  await menu.focus();
  await page.keyboard.press('Enter');

  const drawer = page.getByRole('dialog');
  await expect(drawer).toBeVisible();
  await expect(page.locator('main')).toHaveAttribute('inert', '');
  for (let step = 0; step < 12; step += 1) {
    await page.keyboard.press('Tab');
    await expect.poll(() => drawer.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(menu).toBeFocused();
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
});

test('on a phone, a page chosen in the drawer opens with the drawer shut', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto('/');
  await settled(page);
  await page.getByRole('button', { name: 'Open navigation menu' }).click();
  await page.getByRole('dialog').getByRole('link', { name: 'Churn patterns' }).click();
  await expect(page).toHaveURL(/\/patterns$/);
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
});

test('retrying a failed page by keyboard puts focus back on the page', async ({ page, guard }) => {
  guard.allowConsole(/status of 503/, /^\/api\/overview$/);
  const allow = await refuse(page, '**/api/overview', 503, {
    error: 'model_unavailable',
    detail: 'Refused by a test.',
    problems: [],
  });
  await page.goto('/');
  const retry = page.getByRole('alert').getByRole('button', { name: 'Try again' });
  await expect(retry).toBeVisible();
  await allow();
  await retry.focus();
  await page.keyboard.press('Enter');
  await settled(page);
  await expect(page.getByRole('alert')).toBeHidden();
  await expect.poll(() => focusedId(page)).toBe('main');
});
