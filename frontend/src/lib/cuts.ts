/**
 * Which cuts of a threshold sweep a table lists.
 *
 * The dataset's own sweep is the whole 0.01 grid, too long for a table: every
 * 0.05 is listed, and every cut the chart marks. A public demo publishes its
 * sweep only at a few cuts (twenty churners and twenty stayers between any two),
 * and every one of them is listed.
 */

interface Thresholded {
  threshold: number;
}

/** Whether ``points`` are the whole 0.01 grid, one step after another. */
export function onTheGrid(points: readonly Thresholded[]): boolean {
  return points.every((point, i) => {
    const before = points[i - 1];
    return before === undefined || Math.abs(point.threshold - before.threshold - 0.01) < 1e-9;
  });
}

/** The rows of a sweep's table: see the module note. ``marked`` are indices into ``points``. */
export function tabledCuts<T extends Thresholded>(
  points: readonly T[],
  marked: readonly number[],
): T[] {
  if (!onTheGrid(points)) return [...points];
  return points.filter(
    (point, i) => Math.round(point.threshold * 100) % 5 === 0 || marked.includes(i),
  );
}
