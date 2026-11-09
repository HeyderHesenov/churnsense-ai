/**
 * Full-page screenshots of every page at a phone and a desktop width, for a
 * person to review; nothing is compared (fonts differ across platforms) and
 * nothing is committed. Opt-in: E2E_SCREENSHOTS=1 npx playwright test review.
 * They land in test-results/review/ (gitignored), from synthetic data only.
 */
import { settled, test } from './fixtures';
import { PAGES } from './pages';

test.skip(!process.env['E2E_SCREENSHOTS'], 'set E2E_SCREENSHOTS=1 to take review screenshots');

for (const width of [375, 1440]) {
  test(`every page at ${String(width)} px, for review`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const { path: address, label } of PAGES) {
      await page.goto(address);
      await settled(page);
      const name = label.toLowerCase().replaceAll(' ', '-');
      await page.screenshot({
        path: `test-results/review/${String(width)}-${name}.png`,
        fullPage: true,
      });
    }
  });
}
