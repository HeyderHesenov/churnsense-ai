/**
 * The sentences that name whose customers a figure is of, when the service
 * lists generated ones. Each is chosen by a value the API sent (a partition,
 * a background, a data source), never by the page.
 */
import type { CustomerPage, ExplainResponse } from '../api/queries';
import { globalExplanation } from '../test/explainApi';
import { customerDetail } from '../test/fixtures';
import { DATASET, SYNTHETIC } from '../test/syntheticSource';
import { BACKGROUND_WORD, cardMeta, waterfallNote } from './customer/copy';
import {
  backgroundCustomers,
  backgroundNote,
  explainedCustomers,
  explainedSub,
  ringNote,
} from './explainability/copy';
import { lede, observedTile } from './explorer/copy';
import { footerSource } from './overview/copy';

const SYNTHETIC_SAMPLE = globalExplanation({ partition: 'synthetic' });
const VALIDATION_SAMPLE = globalExplanation({ partition: 'validation' });

function response(background: ExplainResponse['background']): ExplainResponse {
  return {
    model_sha256: null,
    background_customers: 100,
    background,
    permutations: 10,
    seed: 42,
    caveat: '',
    explanations: [],
  };
}

describe('a single customer', () => {
  it('reads a generated customer as synthetic with no outcome, whatever outcome is sent', () => {
    for (const outcome of ['withheld', 'churned', 'stayed'] as const) {
      expect(cardMeta(customerDetail('DEMO-000001', { partition: 'synthetic', outcome }))).toBe(
        'synthetic · no outcome',
      );
    }
  });

  it('names the background the API says the explanation was measured against', () => {
    expect(waterfallNote(response('synthetic'), 0.36)).toContain(
      'the same 100-customer synthetic demo-customer background on every page',
    );
    expect(waterfallNote(response('synthetic'), 0.36)).not.toContain('training');
    expect(waterfallNote(response('training'), 0.36)).toContain(
      'the same 100-customer training background on every page',
    );
  });

  it('has a word for every background the API can send', () => {
    expect(Object.keys(BACKGROUND_WORD).sort()).toEqual(['synthetic', 'training']);
  });
});

describe('explainability', () => {
  it('names a synthetic sample and its background as synthetic demo customers', () => {
    expect(explainedCustomers(SYNTHETIC_SAMPLE)).toBe('synthetic demo customers');
    expect(backgroundCustomers(SYNTHETIC_SAMPLE)).toBe('synthetic demo customers');
    expect(backgroundNote(SYNTHETIC_SAMPLE)).toBe(
      'synthetic demo customers, the same on every page',
    );
    expect(explainedSub(SYNTHETIC_SAMPLE)).toMatch(/^synthetic demo customers, seed \d+$/);
    expect(explainedSub({ ...SYNTHETIC_SAMPLE, seed: null })).toBe('synthetic demo customers');
  });

  it('keeps the dataset’s sample named by its partition, against training customers', () => {
    expect(explainedCustomers(VALIDATION_SAMPLE)).toBe('validation customers');
    expect(backgroundNote(VALIDATION_SAMPLE)).toBe('training customers, the same on every page');
    expect(explainedSub(VALIDATION_SAMPLE)).toMatch(/^validation partition, seed \d+$/);
  });

  it('never calls a ring’s background training customers on a synthetic sample', () => {
    const note = ringNote('DEMO-000007', SYNTHETIC_SAMPLE);
    expect(note).toContain('against the same 100 synthetic demo customers');
    expect(note).toContain('synthetic demo customers; the ring is not one of them');
    expect(note).not.toMatch(/training|validation/);
    expect(ringNote('DEMO-0007', VALIDATION_SAMPLE)).toContain('100 training customers');
  });
});

describe('the explorer', () => {
  const summary: CustomerPage['summary'] = {
    customers: 300,
    share_of_book: 0.15,
    flagged: 120,
    flagged_share: 0.4,
    mean_probability: 0.3,
    expected_churners: 90,
    // Even were the API to send one, no rate is shown for generated customers.
    observed: { customers: 300, churned: 60, rate: 0.2 },
    monthly_charges: 20000,
    active_monthly_charges: 20000,
    at_risk: 6000,
    histogram: Array.from({ length: 20 }, () => 15),
  };

  it('shows no observed churn for generated customers', () => {
    expect(observedTile(summary, null, true)).toEqual({
      value: '—',
      sub: 'synthetic customers have no outcome',
    });
    expect(observedTile(summary, null, false).value).toBe('20.0%');
  });

  it('says the book is synthetic demo customers, and nothing of withheld outcomes', () => {
    expect(lede(2000, null, true)).toBe(
      'All 2,000 synthetic demo customers with their served probability.',
    );
    expect(lede(2000, null, true)).not.toMatch(/withheld|partition/);
    expect(lede(7000, null, false)).toMatch(/the test partition's are withheld/);
  });
});

describe('the overview’s footer', () => {
  it('names the book as the service names it', () => {
    expect(footerSource(SYNTHETIC, 2000)).toBe(`${SYNTHETIC.title} · 2,000 generated customers`);
    expect(footerSource(DATASET, 7043)).toBe('IBM Telco Customer Churn · 7,043 rows');
    expect(footerSource(null, 7043)).toBe('IBM Telco Customer Churn · 7,043 rows');
    expect(footerSource(undefined, 7043)).toBe('IBM Telco Customer Churn · 7,043 rows');
  });
});
