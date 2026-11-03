import type { CustomerPage, ExplainResponse, ModelInfo } from '../../api/queries';
import { modelInfo } from '../../test/fixtures';
import {
  emptyHint,
  explanationMeta,
  explanationNote,
  exportLabel,
  flagCaption,
  flaggedLabel,
  lede,
  observedTile,
  pageNote,
  selectionStatus,
} from './copy';
import { FILTER_DEFAULTS, type Filters } from './explorer';
import { CAUSAL } from '../../test/wording';

type Summary = CustomerPage['summary'];

function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    customers: 120,
    share_of_book: 0.017,
    flagged: 40,
    flagged_share: 0.333,
    mean_probability: 0.31,
    expected_churners: 37.2,
    observed: { customers: 90, churned: 27, rate: 0.3 },
    monthly_charges: 8000,
    at_risk: 2500,
    histogram: Array.from({ length: 20 }, () => 6),
    ...overrides,
  };
}

const BANDS = modelInfo().risk_bands ?? [];

function hint(filters: Partial<Filters>, extra: Partial<Parameters<typeof emptyHint>[0]> = {}) {
  return emptyHint({
    filters: { ...FILTER_DEFAULTS, ...filters },
    idle: null,
    bands: BANDS,
    threshold: 0.36,
    withheld: null,
    ...extra,
  });
}

describe('the lede', () => {
  it('counts the book and says whose outcomes appear', () => {
    expect(lede(7043, null)).toBe(
      "All 7,043 rows with their served probability. Outcomes appear for train and validation customers only; the test partition's are withheld.",
    );
  });

  it('waits for the count', () => {
    expect(lede(undefined, null)).toMatch(/^All … rows/);
  });

  it('leaves the count out when it could not be loaded', () => {
    expect(lede(null, null)).toBe(
      "All rows with their served probability. Outcomes appear for train and validation customers only; the test partition's are withheld.",
    );
  });

  it('says nothing about outcomes until it is known whether they are withheld', () => {
    expect(lede(7043, undefined)).toBe('All 7,043 rows with their served probability.');
  });

  it('says why every outcome is withheld when provenance is not verified', () => {
    expect(lede(7043, 'The dataset differs from the one the model was trained on.')).toBe(
      'All 7,043 rows with their served probability. Observed outcomes are withheld: The dataset differs from the one the model was trained on.',
    );
  });
});

describe('the observed-churn tile', () => {
  it('gives the rate over the selection’s train and validation customers', () => {
    expect(observedTile(summary(), null)).toEqual({ value: '30.0%', sub: '27 of 90 train + val' });
  });

  it('withholds every outcome when provenance is not verified', () => {
    expect(observedTile(summary(), 'Retrain.')).toEqual({
      value: 'Withheld',
      sub: 'outcomes not shown',
    });
  });

  it('says when the selection holds only test customers', () => {
    expect(observedTile(summary({ observed: null }), null)).toEqual({
      value: '—',
      sub: 'test outcomes withheld',
    });
  });

  it('gives no rate for fewer than 20 customers with outcomes', () => {
    expect(
      observedTile(summary({ observed: { customers: 19, churned: null, rate: null } }), null),
    ).toEqual({ value: '—', sub: 'fewer than 20 with outcomes' });
  });

  it('says when nobody is selected', () => {
    expect(observedTile(summary({ customers: 0, observed: null }), null)).toEqual({
      value: '—',
      sub: 'nobody in the selection',
    });
  });
});

describe('the empty state’s hint', () => {
  it('names no band chosen', () => {
    expect(hint({ bands: [] }, { idle: 'no-band' })).toBe('No band is selected.');
  });

  it('names a tenure range that holds nobody', () => {
    expect(hint({ tenure_min: 9, tenure_max: 3 }, { idle: 'tenure' })).toBe(
      'The minimum tenure is above the maximum.',
    );
  });

  it('explains a flagged view of a band that tops out below the threshold, with the API’s figures', () => {
    expect(hint({ bands: ['Low'], flagged: 'true' })).toBe(
      'Low tops out at 0.25, below the 0.36 threshold, so nobody in it is flagged.',
    );
  });

  it('makes that claim only when the band’s top is below the threshold', () => {
    expect(hint({ bands: ['Low'], flagged: 'true' }, { threshold: 0.2 })).toBe(
      'Loosen a filter or clear the ID search.',
    );
    expect(hint({ bands: ['Moderate'], flagged: 'true' })).toBe(
      'Loosen a filter or clear the ID search.',
    );
  });

  it('says a band ending where the threshold starts holds nobody flagged', () => {
    expect(hint({ bands: ['Low'], flagged: 'true' }, { threshold: 0.25 })).toBe(
      'Low stops short of 0.25, the threshold, so nobody in it is flagged.',
    );
  });

  it('makes no such claim for the top band, which holds its top', () => {
    expect(hint({ bands: ['Critical'], flagged: 'true' }, { threshold: 1 })).toBe(
      'Loosen a filter or clear the ID search.',
    );
  });

  it('says a band starting at the threshold is flagged throughout', () => {
    expect(hint({ bands: ['Critical'], flagged: 'false' }, { threshold: 0.75 })).toBe(
      'Critical starts at 0.75, the threshold, so everyone in it is flagged.',
    );
  });

  it('explains an unflagged view of a band that starts at the threshold', () => {
    expect(hint({ bands: ['Critical'], flagged: 'false' })).toBe(
      'Critical starts at 0.75, above the 0.36 threshold, so everyone in it is flagged.',
    );
    expect(hint({ bands: ['Moderate'], flagged: 'false' })).toBe(
      'Loosen a filter or clear the ID search.',
    );
  });

  it('says why no partition matches while provenance is not verified', () => {
    expect(hint({ part: 'train' }, { withheld: 'Retrain.' })).toBe(
      "No customer's partition could be verified, so none is listed under Train.",
    );
  });

  it('asks to loosen a filter otherwise', () => {
    expect(hint({ contract: 'Two year' })).toBe('Loosen a filter or clear the ID search.');
  });

  it('makes no band claim before the bands are known', () => {
    expect(hint({ bands: ['Low'], flagged: 'true' }, { bands: undefined })).toBe(
      'Loosen a filter or clear the ID search.',
    );
  });
});

describe('the table’s note', () => {
  const page = (overrides: Partial<CustomerPage>): CustomerPage => ({
    total: 37,
    page: 1,
    page_size: 15,
    pages: 3,
    rows: [],
    summary: summary(),
    withheld_reason: null,
    ...overrides,
  });

  it('says which rows are shown, and how to open one while none is', () => {
    expect(pageNote(page({ page: 3 }), 7, false)).toBe(
      'Showing 31–37 of 37 · select a row to open its profile and explanation',
    );
    expect(pageNote(page({}), 15, true)).toBe('Showing 1–15 of 37');
  });

  it('says there are no rows', () => {
    expect(pageNote(page({ total: 0, pages: 1 }), 0, false)).toBe('No rows');
  });
});

describe('short labels', () => {
  it('words the selection for a screen reader, one or many', () => {
    expect(selectionStatus(summary())).toBe('120 customers match, 40 flagged.');
    expect(selectionStatus(summary({ customers: 1, flagged: 0 }))).toBe(
      '1 customer matches, 0 flagged.',
    );
  });

  it('counts the export', () => {
    expect(exportLabel(7043)).toBe('Export 7,043 rows');
    expect(exportLabel(1)).toBe('Export 1 row');
    expect(exportLabel(undefined)).toBe('Export rows');
  });

  it('names the decision by the served threshold', () => {
    expect(flagCaption(0.36)).toBe('Decision at 0.36');
    expect(flagCaption(undefined)).toBe('Flag');
    expect(flaggedLabel(0.36)).toBe('Flagged at 0.36');
    expect(flaggedLabel(undefined)).toBe('Flagged');
  });
});

describe('an explanation', () => {
  const response: ExplainResponse = {
    model_sha256: 'a'.repeat(64),
    background_customers: 100,
    permutations: 10,
    seed: 42,
    caveat: 'These are model attributions, not causal effects.',
    explanations: [
      {
        customer_id: 'DEMO-0001',
        base_value: 0.265,
        churn_probability: 0.99988,
        contributions: [],
      },
    ],
  };

  it('goes from the model’s average to this customer’s probability', () => {
    const [first] = response.explanations;
    if (!first) throw new Error('fixture');
    expect(explanationMeta(first)).toBe('base 26.5% → 99.99%');
  });

  it('says how the server computed it, with its own figures', () => {
    expect(explanationNote(response)).toBe(
      "Percentage points against the model's average over 100 training customers; orange raises, blue lowers. Computed on the server with 10 feature orderings. Model attributions, not causal effects.",
    );
  });
});

describe('every sentence', () => {
  const info: ModelInfo = modelInfo();

  it('makes no causal claim', () => {
    const sentences = [
      lede(7043, null),
      lede(7043, 'Retrain.'),
      lede(null, undefined),
      ...['no-band', 'tenure'].map((idle) =>
        hint({ bands: [] }, { idle: idle as 'no-band' | 'tenure' }),
      ),
      hint({ bands: ['Low'], flagged: 'true' }),
      hint({ bands: ['Critical'], flagged: 'false' }),
      hint({ bands: ['Low'], flagged: 'true' }, { threshold: 0.25 }),
      hint({ bands: ['Critical'], flagged: 'false' }, { threshold: 0.75 }),
      hint({ part: 'test' }, { withheld: 'Retrain.' }),
      hint({}),
      explanationNote({
        model_sha256: 'a'.repeat(64),
        background_customers: 100,
        permutations: 10,
        seed: 42,
        caveat: '',
        explanations: [],
      }),
      flagCaption(info.threshold),
    ];
    for (const sentence of sentences) expect(sentence).not.toMatch(CAUSAL);
  });
});
