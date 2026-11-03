import { customerDetail } from '../../test/fixtures';
import {
  amountMessage,
  cardMeta,
  EXPLAIN_BLOCKED,
  EXPLAIN_FAILED,
  EXPLAIN_UNAVAILABLE,
  flagText,
  gapNote,
  inputsTag,
  LEDE,
  LIVE_NOTE,
  notFound,
  otherInputs,
  panelCaption,
  PROFILE_NOTE,
  sentence,
  SENTENCES_NOTE,
  showAll,
  statusLine,
  waterfallNote,
} from './copy';
import { CAUSAL } from '../../test/wording';

describe('the prediction', () => {
  it('says how far the estimate sits from the threshold, and what the operating point does', () => {
    expect(gapNote(0.8029793735676088, 0.36, true)).toBe(
      '0.443 above the threshold. This customer would receive an offer.',
    );
    expect(gapNote(0.030126, 0.36, false)).toBe(
      '0.330 below the threshold. No offer at the current operating point.',
    );
  });

  it('words a gap too small to write in three decimals', () => {
    expect(gapNote(0.36, 0.36, true)).toBe(
      'At the threshold. This customer would receive an offer.',
    );
    expect(gapNote(0.3604, 0.36, true)).toBe(
      'Just above the threshold. This customer would receive an offer.',
    );
    expect(gapNote(0.3599, 0.36, false)).toBe(
      'Just below the threshold. No offer at the current operating point.',
    );
  });

  it('takes the threshold it names from the API', () => {
    expect(flagText(true, 0.36)).toBe('Flagged at 0.36');
    expect(flagText(false, 0.4)).toBe('Not flagged at 0.40');
    expect(gapNote(0.5, 0.4, true)).toBe(
      '0.100 above the threshold. This customer would receive an offer.',
    );
  });

  it('announces who is shown and what the API made of them, in one sentence', () => {
    expect(statusLine('README example', 0.8029793735676088, 'Critical', true, 0.36)).toBe(
      'README example: 80.3%, Critical band, flagged at 0.36.',
    );
    // The served clamp (1 - 0.000118) still reads below certain.
    expect(statusLine('DEMO-0005', 0.999882, 'Critical', true, 0.36)).toBe(
      'DEMO-0005: 99.99%, Critical band, flagged at 0.36.',
    );
    expect(statusLine('New customer', 0.03, 'Low', false, 0.36)).toBe(
      'New customer: 3.0%, Low band, not flagged at 0.36.',
    );
  });
});

describe('the picker', () => {
  it('describes a book customer by partition and outcome', () => {
    expect(cardMeta(customerDetail('A', { partition: 'validation', outcome: 'stayed' }))).toBe(
      'validation · stayed',
    );
    expect(cardMeta(customerDetail('A', { partition: 'test', outcome: 'withheld' }))).toBe(
      'test · withheld',
    );
    expect(cardMeta(customerDetail('A', { partition: 'unknown', outcome: 'withheld' }))).toBe(
      'unverified · withheld',
    );
  });

  it('says plainly when a linked customer is not in the book', () => {
    expect(notFound('DEMO-0099')).toBe('There is no customer DEMO-0099 in the book.');
  });
});

describe('the attributions', () => {
  it('writes the strongest as sentences, raising or lowering the estimate', () => {
    expect(
      sentence({ feature: 'tenure', label: 'Tenure (months)', value: 3, shap_value: 0.308 }),
    ).toEqual({
      label: 'Tenure',
      value: '3 months',
      text: 'raises the estimated risk by 30.8 pp, relative to an average customer.',
    });
    expect(
      sentence({
        feature: 'MonthlyCharges',
        label: 'Monthly charges',
        value: 95,
        shap_value: -0.1609,
      }),
    ).toEqual({
      label: 'Monthly charges',
      value: '$95.00',
      text: 'lowers the estimated risk by 16.1 pp, relative to an average customer.',
    });
    expect(
      sentence({
        feature: 'TechSupport',
        label: 'Tech support add-on',
        value: 'No',
        shap_value: 0.029,
      }).label,
    ).toBe('Tech support add-on');
  });

  it('never claims a direction for an attribution that rounds to nothing', () => {
    expect(
      sentence({ feature: 'Partner', label: 'Has partner', value: 'No', shap_value: 0.0004 }).text,
    ).toBe('moves the estimated risk by less than 0.05 pp, relative to an average customer.');
    expect(
      sentence({ feature: 'Partner', label: 'Has partner', value: 'No', shap_value: -0.0004 }).text,
    ).toBe('moves the estimated risk by less than 0.05 pp, relative to an average customer.');
  });

  it('counts inputs from the data, never a typed 18', () => {
    expect(inputsTag(18)).toBe('18 model inputs');
    expect(showAll(18)).toBe('All 18');
    expect(showAll(17)).toBe('All 17');
    expect(otherInputs(10)).toBe('10 other inputs');
    expect(otherInputs(1)).toBe('1 other input');
  });

  it('says how the server computed them, with its own figures', () => {
    expect(
      waterfallNote(
        {
          model_sha256: null,
          background_customers: 100,
          permutations: 10,
          seed: 42,
          caveat: '',
          explanations: [],
        },
        0.36,
      ),
    ).toBe(
      'Base plus contributions reproduces the served probability; the server checks that sum. Permutation explainer, 10 orderings, fixed seed, the same 100-customer training background on every page. Dashed line: threshold 0.36.',
    );
  });
});

describe('the live form', () => {
  it('says why an amount is not scored', () => {
    expect(amountMessage({ field: 'tenure', reason: 'format' })).toBe(
      'Tenure is a whole number of months.',
    );
    expect(amountMessage({ field: 'MonthlyCharges', reason: 'format' })).toBe(
      'Monthly charges are an amount in dollars.',
    );
    expect(amountMessage({ field: 'TotalCharges', reason: 'format' })).toBe(
      'Total charges are an amount in dollars.',
    );
    expect(amountMessage({ field: 'TotalCharges', reason: 'billed' })).toBe(
      'Total charges are zero only for a customer with no tenure.',
    );
  });

  it('gives the API’s own bounds for an amount outside them', () => {
    expect(amountMessage({ field: 'tenure', reason: 'range', min: 0, max: 120 })).toBe(
      'Tenure is 0 to 120 months.',
    );
    expect(amountMessage({ field: 'MonthlyCharges', reason: 'range', min: 18.25, max: 1000 })).toBe(
      'Monthly charges are $18.25 to $1,000.00.',
    );
    expect(amountMessage({ field: 'TotalCharges', reason: 'range', min: 0, max: 100000 })).toBe(
      'Total charges are $0.00 to $100,000.00.',
    );
  });

  it('names the address the browser used for the API, not a typed host', () => {
    expect(panelCaption('127.0.0.1:8001')).toBe(
      'POST /api/predict · 127.0.0.1:8001 · same number as the library',
    );
  });
});

describe('every sentence', () => {
  it('makes no causal claim, and claims no scoring in the browser', () => {
    const sentences = [
      LEDE,
      PROFILE_NOTE,
      SENTENCES_NOTE,
      LIVE_NOTE,
      EXPLAIN_FAILED,
      EXPLAIN_UNAVAILABLE,
      EXPLAIN_BLOCKED,
      gapNote(0.8, 0.36, true),
      gapNote(0.1, 0.36, false),
      sentence({ feature: 'tenure', label: 'Tenure (months)', value: 3, shap_value: 0.3 }).text,
      sentence({ feature: 'tenure', label: 'Tenure (months)', value: 3, shap_value: -0.3 }).text,
      waterfallNote(
        {
          model_sha256: null,
          background_customers: 100,
          permutations: 10,
          seed: 42,
          caveat: '',
          explanations: [],
        },
        0.36,
      ),
      notFound('A'),
    ];
    for (const text of sentences) {
      expect(text).not.toMatch(CAUSAL);
      expect(text).not.toMatch(/in your browser|orderings, so they can differ/i);
    }
  });
});
