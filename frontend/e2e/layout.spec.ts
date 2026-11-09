/**
 * Layout at the widths people use: no page scrolls sideways, the longest ID the
 * API accepts fits a 320 px phone, and a keyboard user can always see focus.
 */
import { expect, settled, test, WIDTHS } from './fixtures';
import { PAGES } from './pages';

/** The longest customer ID the API accepts; the e2e server gives one customer it. */
const LONG_ID = `E2E-${'X'.repeat(28)}`;

async function overflow(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/**
 * Every visible occurrence of ``id`` on the page that does not show in full:
 * past the viewport's edge, or cut by an ancestor that hides its overflow (an
 * ellipsis included). A scrolling ancestor keeps it reachable, so it counts as
 * shown; text only assistive technology reads (a 1 px clip) is not looked at.
 * Also returns how many occurrences were looked at, so none at all fails.
 */
async function clipped(
  page: import('@playwright/test').Page,
  id: string,
): Promise<{ seen: number; cut: string[] }> {
  return page.evaluate((text) => {
    const cut: string[] = [];
    let seen = 0;
    const edge = document.documentElement.clientWidth;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = (node as Text).data.indexOf(text);
      const parent = node.parentElement;
      if (at < 0 || !parent?.checkVisibility()) continue;
      const box = parent.getBoundingClientRect();
      if (box.width <= 1 || box.height <= 1) continue;
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + text.length);
      const shown = range.getBoundingClientRect();
      seen += 1;
      const name = `${parent.tagName.toLowerCase()}.${parent.className}`;
      let scrolls = false;
      for (let el: Element | null = parent; el; el = el.parentElement) {
        const { overflowX } = getComputedStyle(el);
        if (overflowX === 'auto' || overflowX === 'scroll') {
          scrolls = true;
          break;
        }
        if (overflowX !== 'hidden' && overflowX !== 'clip') continue;
        const frame = el.getBoundingClientRect();
        if (shown.left < frame.left - 0.5 || shown.right > frame.right + 0.5) {
          cut.push(`${name} cut by ${el.tagName.toLowerCase()}.${el.className}`);
          break;
        }
      }
      if (!scrolls && (shown.left < -0.5 || shown.right > edge + 0.5)) {
        cut.push(`${name} passes the viewport`);
      }
    }
    return { seen, cut };
  }, id);
}

for (const width of WIDTHS) {
  test(`no page scrolls sideways at ${String(width)} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const { path, label } of PAGES) {
      await page.goto(path);
      await settled(page);
      expect(await overflow(page), label).toBeLessThanOrEqual(0);
    }
  });
}

test('the longest customer ID fits a 320 px phone on every page that names it', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  for (const path of [
    `/customer?id=${LONG_ID}`,
    `/explorer?q=${LONG_ID}&id=${LONG_ID}`,
    `/explainability?id=${LONG_ID}`,
  ]) {
    await page.goto(path);
    await settled(page);
    await expect(page.getByText(LONG_ID).first()).toBeVisible();
    const { seen, cut } = await clipped(page, LONG_ID);
    expect(seen, path).toBeGreaterThan(0);
    expect(cut, path).toEqual([]);
    expect(await overflow(page), path).toBeLessThanOrEqual(0);
  }
});

test('focus is always visible to a keyboard user', async ({ page }) => {
  await page.goto('/explorer');
  await settled(page);
  // The skip link, the sidebar's links, then the page's own controls.
  for (let step = 0; step < 14; step += 1) {
    await page.keyboard.press('Tab');
    // Every ring is an outline (src/styles/base.css :focus-visible); a row draws
    // its button's on itself (:has(:focus-visible)), so an ancestor's counts.
    const ring = await page.evaluate(() => {
      const element = document.activeElement;
      if (!element || element === document.body) return { name: 'body', visible: false };
      const drawn = (el: Element) => {
        const style = getComputedStyle(el);
        return (
          style.outlineStyle !== 'none' &&
          Number.parseFloat(style.outlineWidth) > 0 &&
          !/^(transparent|rgba\(.*,\s*0\))$/.test(style.outlineColor)
        );
      };
      let visible = false;
      for (let el: Element | null = element; el && !visible; el = el.parentElement) {
        visible = drawn(el);
      }
      return {
        name: `${element.tagName.toLowerCase()} ${element.textContent.trim().slice(0, 40)}`,
        visible,
      };
    });
    expect(ring.visible, `focus ring on ${ring.name}`).toBe(true);
  }
});
