/**
 * The overview's tabs against the real server: each tab at its own address,
 * Back and Forward through them, the arrow keys, and what each asks of the
 * API: the summary one queue listing and no explanation; the queue tab, once
 * opened, that listing again from the cache and one explanation, never sent
 * twice however the reader moves.
 */
import type { Page, Request } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { expect, settled, test } from './fixtures';
import { queueLayout } from './queueLayout';

/**
 * Every API request the page sends, and a way to wait until none is in flight
 * and none has started for half a second: what lets a test say a request was
 * never sent, not merely not sent yet.
 */
function apiRequests(page: Page) {
  const sent: Request[] = [];
  let inFlight = 0;
  let last = Date.now();
  const isApi = (request: Request) => new URL(request.url()).pathname.startsWith('/api/');
  const done = (request: Request) => {
    if (!isApi(request)) return;
    inFlight -= 1;
    last = Date.now();
  };
  page.on('request', (request) => {
    if (!isApi(request)) return;
    sent.push(request);
    inFlight += 1;
    last = Date.now();
  });
  page.on('requestfinished', done);
  page.on('requestfailed', done);
  const path = (request: Request) => new URL(request.url()).pathname;
  return {
    explains: () => sent.filter((r) => path(r) === '/api/explain' && r.method() === 'POST'),
    listings: () =>
      sent.filter((r) => {
        const url = new URL(r.url());
        return (
          url.pathname === '/api/customers' &&
          !url.searchParams.has('ids') &&
          url.searchParams.get('page_size') !== '1'
        );
      }),
    segments: () => sent.filter((r) => path(r) === '/api/segments'),
    idle: () =>
      expect
        .poll(() => inFlight === 0 && Date.now() - last >= 500, { intervals: [100] })
        .toBe(true),
  };
}

const TABS = [
  { value: null, name: /^Summary/, region: 'Key figures' },
  {
    value: 'distribution',
    name: /^Risk distribution/,
    region: 'Where the book sits on the probability scale',
  },
  { value: 'associations', name: /^Associations/, region: 'Churn by segment' },
  { value: 'queue', name: /^Retention queue/, region: /^Retention queue/ },
] as const;

for (const { value, name, region } of TABS) {
  test(`the ${value ?? 'summary'} tab opens from its address`, async ({ page }) => {
    await page.goto(value === null ? '/' : `/?tab=${value}`);
    await settled(page);
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('region', { name: region })).toBeVisible();
  });
}

test('Back and Forward step through the tabs, keeping the queue’s filters', async ({ page }) => {
  await page.goto('/?bands=High');
  await settled(page);
  for (const { name } of TABS.slice(1)) {
    await page.getByRole('tab', { name }).click();
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
  }
  await expect(page).toHaveURL(/\/\?bands=High&tab=queue$/);
  for (const { name } of [...TABS].reverse().slice(1)) {
    await page.goBack();
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
  }
  await expect(page).toHaveURL(/\/\?bands=High$/);
  await page.goForward();
  await expect(page.getByRole('tab', { name: /^Risk distribution/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page).toHaveURL(/bands=High/);
});

test('the arrow keys move between tabs, and Home and End jump', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  await page.getByRole('tab', { name: /^Summary/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: /^Risk distribution/ })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('tab', { name: /^Retention queue/ })).toBeFocused();
  await expect(page).toHaveURL(/tab=queue/);
  await page.keyboard.press('Home');
  await expect(page.getByRole('tab', { name: /^Summary/ })).toBeFocused();
  await expect(page).not.toHaveURL(/tab=/);
});

test('the summary asks for one queue listing and no explanation; the queue tab, for one explanation', async ({
  page,
}) => {
  const api = apiRequests(page);
  await page.goto('/');
  await settled(page);
  const card = page.getByRole('region', { name: /^Customers to call first/ });
  await expect(card.getByRole('link', { name: /^E2E-|^DEMO-/ })).toHaveCount(3);
  await api.idle();
  expect(api.explains()).toHaveLength(0);
  expect(api.listings()).toHaveLength(1);
  expect(api.segments()).toHaveLength(1);

  await card.getByRole('link', { name: 'View explanations in Retention Queue' }).click();
  await expect(page).toHaveURL(/\/\?tab=queue$/);
  await expect(page.getByRole('heading', { name: /^Retention queue/ })).toBeFocused();
  const table = page.getByRole('table', { name: 'Flagged customers' });
  await expect(table).toBeVisible();
  await expect(table.getByText('Loading attributions')).toHaveCount(0);
  await api.idle();
  // The summary's listing is the queue's first page: asked once, explained once, for its rows.
  expect(api.listings()).toHaveLength(1);
  expect(api.explains()).toHaveLength(1);
  const ids = (api.explains()[0]?.postDataJSON() as { customer_ids: string[] }).customer_ids;
  const rows = await table.getByRole('row').count();
  expect(ids).toHaveLength(rows - 1);
});

test('the queue tab opened from its address asks for one explanation', async ({ page }) => {
  const api = apiRequests(page);
  await page.goto('/?tab=queue');
  await settled(page);
  await expect(
    page.getByRole('table', { name: 'Flagged customers' }).getByText('Loading attributions'),
  ).toHaveCount(0);
  await api.idle();
  expect(api.explains()).toHaveLength(1);
});

test('no explanation is sent again across tabs, Back and Forward, or a return from another page', async ({
  page,
}) => {
  const api = apiRequests(page);
  await page.goto('/');
  await settled(page);
  await page.getByRole('tab', { name: /^Retention queue/ }).click();
  const table = page.getByRole('table', { name: 'Flagged customers' });
  await expect(table.getByText('Loading attributions')).toHaveCount(0);
  await api.idle();
  expect(api.explains()).toHaveLength(1);

  // Every tab, from the summary to the queue: one history entry each.
  for (const { name } of TABS) {
    await page.getByRole('tab', { name }).click();
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
  }
  // Back past them all to the summary the page opened on, then Forward to the queue again.
  const steps = TABS.length + 1;
  for (let i = 0; i < steps; i += 1) await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('tab', { name: /^Summary/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  for (let i = 0; i < steps; i += 1) await page.goForward();
  await expect(page).toHaveURL(/tab=queue$/);
  await expect(table).toBeVisible();
  // Away to a page that explains nobody, and back to the queue it left.
  await page.getByRole('link', { name: /^Model performance/ }).click();
  await settled(page);
  await page.goBack();
  await settled(page);
  await expect(table).toBeVisible();
  await expect(table.getByText('Loading attributions')).toHaveCount(0);
  await api.idle();
  expect(api.explains()).toHaveLength(1);
});

test('the card’s one link to the queue, followed by keyboard, puts the focus on the queue’s heading', async ({
  page,
}) => {
  await page.goto('/');
  await settled(page);
  const card = page.getByRole('region', { name: /^Customers to call first/ });
  await expect(card.getByRole('link', { name: /^See all/ })).toHaveCount(0);
  const link = card.getByRole('link', { name: 'View explanations in Retention Queue' });
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/\?tab=queue$/);
  await expect(page.getByRole('heading', { name: /^Retention queue/ })).toBeFocused();
});

test('a key figure opens the queue tab with the focus on its heading', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  await page.getByRole('link', { name: /^Flagged at/ }).click();
  await expect(page).toHaveURL(/tab=queue/);
  await expect(page.getByRole('heading', { name: /^Retention queue/ })).toBeFocused();
});

for (const width of [1440, 375]) {
  test(`at ${String(width)} px, the queue opened from down the summary is not under the tab strip`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/');
    await settled(page);
    const toQueue = page
      .getByRole('region', { name: /^Customers to call first/ })
      .getByRole('link', { name: 'View explanations in Retention Queue' });
    await toQueue.scrollIntoViewIfNeeded();
    await toQueue.click();
    const heading = page.getByRole('heading', { name: /^Retention queue/ });
    await expect(heading).toBeFocused();
    const strip = await page.getByRole('tablist', { name: 'Overview sections' }).boundingBox();
    const shown = await heading.boundingBox();
    expect(strip && shown).toBeTruthy();
    if (!strip || !shown) return;
    expect(shown.y).toBeGreaterThanOrEqual(strip.y + strip.height);
    expect(shown.y + shown.height).toBeLessThanOrEqual(800);
  });
}

test('no band card on the distribution tab cuts its figures, at any width', async ({ page }) => {
  for (const width of [1440, 1280, 1024, 768, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?tab=distribution');
    await settled(page);
    const cards = page
      .getByRole('region', { name: 'Where the book sits on the probability scale' })
      .locator('a[data-band]');
    await expect(cards).toHaveCount(4);
    const cut = await cards.evaluateAll((links) =>
      links
        .filter((link) =>
          Array.from(link.querySelectorAll('span'))
            // What a reader sees: the screen-reader description is clipped on purpose.
            .filter((part) => !part.classList.contains('sr'))
            .some(
              (part) =>
                part.getBoundingClientRect().right > link.getBoundingClientRect().right + 0.5,
            ),
        )
        .map((link) => link.getAttribute('data-band')),
    );
    expect(cut, `${String(width)} px`).toEqual([]);
  }
});

test('an explanation still unanswered when the reader leaves the page is not sent again on Back', async ({
  page,
}) => {
  const api = apiRequests(page);
  // The explanation is held until the reader has left the overview and come Back.
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/explain', async (route) => {
    await held;
    await route.continue();
  });
  // Never left holding a request, should the test fail before the release below.
  page.on('close', () => release());
  await page.goto('/?tab=queue');
  await settled(page);
  await expect.poll(() => api.explains().length).toBe(1);
  await page.getByRole('link', { name: /^Model performance/ }).click();
  await settled(page);
  await page.goBack();
  await expect(page.getByRole('tab', { name: /^Retention queue/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  release();
  const table = page.getByRole('table', { name: 'Flagged customers' });
  await expect(table).toBeVisible();
  await expect(table.getByText('Loading attributions')).toHaveCount(0);
  await api.idle();
  expect(api.explains()).toHaveLength(1);
});

for (const width of [320, 375]) {
  test(`at ${String(width)} px the queue is a list of cards, every figure in view, with no sideways scroll`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/?tab=queue');
    await settled(page);
    const table = page.getByRole('table', { name: 'Flagged customers' });
    await expect(table.getByText('Loading attributions')).toHaveCount(0);
    // Nothing wider than the screen, the table included.
    const overflow = await table.evaluate((node) => {
      const scroller = node.parentElement;
      return {
        table: node.scrollWidth - node.clientWidth,
        scroller: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
        page: document.documentElement.scrollWidth - window.innerWidth,
      };
    });
    expect(overflow).toEqual({ table: 0, scroller: 0, page: 0 });
    // The sort buttons stay on screen, in the header row.
    await table.getByRole('row').first().scrollIntoViewIfNeeded();
    for (const name of [/^Probability/, /^Tenure/, /^Monthly/, /^At risk/]) {
      await expect(table.getByRole('button', { name })).toBeInViewport({ ratio: 1 });
    }
    // Every cell of every row inside its card, and each figure named beside it.
    const rows = table.getByRole('row').filter({ has: page.getByRole('cell') });
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i += 1) {
      const row = rows.nth(i);
      const outside = await row.evaluate((node) => {
        const box = node.getBoundingClientRect();
        return Array.from(node.querySelectorAll('[role="cell"] *'))
          .filter((part) => !part.closest('.sr') && !part.classList.contains('sr'))
          .filter((part) => {
            const r = part.getBoundingClientRect();
            return r.width > 0 && (r.left < box.left - 0.5 || r.right > box.right + 0.5);
          }).length;
      });
      expect(outside, `row ${String(i)}`).toBe(0);
      for (const label of ['Tenure', 'Monthly', 'At risk / mo']) {
        await expect(row.getByText(label, { exact: true })).toBeVisible();
      }
    }
    // The table is still one table to assistive technology: its column names are all there.
    await expect(table.getByRole('columnheader')).toHaveCount(10);
  });
}

test('at 1440 px the queue is a table wholly inside its card: every column, the Explain link whole', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?tab=queue');
  await settled(page);
  const table = page.getByRole('table', { name: 'Flagged customers' });
  await expect(table.getByText('Loading attributions')).toHaveCount(0);
  expect(await queueLayout(page)).toEqual({
    sideways: 0,
    outsideCard: 0,
    clipped: 0,
    explainCut: 0,
  });
  // Nothing to scroll, so nothing says to, and the keyboard does not stop at the table's box.
  await expect(page.getByRole('region', { name: /scrolls sideways/ })).toHaveCount(0);
  await expect(page.getByText(/scroll sideways/)).toHaveCount(0);
  const header = table.getByRole('row').first();
  for (const name of ['Customer', 'Band', 'Strongest attributions', 'Outcome']) {
    await expect(header.getByText(name, { exact: true })).toBeVisible();
  }
  const first = table.getByRole('row').nth(1);
  await expect(first.getByRole('link', { name: /^Explain / })).toBeInViewport({ ratio: 1 });
  // The column names a card shows beside its figures are there, and hidden, at this width.
  await expect(first.getByText('Monthly', { exact: true })).toHaveCount(1);
  await expect(first.getByText('Monthly', { exact: true })).toBeHidden();
  const tops = await first
    .getByRole('cell')
    .evaluateAll((cells) => cells.map((cell) => Math.round(cell.getBoundingClientRect().top)));
  expect(new Set(tops).size).toBe(1);
});

for (const width of [768, 1024]) {
  test(`at ${String(width)} px the queue scrolls sideways as a labelled region the keyboard reaches and moves`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?tab=queue');
    await settled(page);
    const table = page.getByRole('table', { name: 'Flagged customers' });
    await expect(table.getByText('Loading attributions')).toHaveCount(0);
    const region = page.getByRole('region', { name: 'Flagged customers, scrolls sideways' });
    await expect(region).toHaveAttribute('tabindex', '0');
    await expect(
      page.getByText('Wider than the screen: scroll sideways for every column'),
    ).toBeVisible();
    // Wider than its box, yet no figure is cut or squeezed: it is all there to scroll to.
    const before = await queueLayout(page);
    expect(before.sideways).toBeGreaterThan(0);
    expect(before.clipped).toBe(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBe(0);
    // From the control before it, Tab stops on the table's box; the arrow keys then move it.
    await page.getByRole('combobox', { name: 'Partition' }).focus();
    await page.keyboard.press('Tab');
    await expect(region).toBeFocused();
    const left = () => region.evaluate((node) => node.scrollLeft);
    expect(await left()).toBe(0);
    for (let i = 0; i < 80 && before.sideways - (await left()) > 1; i += 1) {
      await page.keyboard.press('ArrowRight');
    }
    expect(await left()).toBeGreaterThanOrEqual(before.sideways - 1);
    // At the far end, every row's Explain link is whole inside the card.
    expect((await queueLayout(page)).explainCut).toBe(0);
    // And from the box, Tab goes on into the table.
    await page.keyboard.press('Tab');
    await expect(
      table.getByRole('checkbox', { name: 'Select all rows on this page' }),
    ).toBeFocused();
  });

  test(`the scrolling queue passes axe at ${String(width)} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?tab=queue');
    await settled(page);
    await expect(page.getByText('Loading attributions')).toHaveCount(0);
    await expect(page.getByRole('region', { name: /scrolls sideways/ })).toBeVisible();
    const { violations } = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .include('#queue')
      .analyze();
    expect(
      violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`),
    ).toEqual([]);
  });
}

for (const width of [1440, 375]) {
  test(`at ${String(width)} px every segment name on the associations tab is shown whole`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?tab=associations');
    await settled(page);
    const card = page.getByRole('region', { name: 'Churn by segment' });
    const tabs = card.getByRole('tab');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i += 1) {
      await tabs.nth(i).click();
      await expect(tabs.nth(i)).toHaveAttribute('aria-selected', 'true');
      const panel = card.getByRole('tabpanel');
      const cut = await panel.evaluate((node) => {
        const box = node.getBoundingClientRect();
        return Array.from(node.querySelectorAll('span'))
          .filter(
            (part) =>
              part.scrollWidth > part.clientWidth + 0.5 ||
              part.getBoundingClientRect().right > box.right + 0.5,
          )
          .map((part) => part.textContent);
      });
      expect(cut, `segment tab ${String(i)}`).toEqual([]);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBe(0);
  });
}
