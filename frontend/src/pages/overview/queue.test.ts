import { overview } from '../../test/overviewFixtures';
import { binIndex } from './bins';
import { chipBands, defaultBands, queueQuery, select, toggleBand } from './queue';

describe('the retention queue’s bands', () => {
  it('defaults to every band that holds flagged customers', () => {
    // The fixture flags no one in Low (it tops out below the threshold).
    expect(defaultBands(overview())).toEqual(['Moderate', 'High', 'Critical']);
  });

  it('offers a chip for each band that can hold flagged customers, riskiest first', () => {
    expect(chipBands(overview()).map((band) => band.name)).toEqual([
      'Critical',
      'High',
      'Moderate',
    ]);
  });

  it('adds a band, dropping one nobody in it is flagged, and removes a band', () => {
    const book = overview();
    expect(toggleBand(['Low'], 'High', book)).toEqual(['High']);
    expect(toggleBand(['High', 'Critical'], 'High', book)).toEqual(['Critical']);
    expect(toggleBand(['High'], 'High', book)).toEqual([]);
  });

  it('forgets a choice that is the default again, so the address is the default view', () => {
    expect(toggleBand(['High', 'Critical'], 'Moderate', overview())).toBeNull();
  });
});

describe('the selection', () => {
  it('adds and removes customers, and stops at its limit', () => {
    const two = select(new Set(), ['A', 'B'], true, 3);
    expect([...two]).toEqual(['A', 'B']);
    expect([...select(two, ['C', 'D', 'E'], true, 3)]).toEqual(['A', 'B', 'C']);
    expect([...select(two, ['A'], false, 3)]).toEqual(['B']);
  });
});

describe('bins under the pointer', () => {
  it('maps a position across the plot to a bin, clamped at the edges', () => {
    expect(binIndex(40, 40, 560, 20)).toBe(0);
    expect(binIndex(40 + 28.5, 40, 560, 20)).toBe(1);
    expect(binIndex(600, 40, 560, 20)).toBe(19);
    expect(binIndex(10, 40, 560, 20)).toBe(0);
  });

  it('gives no bin for a plot with no width', () => {
    expect(binIndex(50, 0, 0, 20)).toBeNull();
  });
});

describe('the queue’s request', () => {
  it('asks for flagged customers by revenue at risk, a page of eight', () => {
    expect(
      queueQuery(
        { bands: ['High'], part: 'test', q: 'DEMO', sort: 'at_risk', dir: 'desc', page: 2 },
        ['High', 'Critical'],
      ),
    ).toEqual({
      flagged: true,
      exclude_churned: true,
      bands: ['High'],
      partition: 'test',
      q: 'DEMO',
      sort: 'at_risk',
      dir: 'desc',
      page: 2,
      page_size: 8,
    });
  });

  it('uses the data’s default bands until bands are chosen', () => {
    const query = queueQuery(
      { bands: null, part: null, q: '', sort: 'at_risk', dir: 'desc', page: 1 },
      ['Moderate', 'High', 'Critical'],
    );
    expect(query.bands).toEqual(['Moderate', 'High', 'Critical']);
    expect(query.partition).toBeNull();
  });
});

describe('the queue’s request', () => {
  const state = { bands: null, part: null, q: '', sort: 'at_risk', dir: 'desc', page: 1 } as const;

  it('sends the same bands in one order, however they were chosen', () => {
    const defaults = ['Moderate', 'High', 'Critical'] as const;
    expect(queueQuery({ ...state, bands: ['Critical', 'Moderate'] }, defaults).bands).toEqual([
      'Moderate',
      'Critical',
    ]);
    expect(queueQuery(state, defaults).bands).toEqual(defaults);
  });

  it('keeps a band nobody is flagged in, last, when a link names it', () => {
    expect(queueQuery({ ...state, bands: ['High', 'Critical'] }, ['High']).bands).toEqual([
      'High',
      'Critical',
    ]);
    expect(queueQuery({ ...state, bands: ['Critical', 'High'] }, ['High']).bands).toEqual([
      'High',
      'Critical',
    ]);
  });
});
