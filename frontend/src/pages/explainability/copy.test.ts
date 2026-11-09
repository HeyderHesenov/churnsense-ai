import { globalExplanation } from '../../test/explainApi';
import { CAUSAL } from '../../test/wording';
import {
  additivity,
  ASSOCIATION_METHOD,
  CARDS,
  categoryTick,
  depLabel,
  depNote,
  EXPLAIN_FAILED,
  EXPLAIN_UNAVAILABLE,
  explainedSub,
  FAILED,
  groupName,
  groupText,
  incomparable,
  LEDE,
  MASKING_CAVEAT,
  METHOD_NOTES,
  MISMATCH,
  NOT_CACHED,
  notFound,
  PROVENANCE_UNVERIFIED,
  question,
  ringNote,
  ROUNDING_NOTE,
  rowDescription,
  SCALE,
  TITLE,
  totalChargesNote,
} from './copy';

const GLOBAL = globalExplanation();

describe('the method', () => {
  it('states the served method from the served figures', () => {
    expect(explainedSub(GLOBAL)).toBe('validation partition, seed 42');
    expect(explainedSub({ ...GLOBAL, seed: null })).toBe('validation partition');
    expect(additivity(1.942890293094024e-15)).toBe('1.9e-15');
    expect(additivity(0)).toBe('0');
  });

  it('describes a swarm row by its served figure and rank', () => {
    expect(rowDescription(0.104331, 1)).toBe(
      'Mean absolute contribution 10.4 percentage points, rank 1',
    );
  });

  it('asks the artboard’s question of a feature, and a plain one of the rest', () => {
    expect(question('tenure')).toBe('Does a longer relationship lower the estimate, and how fast?');
    expect(question('Partner')).toBe('How does each value of this attribute move the estimate?');
  });
});

describe('the dependence chart', () => {
  it('says how its groups are summarised, with the served floor', () => {
    expect(depNote('numeric', 5)).toBe(
      'Line: mean contribution in each equal-width value group; where a group is empty or has fewer than 5 customers there is no mean, and the line breaks.',
    );
    expect(depNote('categorical', 5)).toBe(
      'Bars: mean contribution per category, given from 5 customers; dots are spread sideways only for legibility.',
    );
    expect(depLabel('Tenure', 300)).toBe(
      'Contribution of Tenure against its value for 300 explained customers',
    );
  });

  it('reads a group with its range, its customers and its mean, or why there is none', () => {
    expect(
      groupText('tenure', {
        kind: 'bin',
        low: 1,
        high: 6.916667,
        first: true,
        customers: 5,
        mean: 0.137696,
      }),
    ).toBe('Tenure 1 to 6.9 months: 5 customers, mean contribution +13.8 pp');
    expect(
      groupText('tenure', {
        kind: 'bin',
        low: 6.916667,
        high: 12.833333,
        first: false,
        customers: 1,
        mean: null,
      }),
    ).toBe('Tenure over 6.9, up to 12.8 months: 1 customer, too few for a mean');
    expect(
      groupText('MonthlyCharges', {
        kind: 'bin',
        low: 20.05,
        high: 28.279,
        first: true,
        customers: 6,
        mean: -0.00004,
      }),
    ).toBe('Monthly charges $20.05 to $28.28: 6 customers, mean contribution 0.0 pp');
    expect(
      groupText('Contract', { kind: 'level', category: 'One year', customers: 4, mean: null }),
    ).toBe('Contract One year: 4 customers, too few for a mean');
    expect(
      groupText('PaymentMethod', {
        kind: 'level',
        category: 'Credit card (automatic)',
        customers: 0,
        mean: null,
      }),
    ).toBe('Payment method Credit card (auto): none in the sample');
    expect(
      groupText('SeniorCitizen', { kind: 'level', category: '1', customers: 7, mean: -0.0123 }),
    ).toBe('Senior citizen Yes: 7 customers, mean contribution −1.2 pp');
  });

  it('names a group without its feature for the table, and a category shortly under the chart', () => {
    expect(
      groupName('TotalCharges', {
        kind: 'bin',
        low: 731.22,
        high: 1442.39,
        first: false,
        customers: 1,
        mean: null,
      }),
    ).toBe('over $731.22, up to $1,442.39');
    expect(
      groupName('OnlineSecurity', {
        kind: 'level',
        category: 'No internet service',
        customers: 3,
        mean: null,
      }),
    ).toBe('No internet');
    expect(categoryTick('Contract', 'Month-to-month')).toBe('Monthly');
    expect(categoryTick('InternetService', 'Fiber optic')).toBe('Fiber');
    expect(categoryTick('InternetService', 'No')).toBe('No internet');
    expect(categoryTick('PaymentMethod', 'Electronic check')).toBe('E-check');
    expect(categoryTick('PaymentMethod', 'Bank transfer (automatic)')).toBe('Bank transfer');
    expect(categoryTick('MultipleLines', 'No phone service')).toBe('No phone');
    expect(categoryTick('SeniorCitizen', '1')).toBe('Yes');
  });

  it('hedges the total-charges note on the served correlation, and says nothing without one', () => {
    expect(totalChargesNote(0.8312)).toBe(
      'Total charges track tenure × monthly charges in training (r = 0.831), so the explainer’s split of credit between the three can be arbitrary; read their contributions together.',
    );
    expect(totalChargesNote(null)).toBeNull();
  });
});

describe('one customer', () => {
  it('never calls the ring one of the dots', () => {
    expect(ringNote('DEMO-0005', GLOBAL)).toBe(
      'Ring: DEMO-0005, explained on their own against the same 100 training customers. The dots are a cached sample of 12 validation customers; the ring is not one of them, even for a customer in the sample.',
    );
  });

  it('explains why a customer is not ringed', () => {
    expect(incomparable('artifact')).toBe(
      'Not drawn on the figures: this explanation is of another artifact than the cached attributions.',
    );
    expect(incomparable('baseline')).toBe(
      'Not drawn on the figures: this explanation was measured from another base value than the cached attributions.',
    );
    expect(incomparable('background')).toBe(
      'Not drawn on the figures: this explanation used a background of another size than the cached attributions.',
    );
    expect(notFound('DEMO-9999')).toBe('No customer with ID DEMO-9999 is in the book.');
  });
});

describe('the words', () => {
  it('make no causal claim, never print a sum, and never claim to compute in the browser', () => {
    const sentences = [
      TITLE,
      LEDE,
      SCALE,
      ROUNDING_NOTE,
      ASSOCIATION_METHOD,
      MASKING_CAVEAT,
      NOT_CACHED.title,
      NOT_CACHED.body,
      MISMATCH,
      FAILED,
      EXPLAIN_FAILED,
      EXPLAIN_UNAVAILABLE,
      PROVENANCE_UNVERIFIED,
      depNote('numeric', 5),
      depNote('categorical', 5),
      totalChargesNote(0.9996) ?? '',
      ringNote('DEMO-0005', GLOBAL),
      incomparable('artifact'),
      incomparable('baseline'),
      incomparable('background'),
      question('tenure'),
      question('MonthlyCharges'),
      question('TotalCharges'),
      question('Contract'),
      question('InternetService'),
    ];
    for (const text of sentences) {
      expect(text).not.toMatch(CAUSAL);
      expect(text).not.toMatch(/in your browser|cram[eé]r.s v \d|front-loaded|discount working/i);
    }
    for (const text of [
      ...Object.values(CARDS).flatMap((c) => Object.values(c)),
      ...Object.values(METHOD_NOTES),
    ]) {
      expect(text).not.toMatch(CAUSAL);
    }
    expect(NOT_CACHED.body).toContain('make explain');
  });

  it('asks only what the charts show: no effect at a fixed value, no summed figure', () => {
    for (const feature of [
      'tenure',
      'MonthlyCharges',
      'TotalCharges',
      'Contract',
      'InternetService',
      'Partner',
    ])
      expect(question(feature)).not.toMatch(/fixed|once .* is known|holding|other things equal/i);
    expect(ROUNDING_NOTE).not.toMatch(/exactly/i);
    expect(ROUNDING_NOTE).toContain('1e-9');
  });
});
