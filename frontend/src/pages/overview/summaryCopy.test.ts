import { overview } from '../../test/overviewFixtures';
import {
  campaignBrief,
  donutDescription,
  flaggedBrief,
  observedShort,
  tenureBrief,
  watchNote,
  wholeMonths,
} from './copy';
import { arcs, scaleTop, starts } from './scale';
import { chipAnchor } from './Summary';
import { trackPercent } from './Meter';

describe('the briefing’s sentences', () => {
  it('states the flagged share and count as the API gives them', () => {
    expect(flaggedBrief(overview())).toEqual({
      title: '32.9% of the book is flagged',
      fact: '2,300 customers at or above 0.36; 1,900 of them in the retention queue.',
    });
    const book = overview();
    expect(flaggedBrief({ ...book, flagged: { ...book.flagged, share: null } }).title).toBe(
      '2,300 customers are flagged',
    );
  });

  it('says nothing of tenure without both medians', () => {
    const lifecycle = overview().lifecycle;
    if (!lifecycle) throw new Error('the fixture has a lifecycle');
    expect(tenureBrief(null)).toBeNull();
    expect(tenureBrief({ ...lifecycle, median_tenure_stayed: null })).toBeNull();
    expect(tenureBrief(lifecycle)?.fact).toBe(
      'Median tenure 10 months among customers who churned, 37 among those who stayed (training partition).',
    );
  });

  it('says a campaign pays only when its net benefit is above zero', () => {
    const campaign = overview().campaign;
    if (!campaign) throw new Error('the fixture has a campaign');
    expect(campaignBrief(campaign).title).toBe('A campaign pays, on paper');
    expect(campaignBrief({ ...campaign, net_benefit: 0 }).title).toBe(
      'A campaign does not pay at these assumptions',
    );
    expect(campaignBrief({ ...campaign, net_benefit: -5 }).fact).toBe(
      '−$5 simulated net benefit at 0.36 on validation, 450 offers.',
    );
  });

  it('names how the watched segments were chosen, and that they are no cause', () => {
    expect(watchNote(20)).toContain('20 or more customers each');
    expect(watchNote(20)).toContain('more likely overstated');
    expect(watchNote(20)).toContain('An association, not a cause.');
  });
});

describe('the ring’s words', () => {
  it('reads each band and everyone flagged', () => {
    expect(donutDescription(overview())).toMatch(
      /^Customers by risk band: Low 4,000 \(57\.1%\), Moderate .+\. 2,300 flagged at 0\.36, 32\.9% of the book\.$/,
    );
  });

  it('says withheld, a rate, or a dash', () => {
    expect(observedShort({ observed: { customers: 100, churned: 8, rate: 0.08 } }, false)).toBe(
      '8.0% churned',
    );
    expect(observedShort({ observed: { customers: 100, churned: 8, rate: 0.08 } }, true)).toBe(
      'withheld',
    );
    expect(observedShort({ observed: { customers: 10, churned: null, rate: null } }, false)).toBe(
      '—',
    );
    expect(observedShort({ observed: null }, false)).toBe('—');
  });

  it('rounds a median to whole months', () => {
    expect(wholeMonths(9.5)).toBe('10');
    expect(wholeMonths(null)).toBe('—');
  });
});

describe('drawing scales', () => {
  it('lays arcs end to end, a gap apart, none for a missing or empty share', () => {
    expect(arcs([0.5, null, 0.25, 0], 1)).toEqual([
      { dash: '49 51', offset: -0 },
      null,
      { dash: '24 76', offset: -50 },
      null,
    ]);
  });

  it('starts each share where the ones before it end', () => {
    expect(starts([0.2, 0.3, 0.5])).toEqual([0, 0.2, 0.5]);
  });

  it('tops a chart at the largest rate rounded up to the next tenth', () => {
    expect(scaleTop([0.12, null, 0.31])).toBe(0.4);
    expect(scaleTop([])).toBe(0.1);
  });

  it('keeps a bar inside its track', () => {
    expect(trackPercent(0.36)).toBe('36%');
    expect(trackPercent(1.4)).toBe('100%');
    expect(trackPercent(-1)).toBe('0%');
  });

  it('holds the threshold chip inside the scale near either end', () => {
    expect(chipAnchor(0.05)).toBe('start');
    expect(chipAnchor(0.36)).toBe('middle');
    expect(chipAnchor(0.95)).toBe('end');
  });
});
