import { curve, measured, performance, recorded } from '../../test/performanceApi';
import {
  charted,
  contour,
  explored,
  f1Of,
  groups,
  indexOfLabel,
  nearestCut,
  precisionOf,
  PR,
  prX,
  prY,
  recallOf,
  segmentName,
  selectionScale,
  sweepPath,
  tabledCuts,
  type Cut,
} from './performance';

const points = curve().points;

describe('the explored cut', () => {
  it('is found by its label, since grid floats are inexact', () => {
    // 0.35 on the grid is 0.35000000000000003: a float comparison misses it.
    expect(points[35]?.threshold).not.toBe(0.35);
    expect(indexOfLabel(points, '0.35')).toBe(35);
    expect(indexOfLabel(points, '0.355')).toBe(-1);
  });

  it('is the address’s when the curve has it, and the served cut’s otherwise', () => {
    expect(explored(points, '0.50', 0.36)).toBe(50);
    expect(explored(points, '', 0.36)).toBe(36);
    // A label the grid does not hold falls back, as does a curve without it.
    expect(explored(points, '0.99', 0.36)).toBe(99);
    expect(explored(points.slice(0, 40), '0.50', 0.36)).toBe(36);
    expect(explored(points.slice(0, 30), '', 0.36)).toBe(-1);
  });
});

describe('undefined rates', () => {
  it('come from the counts, never from a served 0.0', () => {
    expect(precisionOf(0, 0)).toBe('—');
    expect(precisionOf(3, 0)).toBe('0.000');
    expect(precisionOf(512, 0.5766)).toBe('0.577');
    expect(recallOf(0, 0)).toBe('—');
    expect(recallOf(310, 0)).toBe('0.000');
    expect(f1Of(0, 310, 0)).toBe('—');
    expect(f1Of(5, 0, 0)).toBe('—');
    expect(f1Of(5, 310, 0)).toBe('0.000');
    expect(f1Of(5, 310, 0.63891)).toBe('0.639');
  });
});

describe('the precision–recall picture', () => {
  it('draws only cuts that flag someone, in cut order, recall across and precision up', () => {
    const empty = points.filter((p) => p.flagged === 0).length;
    expect(empty).toBeGreaterThan(0);
    const d = sweepPath(points);
    expect(d.match(/[ML]/g)).toHaveLength(points.length - empty);
    const first = points[0] as Cut;
    expect(
      d.startsWith(`M${prX(first.recall).toFixed(1)} ${prY(first.precision).toFixed(1)}`),
    ).toBe(true);
  });

  it('puts an equal-F1 contour where 2PR/(P+R) is that F1, from precision 1 to recall 1', () => {
    const d = contour(0.6);
    const pairs = [...d.matchAll(/[ML]([\d.]+) ([\d.]+)/g)].map((m) => [
      prX.invert(Number(m[1])),
      prY.invert(Number(m[2])),
    ]);
    expect(pairs.length).toBeGreaterThan(10);
    for (const [r = 0, p = 0] of pairs) expect((2 * p * r) / (p + r)).toBeCloseTo(0.6, 2);
    expect(pairs[0]?.[1]).toBeCloseTo(1, 2);
    expect(pairs.at(-1)?.[0]).toBeCloseTo(1, 2);
  });

  it('finds the drawn cut nearest the pointer, never one that flags nobody', () => {
    const at36 = points[36] as Cut;
    const fx = prX(at36.recall) / PR.width;
    const fy = prY(at36.precision) / PR.height;
    expect(nearestCut(points, fx, fy)).toBe(36);
    // At the origin, where a cut that flags nobody is served (precision and recall 0.0).
    const empty = points.filter((p) => p.flagged === 0 || p.precision > 0);
    const chosen = empty[nearestCut(empty, PR.left / PR.width, PR.bottom / PR.height)];
    expect(chosen?.flagged).toBeGreaterThan(0);
  });

  it('tables every 0.05 and every marked cut', () => {
    const rows = tabledCuts(points, [36, 41]).map((p) => Math.round(p.threshold * 100));
    expect(rows).toContain(36);
    expect(rows).toContain(41);
    expect(rows).toContain(35);
    expect(rows).not.toContain(37);
    expect(rows).toHaveLength(21 + 2);
  });
});

describe('the one-standard-error chart', () => {
  const candidates = performance().candidates ?? [];

  it('charts the candidates the rule kept, and lists the rest only in the table', () => {
    expect(charted(candidates).map((c) => c.model)).toEqual([
      'Random Forest',
      'Hist Gradient Boosting',
      'Logistic Regression',
    ]);
  });

  it('takes its axis from every whisker and the cutoff, not from fixed ticks', () => {
    const shown = charted(candidates);
    const scale = selectionScale(shown, 0.6633);
    const [low = 0, high = 0] = scale.domain();
    for (const c of shown) {
      expect(low).toBeLessThanOrEqual(c.cv_pr_auc - c.cv_se);
      expect(high).toBeGreaterThanOrEqual(c.cv_pr_auc + c.cv_se);
    }
    expect(low).toBeGreaterThan(0.6);
    const [first] = shown;
    if (!first) throw new Error('nothing charted');
    const wide = selectionScale([{ ...first, cv_se: 0.05 }], 0.6633).domain();
    expect(wide[0]).toBeLessThan(low);
  });
});

describe('segments', () => {
  it('keep the server’s order, grouped by dimension, with readable names', () => {
    const grouped = groups(measured().segments);
    expect(grouped.map((g) => g.dimension)).toEqual([
      'Contract',
      'InternetService',
      'SeniorCitizen',
      'gender',
    ]);
    expect(grouped[0]?.rows).toHaveLength(3);
    expect(segmentName('SeniorCitizen', '1')).toBe('Yes');
    expect(segmentName('InternetService', 'Fiber optic')).toBe('Fiber optic');
    // "None" would read as no risk, or no data: it is customers without internet.
    expect(segmentName('InternetService', 'No')).toBe('No internet');
    expect(groups(recorded().segments).map((g) => g.dimension)).toEqual(['Contract']);
  });
});
