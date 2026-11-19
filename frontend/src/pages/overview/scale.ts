/**
 * Drawing scales for the overview's small charts. They place figures the API
 * returned; none of them makes a figure of its own.
 */

/** The largest rate, rounded up to the next tenth (at least 0.1): the top of a chart's bars. */
export function scaleTop(rates: readonly (number | null | undefined)[]): number {
  const largest = Math.max(0.1, ...rates.filter((r): r is number => typeof r === 'number'));
  return Math.ceil(largest * 10) / 10;
}

/** Arcs around a ring of ``pathLength`` 100, one per share, ``gap`` apart; a ``null`` share draws none. */
export function arcs(
  shares: readonly (number | null)[],
  gap: number,
): ({ dash: string; offset: number } | null)[] {
  let start = 0;
  return shares.map((share) => {
    if (share === null || share <= 0) return null;
    const length = share * 100;
    const drawn = Math.max(0, length - gap);
    const arc = { dash: `${drawn} ${100 - drawn}`, offset: -start };
    start += length;
    return arc;
  });
}

/** Where each of ``shares`` starts when they are laid end to end from 0. */
export function starts(shares: readonly number[]): number[] {
  return shares.map((_, i) => shares.slice(0, i).reduce((sum, share) => sum + share, 0));
}
