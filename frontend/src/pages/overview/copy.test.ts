import { overview } from '../../test/overviewFixtures';
import {
  assumptionsNote,
  riskiestBands,
  unflaggedNote,
  bandRange,
  campaignCaption,
  campaignLabel,
  importanceNote,
  scaleLabel,
  segmentNote,
  spread,
} from './copy';

const book = overview();
const tab = (key: string) => {
  const found = book.segments.find((s) => s.key === key);
  if (!found) throw new Error(key);
  return found;
};

describe('band ranges', () => {
  it('writes a band card’s range, the top clamped to 1.00', () => {
    expect(bandRange({ min: 0, max: 0.25 })).toBe('0–0.25');
    expect(bandRange({ min: 0.25, max: 0.5 })).toBe('0.25–0.50');
    expect(bandRange({ min: 0.75, max: 1.01 })).toBe('0.75–1.00');
  });

  it('labels the scale with the first range in full and the rest by their top', () => {
    expect(scaleLabel({ min: 0, max: 0.25 }, 0)).toBe('0–25%');
    expect(scaleLabel({ min: 0.25, max: 0.5 }, 1)).toBe('–50%');
    expect(scaleLabel({ min: 0.75, max: 1.01 }, 3)).toBe('–100%');
  });
});

describe('segment notes', () => {
  it('states the contract spread the API measured', () => {
    expect(segmentNote(tab('Contract'))).toBe(
      'A 17× spread in observed churn across contract types. An association in one snapshot; it does not say why.',
    );
    expect(segmentNote({ ...tab('Contract'), spread: null })).toBe(
      'Observed churn differs across contract types. An association in one snapshot; it does not say why.',
    );
  });

  it('states what the data shows of payment and internet service, and no motive', () => {
    expect(segmentNote(tab('PaymentMethod'))).toBe(
      'Observed churn differs across payment methods. An association in one snapshot; it does not say why.',
    );
    expect(segmentNote(tab('InternetService'))).toBe(
      'Observed churn differs across internet service types. An association in one snapshot; it does not say why.',
    );
  });

  it('assumes nothing about why customers stay or what a product is', () => {
    const notes = ['Contract', 'PaymentMethod', 'InternetService'].map((key) =>
      segmentNote(tab(key)),
    );
    const notes2 = segmentNote({ ...tab('Contract'), spread: null });
    expect([...notes, notes2].join(' ')).not.toMatch(
      /self-selection|expect to stay|sign longer|commitment|friction|proxies|lever|premium/i,
    );
  });

  it('calls price not monotonic only when the peak is inside the range', () => {
    expect(segmentNote(tab('monthly'))).toBe('Not monotonic: churn peaks at $75–95.');
    expect(segmentNote({ ...tab('monthly'), peak: '$95+' })).toBe('Churn is highest at $95+.');
  });

  it('writes a spread under ten with one decimal', () => {
    expect(spread(3.24)).toBe('3.2×');
    expect(spread(17.3)).toBe('17×');
  });
});

describe('importance note', () => {
  it('compares the strongest association’s rank in the model', () => {
    // Contract is first on its own and fourth by mean |SHAP| in the fixture.
    expect(importanceNote(book.importance ?? [], book.association ?? [])).toBe(
      'Contract ranks first on its own but fourth in the model: on its own, a feature also carries what it shares with others.',
    );
  });

  it('says so when both rankings agree', () => {
    const importance = [
      { feature: 'Contract', label: 'Contract type', mean_abs_shap: 0.2, share: 0.4 },
    ];
    expect(importanceNote(importance, book.association ?? [])).toBe(
      'Contract ranks first both on its own and in the model.',
    );
  });

  it('says nothing it cannot compare', () => {
    expect(importanceNote([], book.association ?? [])).toBeNull();
    expect(importanceNote(book.importance ?? [], [])).toBeNull();
  });
});

describe('campaign copy', () => {
  const campaign = book.campaign;
  if (!campaign) throw new Error('fixture has a campaign');

  it('names the shipped point and the peak, with the gap the API serves', () => {
    expect(campaignCaption(campaign)).toBe('0.36 shipped · 0.30 peak, $32 higher');
    // The served figure itself: nothing here subtracts the two it sits beside.
    expect(campaignCaption({ ...campaign, peak_gain: 7 })).toBe(
      '0.36 shipped · 0.30 peak, $7 higher',
    );
    expect(campaignCaption({ ...campaign, peak_threshold: 0.36, peak_net_benefit: 26_400 })).toBe(
      '0.36 shipped, at the peak',
    );
  });

  it('says the shipped point is within an offer’s cost of the peak only when the rule chose it', () => {
    expect(campaignLabel(campaign)).toBe(
      "Simulated net benefit by threshold on validation. Peak at 0.30, shipped threshold 0.36, within one offer's cost of the peak.",
    );
    expect(campaignLabel({ ...campaign, recommended_threshold: 0.3 })).toBe(
      'Simulated net benefit by threshold on validation. Peak at 0.30, shipped threshold 0.36.',
    );
  });

  it('states every assumption the money rests on', () => {
    expect(assumptionsNote(campaign.assumptions)).toBe(
      'Assumed: offer $50, success 30%, 12 months, margin 65%. Not measured: the data holds no campaign outcomes.',
    );
  });
});

describe('claims about the book', () => {
  it('explains an unflagged band by its top only when the top is below the threshold', () => {
    const low = { name: 'Low', min: 0, max: 0.25, customers: 4000 };
    expect(unflaggedNote(low, 0.36)).toBe(
      'Low tops out at a probability of 0.25, below the 0.36 threshold, so these 4,000 customers never enter the queue.',
    );
    expect(unflaggedNote({ ...low, name: 'Moderate', min: 0.25, max: 0.5 }, 0.36)).toBe(
      'None of the 4,000 customers in Moderate reaches the 0.36 threshold.',
    );
  });

  it('names the two riskiest bands, whatever the threshold', () => {
    expect(riskiestBands(book)).toEqual(['High', 'Critical']);
    expect(riskiestBands({ ...book, threshold: 0.6 })).toEqual(['High', 'Critical']);
  });
});
