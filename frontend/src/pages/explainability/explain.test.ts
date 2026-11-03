import type { Contribution, CustomerDetail, Explanation } from '../../api/queries';
import { EXAMPLES } from '../../test/customerApi';
import { explainResponse, explanationOf, globalExplanation, ROWS } from '../../test/explainApi';
import {
  BASELINE_TOLERANCE,
  categoryCenter,
  columnDomain,
  comparability,
  contributionDomain,
  contributionOf,
  dependenceOf,
  EXPLAIN_PARAMS,
  featureChange,
  groupsOf,
  jitter,
  meanSegments,
  modelRanks,
  pickExplanation,
  selectedFeature,
  slopeLines,
  strongest,
  SWARM,
  swarmPaths,
  valueClasses,
  valueDomain,
  type Dependence,
  type NumericBin,
} from './explain';

const GLOBAL = globalExplanation();
const FIRST = EXAMPLES[0] as CustomerDetail;
const EXPLAINED: Explanation = explanationOf(FIRST);

describe('the address', () => {
  it('reads a known feature and a customer ID, and nothing else', () => {
    expect(EXPLAIN_PARAMS.f.parse(['Contract'])).toBe('Contract');
    expect(EXPLAIN_PARAMS.f.parse(['contract'])).toBeNull();
    expect(EXPLAIN_PARAMS.f.parse(['customerID'])).toBeNull();
    expect(EXPLAIN_PARAMS.id.parse(['DEMO-0005'])).toBe('DEMO-0005');
    expect(EXPLAIN_PARAMS.id.parse(['DEMO 0005'])).toBe('');
    expect(EXPLAIN_PARAMS.id.parse(['A'.repeat(32)])).toBe('A'.repeat(32));
    expect(EXPLAIN_PARAMS.id.parse(['A'.repeat(33)])).toBe('');
  });

  it('studies the top of the served ranking until another feature is chosen', () => {
    expect(GLOBAL.importance[0]?.feature).toBe('tenure');
    expect(selectedFeature(GLOBAL, null)).toBe('tenure');
    expect(selectedFeature(GLOBAL, 'Contract')).toBe('Contract');
    // A feature the cache does not explain falls back too.
    const fewer = { ...GLOBAL, features: GLOBAL.features.filter((f) => f !== 'Contract') };
    expect(selectedFeature(fewer, 'Contract')).toBe('tenure');
  });

  it('leaves the top of the ranking out of the address', () => {
    expect(featureChange(GLOBAL, 'tenure')).toBeNull();
    expect(featureChange(GLOBAL, 'Contract')).toBe('Contract');
  });
});

describe('one customer beside the cached figures', () => {
  const response = explainResponse([explanationOf(EXAMPLES[1] as CustomerDetail), EXPLAINED]);

  it('picks the explanation by customer, never by position', () => {
    expect(pickExplanation(response, FIRST.id)).toBe(EXPLAINED);
    expect(pickExplanation(response, 'DEMO-9999')).toBeUndefined();
    expect(pickExplanation(undefined, FIRST.id)).toBeUndefined();
  });

  it('is ringed only on the same artifact, baseline and background', () => {
    expect(BASELINE_TOLERANCE).toBe(1e-9);
    expect(comparability(GLOBAL, response, EXPLAINED)).toBe('ok');
    expect(
      comparability(GLOBAL, explainResponse([], { model_sha256: 'b'.repeat(64) }), EXPLAINED),
    ).toBe('artifact');
    // No artifact named: it cannot be shown to be the same one, so it is not ringed.
    expect(comparability(GLOBAL, explainResponse([], { model_sha256: null }), EXPLAINED)).toBe(
      'artifact',
    );
    const shifted = { ...EXPLAINED, base_value: GLOBAL.base_value + 2e-9 };
    expect(comparability(GLOBAL, response, shifted)).toBe('baseline');
    const close = { ...EXPLAINED, base_value: GLOBAL.base_value + 0.5e-9 };
    expect(comparability(GLOBAL, response, close)).toBe('ok');
    expect(
      comparability(GLOBAL, explainResponse([], { background_customers: 99 }), EXPLAINED),
    ).toBe('background');
  });

  it('reads a feature’s contribution and value from the customer’s own explanation', () => {
    const tenure = EXPLAINED.contributions.find((c) => c.feature === 'tenure') as Contribution;
    expect(contributionOf(EXPLAINED, 'tenure')).toEqual({
      value: tenure.value,
      shap: tenure.shap_value,
    });
    expect(contributionOf(undefined, 'tenure')).toBeUndefined();
    expect(contributionOf({ ...EXPLAINED, contributions: [] }, 'tenure')).toBeUndefined();
  });

  it('names the strongest raises and lowers by the sign a reader sees', () => {
    const contributions = [
      { feature: 'tenure', label: 'Tenure', value: 2, shap_value: 0.2 },
      { feature: 'Contract', label: 'Contract', value: 'Month-to-month', shap_value: -0.1 },
      { feature: 'Partner', label: 'Partner', value: 'No', shap_value: 0.05 },
      { feature: 'TechSupport', label: 'Tech support', value: 'No', shap_value: 0.03 },
      // Rounds to "0.0": neither raises nor lowers.
      { feature: 'Dependents', label: 'Dependents', value: 'No', shap_value: -0.0004 },
    ];
    const picked = strongest({ ...EXPLAINED, contributions }, 2);
    expect(picked.raises.map((c) => c.feature)).toEqual(['tenure', 'Partner']);
    expect(picked.lowers.map((c) => c.feature)).toEqual(['Contract']);
  });
});

describe('the swarm', () => {
  it('spans zero, every cached contribution and the ringed customer', () => {
    const [low, high] = contributionDomain(GLOBAL, undefined);
    const all = GLOBAL.values.flat();
    expect(low).toBeLessThanOrEqual(Math.min(0, ...all));
    expect(high).toBeGreaterThanOrEqual(Math.max(0, ...all));
    const far = {
      ...EXPLAINED,
      contributions: [{ feature: 'tenure', label: 'Tenure', value: 2, shap_value: 0.43 }],
    };
    expect(contributionDomain(GLOBAL, far)[1]).toBeGreaterThanOrEqual(0.43);
    const positive = { ...GLOBAL, values: [[0.02, 0.05]] };
    expect(contributionDomain(positive, undefined)[0]).toBeLessThanOrEqual(0);
  });

  it('colours a numeric value by the third of the observed range it falls in', () => {
    // Tenure runs 1 to 72: up to 24.67 is low, up to 48.33 middle, above high.
    const classes = valueClasses(GLOBAL, 'tenure');
    expect(ROWS.map((r) => r.tenure)).toEqual([1, 2, 3, 5, 6, 12, 24, 36, 48, 60, 70, 72]);
    expect(classes).toEqual([
      'low',
      'low',
      'low',
      'low',
      'low',
      'low',
      'low',
      'mid',
      'mid',
      'high',
      'high',
      'high',
    ]);
    expect(new Set(valueClasses(GLOBAL, 'Contract'))).toEqual(new Set(['category']));
    const constant = { ...GLOBAL, x: GLOBAL.x.map((r) => ({ ...r, tenure: 5 })) };
    expect(new Set(valueClasses(constant, 'tenure'))).toEqual(new Set(['mid']));
  });

  it('puts a value on a third’s edge in the upper third', () => {
    const edges = { ...GLOBAL, x: [0, 1, 2, 3].map((tenure) => ({ ...GLOBAL.x[0], tenure })) };
    expect(valueClasses(edges, 'tenure')).toEqual(['low', 'mid', 'high', 'high']);
  });

  it('draws one path per colour, every customer once, inside its row', () => {
    const x = (v: number) => v * 1000 + 200;
    const column = [0.1, 0.1, 0.1, -0.05, 0];
    const paths = swarmPaths(column, ['low', 'low', 'mid', 'high', 'category'], x, 0);
    const dots = (d: string) => (d.match(/M/g) ?? []).length;
    expect([paths.low, paths.mid, paths.high, paths.category].map(dots)).toEqual([2, 1, 1, 1]);
    const ys = [
      ...paths.low.matchAll(/M[\d.]+ ([\d.]+)/g),
      ...paths.mid.matchAll(/M[\d.]+ ([\d.]+)/g),
    ].map((m) => Number(m[1]));
    // Three at one place are stacked apart, and stay inside the row.
    expect(new Set(ys).size).toBe(3);
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(SWARM.pad);
      expect(y).toBeLessThanOrEqual(SWARM.row - SWARM.pad);
    }
    expect(swarmPaths(column, ['low', 'low', 'mid', 'high', 'category'], x, 0)).toEqual(paths);
    // Row three sits three rows down.
    const third = swarmPaths([0.1], ['mid'], x, 2).mid;
    expect(Number(third.match(/M[\d.]+ ([\d.]+)/)?.[1])).toBeCloseTo(
      2 * SWARM.row + SWARM.row / 2,
      5,
    );
  });

  it('caps a dense stack at the row’s edge instead of spilling into the next', () => {
    const many = Array.from({ length: 40 }, () => 0.1);
    for (const rowIndex of [0, 3]) {
      const paths = swarmPaths(many, Array<'low'>(40).fill('low'), (v) => v * 1000, rowIndex);
      const ys = [...paths.low.matchAll(/M[\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
      const top = rowIndex * SWARM.row;
      expect(ys).toHaveLength(40);
      expect(Math.max(...ys)).toBeCloseTo(top + SWARM.row - SWARM.pad, 5);
      expect(Math.min(...ys)).toBeCloseTo(top + SWARM.pad, 5);
    }
  });

  it('stacks dots closer than a bucket apart, and leaves dots a bucket apart side by side', () => {
    const ys = (column: number[]) =>
      [...swarmPaths(column, ['low', 'low'], (v) => v, 0).low.matchAll(/M[\d.]+ ([\d.]+)/g)].map(
        (m) => Number(m[1]),
      );
    // 30 and 31 share the bucket round(x / 3) = 10; 30 and 36 do not.
    const [a, b] = ys([30, 31]);
    expect(a).not.toBe(b);
    expect(ys([30, 36])).toEqual([SWARM.row / 2, SWARM.row / 2]);
  });
});

describe('the dependence chart', () => {
  it('reads the served summary of the feature', () => {
    expect(dependenceOf(GLOBAL, 'tenure')?.kind).toBe('numeric');
    expect(dependenceOf(GLOBAL, 'Contract')?.kind).toBe('categorical');
    expect(dependenceOf({ ...GLOBAL, dependence: [] }, 'tenure')).toBeUndefined();
  });

  it('spans zero, the feature’s own contributions and the ringed customer’s', () => {
    const j = GLOBAL.features.indexOf('Contract');
    const column = GLOBAL.values.map((r) => r[j] ?? NaN);
    const [low, high] = columnDomain(GLOBAL, 'Contract', undefined);
    expect(low).toBeLessThanOrEqual(Math.min(0, ...column));
    expect(high).toBeGreaterThanOrEqual(Math.max(0, ...column));
    // Narrower than the swarm's axis, which spans every feature.
    expect(high - low).toBeLessThan(
      contributionDomain(GLOBAL, undefined)[1] - contributionDomain(GLOBAL, undefined)[0],
    );
    expect(columnDomain(GLOBAL, 'Contract', -0.61)[0]).toBeLessThanOrEqual(-0.61);
    expect(columnDomain(GLOBAL, 'customerID', undefined)).toEqual([0, 0]);
  });

  it('spans every cached value and the ringed customer’s', () => {
    expect(valueDomain(GLOBAL, 'tenure', undefined)).toEqual([1, 72]);
    expect(valueDomain(GLOBAL, 'tenure', 80)).toEqual([1, 80]);
    expect(valueDomain(GLOBAL, 'tenure', 0)).toEqual([0, 72]);
    const constant = { ...GLOBAL, x: GLOBAL.x.map((r) => ({ ...r, tenure: 5 })) };
    expect(valueDomain(constant, 'tenure', undefined)).toEqual([4, 6]);
  });

  it('breaks the mean line at a group too small for a mean and between groups apart', () => {
    const bins: NumericBin[] = [
      { low: 0, high: 1, customers: 5, mean: 0.1 },
      { low: 1, high: 2, customers: 6, mean: 0.2 },
      { low: 2, high: 3, customers: 2, mean: null },
      { low: 3, high: 4, customers: 7, mean: 0.3 },
      { low: 5, high: 6, customers: 5, mean: 0.4 },
      { low: 6, high: 7, customers: 5, mean: 0.5 },
    ];
    expect(meanSegments(bins)).toEqual([
      [
        [0.5, 0.1],
        [1.5, 0.2],
      ],
      [[3.5, 0.3]],
      [
        [5.5, 0.4],
        [6.5, 0.5],
      ],
    ]);
  });

  it('starts and ends no run at a group without a mean', () => {
    const bins: NumericBin[] = [
      { low: 0, high: 1, customers: 2, mean: null },
      { low: 1, high: 2, customers: 3, mean: null },
      { low: 2, high: 3, customers: 5, mean: 0.1 },
      { low: 3, high: 4, customers: 5, mean: 0.2 },
      { low: 4, high: 5, customers: 1, mean: null },
    ];
    expect(meanSegments(bins)).toEqual([
      [
        [2.5, 0.1],
        [3.5, 0.2],
      ],
    ]);
    expect(meanSegments(bins.filter((b) => b.mean === null))).toEqual([]);
  });

  it('places categories evenly, every one of the contract, and jitters a dot deterministically', () => {
    expect(categoryCenter(0, 3, 0, 300)).toBe(50);
    expect(categoryCenter(2, 3, 0, 300)).toBe(250);
    expect(jitter(4, 60)).toBe(jitter(4, 60));
    for (let i = 0; i < 50; i++) expect(Math.abs(jitter(i, 60))).toBeLessThanOrEqual(30);
  });

  it('lists the groups a reader steps through: bins, or every category', () => {
    const tenure = groupsOf(dependenceOf(GLOBAL, 'tenure') as Dependence);
    expect(tenure[0]).toMatchObject({ customers: 5, mean: 0.137696, first: true });
    expect(tenure[1]).toMatchObject({ customers: 1, mean: null, first: false });
    const contract = groupsOf(dependenceOf(GLOBAL, 'Contract') as Dependence);
    expect(
      contract.map((g) => (g.kind === 'level' ? [g.category, g.customers, g.mean] : [])),
    ).toEqual([
      ['Month-to-month', 6, 0.013316],
      ['One year', 4, null],
      ['Two year', 2, null],
    ]);
  });
});

describe('the two rankings', () => {
  it('ranks the model by the served order', () => {
    const ranks = modelRanks(GLOBAL);
    expect(ranks.tenure).toBe(1);
    expect(ranks.Contract).toBe(2);
    expect(Object.keys(ranks)).toHaveLength(18);
  });

  it('draws a line only for a feature ranked both ways', () => {
    const lines = slopeLines(GLOBAL);
    expect(lines.map((l) => l.feature)).toEqual([
      'Contract',
      'OnlineSecurity',
      'tenure',
      'TechSupport',
      'InternetService',
      'PaymentMethod',
      'MonthlyCharges',
    ]);
    expect(lines[0]).toEqual({ feature: 'Contract', association: 1, model: 2 });
    expect(slopeLines({ ...GLOBAL, association_rank: null })).toEqual([]);
    // A cached ranking that leaves an attribute out gives it no model rank, so no line.
    const partial = {
      ...GLOBAL,
      importance: GLOBAL.importance.filter((r) => r.feature !== 'tenure'),
    };
    expect(slopeLines(partial).map((l) => l.feature)).not.toContain('tenure');
    expect(slopeLines(partial)).toHaveLength(6);
  });
});
