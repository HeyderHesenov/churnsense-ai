/**
 * The three charts on their own: where each mark lands, from the served
 * figures. The page tests check what a reader is told; these check what is drawn.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { scaleLinear } from 'd3-scale';
import type { CustomerDetail, Explanation, GlobalExplanation } from '../../api/queries';
import { shownSign } from '../../lib/format';
import { EXAMPLES } from '../../test/customerApi';
import { explanationOf, globalExplanation, VOCABULARY } from '../../test/explainApi';
import { depNote, totalChargesNote } from './copy';
import { Dependence } from './Dependence';
import {
  categoryCenter,
  contributionDomain,
  contributionOf,
  dependenceOf,
  slopeLines,
  SWARM,
  type NumericDependence,
} from './explain';
import { Rankings } from './Rankings';
import { Swarm } from './Swarm';
import styles from './Explain.module.css';

const GLOBAL = globalExplanation();
const FIRST = EXAMPLES[0] as CustomerDetail;
const EXPLAINED: Explanation = explanationOf(FIRST);
/** The dependence chart's box (``D`` in Dependence.tsx). */
const BOX = { width: 520, height: 330, left: 50, right: 506, top: 14, bottom: 276 };

/** The y of every dot a path draws (one ``M`` per dot); none for an empty path. */
function dotYs(d: string | null): number[] {
  if (d === null || d === 'M0 0') return [];
  return [...d.matchAll(/M[\d.-]+ ([\d.-]+)/g)].map((m) => Number(m[1]));
}

function pathOf(container: HTMLElement, className: string | undefined): string | null {
  return container.querySelector(`path[class="${className}"]`)?.getAttribute('d') ?? null;
}

const cssVar = (el: Element, name: string) => (el as HTMLElement).style.getPropertyValue(name);

describe('the swarm', () => {
  const show = (ringed?: Explanation) =>
    render(<Swarm global={GLOBAL} feature="tenure" ringed={ringed} onPick={() => {}} />);

  it('draws each attribute’s dots inside its own row, in the served order', () => {
    const { container } = show();
    const rows = container.querySelectorAll('svg > g');
    expect(rows).toHaveLength(GLOBAL.importance.length);
    rows.forEach((row, i) => {
      const ys = [...row.querySelectorAll('path')].flatMap((p) => dotYs(p.getAttribute('d')));
      expect(ys).toHaveLength(GLOBAL.n_explained);
      for (const y of ys) {
        expect(y).toBeGreaterThanOrEqual(i * SWARM.row + SWARM.pad);
        expect(y).toBeLessThanOrEqual((i + 1) * SWARM.row - SWARM.pad);
      }
    });
  });

  it('rings the customer on each attribute’s row, at their own contribution', () => {
    show(EXPLAINED);
    const x = scaleLinear()
      .domain(contributionDomain(GLOBAL, EXPLAINED))
      .range([6, 400 - 6]);
    const rings = screen.getAllByTestId('ring');
    expect(rings).toHaveLength(GLOBAL.importance.length);
    rings.forEach((ring, i) => {
      const feature = GLOBAL.importance[i]?.feature ?? '';
      const own = contributionOf(EXPLAINED, feature);
      expect(cssVar(ring, '--row')).toBe(String(i));
      expect(Number(cssVar(ring, '--at'))).toBeCloseTo(x(own?.shap ?? NaN) / 400, 9);
    });
  });
});

describe('the dependence chart', () => {
  const show = (feature: string, ringed?: Explanation, global: GlobalExplanation = GLOBAL) =>
    render(<Dependence global={global} feature={feature} ringed={ringed} />);
  const slider = () => screen.getByRole('slider', { name: 'Group' });

  it.each(['tenure', 'PaperlessBilling'])(
    'colours each dot by the sign its contribution shows, not by its raw sign (%s)',
    (feature) => {
      const j = GLOBAL.features.indexOf(feature);
      // Rounding traps: ±0.0004 and 0 show "0.0", ±0.0005 show ±0.1.
      const traps = [0.0004, -0.0004, 0.0005, -0.0005, 0];
      const global = {
        ...GLOBAL,
        values: GLOBAL.values.map((row, i) =>
          row.map((v, k) => (k === j && i < traps.length ? (traps[i] ?? v) : v)),
        ),
      };
      const { container } = show(feature, undefined, global);
      const signs = global.values.map((row) => shownSign(row[j] ?? 0));
      const count = (sign: number) => signs.filter((s) => s === sign).length;
      expect(dotYs(pathOf(container, styles.raises))).toHaveLength(count(1));
      expect(dotYs(pathOf(container, styles.lowers))).toHaveLength(count(-1));
      expect(dotYs(pathOf(container, styles.none))).toHaveLength(count(0));
      expect(count(0)).toBeGreaterThanOrEqual(3);
    },
  );

  it('marks a lone mean without drawing a line to nowhere', () => {
    const { container } = show('tenure');
    const bins = (dependenceOf(GLOBAL, 'tenure') as NumericDependence).bins;
    expect(bins.filter((b) => b.mean !== null)).toHaveLength(1);
    expect(pathOf(container, styles.mean)).toBe('M0 0');
    expect(dotYs(pathOf(container, styles.meanMarker))).toHaveLength(1);
  });

  it('steps from the first group to the last served one', () => {
    show('tenure');
    const bins = (dependenceOf(GLOBAL, 'tenure') as NumericDependence).bins;
    expect(slider()).toHaveAttribute('min', '0');
    expect(slider()).toHaveAttribute('max', String(bins.length - 1));
  });

  it('keeps the slider on the last group when the served groups shrink under it', () => {
    const { rerender } = show('tenure');
    fireEvent.change(slider(), { target: { value: slider().getAttribute('max') } });
    const summary = dependenceOf(GLOBAL, 'tenure') as NumericDependence;
    const fewer: GlobalExplanation = {
      ...GLOBAL,
      dependence: GLOBAL.dependence.map((d) =>
        d.feature === 'tenure' ? { ...summary, bins: summary.bins.slice(0, 2) } : d,
      ),
    };
    rerender(<Dependence global={fewer} feature="tenure" ringed={undefined} />);
    expect(slider()).toHaveAttribute('value', '1');
    expect(slider().getAttribute('aria-valuetext')).toMatch(/^Tenure over 6\.9, up to 12\.8/);
  });

  it('names the served floor for a mean', () => {
    show('tenure', undefined, { ...GLOBAL, dependence_min_customers: 7 });
    expect(screen.getByText(depNote('numeric', 7))).toBeInTheDocument();
  });

  it('notes the collinearity of total charges, and only there', () => {
    const note = totalChargesNote(GLOBAL.total_charges_r) ?? '';
    const { unmount } = show('TotalCharges');
    expect(screen.getByText(note)).toBeInTheDocument();
    unmount();
    show('MonthlyCharges');
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });

  it('points the tooltip at a group’s mean, and at the top where there is none', () => {
    const { container } = show('tenure');
    const tip = () => container.querySelector('[data-visible]') as HTMLElement;
    fireEvent.focus(slider());
    const mark = screen.getByTestId('group-mark');
    expect(Number(cssVar(tip(), '--y'))).toBeCloseTo(Number(mark.getAttribute('cy')) / BOX.height);
    fireEvent.change(slider(), { target: { value: '1' } });
    expect(Number(cssVar(tip(), '--y'))).toBeCloseTo(BOX.top / BOX.height);
  });

  it('rings a customer at the centre of their category', () => {
    show('Contract', EXPLAINED);
    const levels = VOCABULARY.Contract ?? [];
    const k = levels.indexOf(FIRST.features.Contract);
    expect(k).toBeGreaterThanOrEqual(0);
    const ring = screen.getByTestId('ring');
    expect(Number(ring.getAttribute('cx'))).toBeCloseTo(
      categoryCenter(k, levels.length, BOX.left, BOX.right),
    );
  });

  it('widens both axes to keep a customer beyond the cached figures on the chart', () => {
    const far: Explanation = {
      ...EXPLAINED,
      contributions: [{ feature: 'tenure', label: 'Tenure', value: 100, shap_value: 0.9 }],
    };
    show('tenure', far);
    const ring = screen.getByTestId('ring');
    expect(Number(ring.getAttribute('cx'))).toBeCloseTo(BOX.right);
    expect(Number(ring.getAttribute('cy'))).toBeGreaterThanOrEqual(BOX.top);
  });
});

describe('the two rankings', () => {
  it('draws each ranked attribute’s line once, the studied one on top', () => {
    const { container } = render(<Rankings global={GLOBAL} feature="tenure" onPick={() => {}} />);
    const paths = [...container.querySelectorAll('svg path')];
    expect(paths).toHaveLength(slopeLines(GLOBAL).length);
    expect(paths.filter((p) => p.getAttribute('class') === styles.slopeOn)).toHaveLength(1);
  });
});
