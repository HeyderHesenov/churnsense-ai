import { contractAlone, contractByTenure, hidden, reported } from '../../test/patternsApi';
import {
  associationRows,
  explore,
  grid,
  pinnedCell,
  scaleOf,
  shadeOf,
  SUGGESTED,
  viewOf,
  type PatternsState,
} from './patterns';

const STATE: PatternsState = { rows: 'Contract', cols: 'tenure', show: 'rate', pr: '', pc: '' };

describe('the view the address asks for', () => {
  it('crosses two dimensions', () => {
    expect(viewOf(STATE)).toEqual({ rows: 'Contract', cols: 'tenure' });
  });

  it('reads "none" as one dimension', () => {
    expect(viewOf({ ...STATE, cols: 'none' })).toEqual({ rows: 'Contract', cols: null });
  });

  it('never crosses a dimension with itself, which the API refuses', () => {
    expect(viewOf({ ...STATE, cols: 'Contract' })).toEqual({ rows: 'Contract', cols: null });
  });

  it('suggests only views of dimensions the API knows, each crossing two', () => {
    expect(SUGGESTED.map((s) => s.label)).toEqual([
      'Contract × tenure',
      'Internet × payment',
      'Price × tenure',
      'Tech support × contract',
    ]);
    for (const s of SUGGESTED) expect(s.rows).not.toBe(s.cols);
  });
});

describe('the grid of a response', () => {
  it("takes its rows and columns from the response's own levels", () => {
    const g = grid(contractByTenure());
    expect(g.rows).toEqual(['Month-to-month', 'One year', 'Two year']);
    expect(g.columns).toEqual(['0-3', '4-72']);
    expect(g.at('One year', '4-72')).toMatchObject({ n: 988, churned: 148 });
    expect(g.at('One year', '13-24')).toBeUndefined();
  });

  it('reads a one-dimension response off its levels', () => {
    const g = grid(contractAlone());
    expect(g.columns).toBeNull();
    expect(g.at('Two year', null)).toMatchObject({ n: 1000, churned: 50 });
  });
});

describe('the pinned cell', () => {
  const data = contractByTenure();

  it('is the cell the address names', () => {
    expect(pinnedCell(data, { ...STATE, pr: 'One year', pc: '4-72' })).toMatchObject({ n: 988 });
  });

  it('is none for levels the shown response does not hold', () => {
    expect(pinnedCell(data, { ...STATE, pr: 'One year', pc: '13-24' })).toBeNull();
    expect(pinnedCell(data, { ...STATE, pr: 'Gold', pc: '4-72' })).toBeNull();
    expect(pinnedCell(data, STATE)).toBeNull();
  });

  it('needs no column in a one-dimension view, and refuses one', () => {
    const alone = contractAlone();
    expect(pinnedCell(alone, { ...STATE, pr: 'Two year' })).toMatchObject({ n: 1000 });
    expect(pinnedCell(alone, { ...STATE, pr: 'Two year', pc: '0-3' })).toBeNull();
  });
});

describe('the shade of a cell', () => {
  const data = contractByTenure();

  it('runs rates from zero to the highest reported rate, rounded up to a tenth', () => {
    const scale = scaleOf(data, 'rate');
    // Month-to-month 4-72 is the highest reported cell: 280 / 1040 = 26.9%.
    expect(scale).toEqual({ measure: 'rate', top: 0.3 });
    expect(shadeOf(reported('a', 'b', 100, 15), scale)).toBeCloseTo(0.5);
  });

  it('rounds the rate scale up to a tenth, never below a tenth', () => {
    const top = (...rates: number[]) =>
      scaleOf(
        contractAlone({ row_totals: rates.map((r, i) => reported(`L${i}`, null, 1000, r * 1000)) }),
        'rate',
      ).top;
    expect(top(0.04)).toBe(0.1);
    // Nobody churned anywhere: still a scale to divide by.
    expect(top(0)).toBe(0.1);
    expect(top(0.31, 0.2)).toBe(0.4);
    expect(top(0.97)).toBe(1);
  });

  it('rounds the lift span up to a half', () => {
    const span = contractAlone({
      row_totals: [
        { ...reported('A', null, 1000, 550), lift: 2.2 },
        reported('B', null, 1000, 250),
      ],
    });
    expect(scaleOf(span, 'lift').top).toBe(2.5);
    const even = contractAlone({ row_totals: [{ ...reported('A', null, 1000, 250), lift: 1 }] });
    expect(scaleOf(even, 'lift').top).toBe(1.5);
    expect(shadeOf({ ...reported('A', null, 1000, 250), lift: 1 }, scaleOf(even, 'lift'))).toBe(0);
  });

  it('shades a group where nobody churned at the bottom of the lift scale, not as average', () => {
    const scale = scaleOf(contractByTenure(), 'lift');
    expect(shadeOf(reported('Two year', null, 40, 0), scale)).toBe(-1);
  });

  it('sizes the customers scale by every group, hidden ones included', () => {
    const big = contractAlone({
      row_totals: [hidden('A', null, 5000, 'withheld'), reported('B', null, 1000, 250)],
    });
    expect(scaleOf(big, 'customers').top).toBe(5000);
  });

  it('gives a hidden cell no shade on any measure but its size', () => {
    const cell = hidden('a', 'b', 12);
    expect(shadeOf(cell, scaleOf(data, 'rate'))).toBeNull();
    expect(shadeOf(cell, scaleOf(data, 'lift'))).toBeNull();
    expect(shadeOf(cell, scaleOf(data, 'customers'))).toBeCloseTo(12 / 1040);
  });

  it('centres lift on 1× and spreads it evenly on a log scale', () => {
    const scale = scaleOf(data, 'lift');
    // Lifts 1.08, 0.60 and 0.20: the widest is 1 / 0.2 = 5×.
    expect(scale).toEqual({ measure: 'lift', top: 5 });
    const shade = (lift: number) => shadeOf({ ...reported('a', 'b', 100, 25), lift }, scale);
    expect(shade(1)).toBeCloseTo(0);
    expect(shade(5)).toBeCloseTo(1);
    expect(shade(0.2)).toBeCloseTo(-1);
    expect(shade(Math.sqrt(5))).toBeCloseTo(0.5);
  });

  it('keeps a small lift spread from looking wide', () => {
    const flat = contractAlone({
      row_totals: [reported('A', null, 500, 130), reported('B', null, 500, 120)],
    });
    expect(scaleOf(flat, 'lift').top).toBe(1.5);
  });

  it('runs sizes from zero to the largest group', () => {
    expect(scaleOf(data, 'customers')).toEqual({ measure: 'customers', top: 1040 });
  });
});

describe('the association list', () => {
  it('names a dimension for the attributes the page can break churn down by', () => {
    expect(associationRows(contractByTenure()).map((a) => [a.feature, a.dimension])).toEqual([
      ['Contract', 'Contract'],
      ['tenure', 'tenure'],
      ['OnlineBackup', null],
      ['MonthlyCharges', 'monthly'],
    ]);
  });

  it('never names gender, a protected attribute the model does not use', () => {
    expect(associationRows(contractByTenure()).some((a) => a.feature === 'gender')).toBe(false);
  });

  it('is empty when the ranking is not available', () => {
    expect(associationRows(contractByTenure({ association: null }))).toEqual([]);
  });
});

describe('opening a group in the customer explorer', () => {
  const view = { rows: 'Contract', cols: 'tenure' };

  it('links a reported cell with the filters that keep exactly its training customers', () => {
    expect(explore(view, reported('Two year', '4-72', 1000, 50))).toEqual({
      kind: 'link',
      href: '/explorer?contract=Two+year&tenure_min=4&tenure_max=72&part=train',
    });
  });

  it('links internet service and payment method by their own filters', () => {
    const cell = reported('No', 'Credit card (automatic)', 200, 10);
    expect(explore({ rows: 'InternetService', cols: 'PaymentMethod' }, cell)).toEqual({
      kind: 'link',
      href: '/explorer?internet=No&payment=Credit+card+%28automatic%29&part=train',
    });
  });

  it('links a level of one dimension alone', () => {
    expect(explore({ rows: 'tenure', cols: null }, reported('0-3', null, 500, 200))).toEqual({
      kind: 'link',
      href: '/explorer?tenure_min=0&tenure_max=3&part=train',
    });
  });

  it('never links a hidden cell', () => {
    expect(explore(view, hidden('One year', '0-3', 12))).toEqual({ kind: 'hidden' });
    expect(explore(view, hidden('Month-to-month', '0-3', 960))).toEqual({ kind: 'hidden' });
  });

  it('names the dimension the explorer cannot filter', () => {
    const cell = reported('<$35', 'No', 1400, 140);
    expect(explore({ rows: 'monthly', cols: 'StreamingTV' }, cell)).toEqual({
      kind: 'unfiltered',
      dimension: 'monthly',
    });
    expect(
      explore({ rows: 'Contract', cols: 'StreamingTV' }, reported('Two year', 'No', 40, 2)),
    ).toEqual({
      kind: 'unfiltered',
      dimension: 'StreamingTV',
    });
  });

  it('refuses a level the explorer would not read back, rather than drop the filter', () => {
    expect(explore(view, reported('Gold', '4-72', 100, 5))).toEqual({
      kind: 'unfiltered',
      dimension: 'Contract',
    });
    expect(explore(view, reported('Two year', '49+', 100, 5))).toEqual({
      kind: 'unfiltered',
      dimension: 'tenure',
    });
  });
});
