/**
 * How the retention queue's table sits in its card, read from the page: what
 * a reader cannot see without scrolling, and what no scrolling would reveal.
 */
import type { Page } from '@playwright/test';

export interface QueueLayout {
  /** How far the table reaches past its box, in px (0: it fits). */
  sideways: number;
  /** Visible parts of cells and headers outside the queue's card. */
  outsideCard: number;
  /** Visible parts of cells and headers that their cell clips. */
  clipped: number;
  /** Explain links not wholly inside the card. */
  explainCut: number;
}

export function queueLayout(page: Page): Promise<QueueLayout> {
  return page.evaluate(() => {
    const queue = document.getElementById('queue');
    const table = queue?.querySelector('[role="table"]');
    const box = table?.parentElement;
    if (!queue || !table || !box) throw new Error('no queue table');
    const card = queue.getBoundingClientRect();
    const shown = Array.from(
      table.querySelectorAll<HTMLElement>('[role="cell"] *, [role="columnheader"] *'),
    ).filter((part) => {
      const r = part.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && !part.closest('.sr') && !part.classList.contains('sr');
    });
    const outside = (r: DOMRect) => r.left < card.left - 0.5 || r.right > card.right + 0.5;
    return {
      sideways: Math.max(0, box.scrollWidth - box.clientWidth),
      outsideCard: shown.filter((part) => outside(part.getBoundingClientRect())).length,
      clipped: shown.filter((part) => {
        const cell = part.closest('[role="cell"], [role="columnheader"]');
        if (!cell) return false;
        const r = part.getBoundingClientRect();
        const c = cell.getBoundingClientRect();
        return r.right > c.right + 0.5 || r.bottom > c.bottom + 0.5;
      }).length,
      explainCut: Array.from(table.querySelectorAll('a'))
        .filter((link) => link.textContent === 'Explain')
        .filter((link) => outside(link.getBoundingClientRect())).length,
    };
  });
}
