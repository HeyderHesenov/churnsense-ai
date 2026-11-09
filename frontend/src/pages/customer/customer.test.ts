import type { CustomerFeatures, Explanation } from '../../api/queries';
import { customerFeatures } from '../../test/fixtures';
import {
  ADD_ONS,
  bar,
  choiceKey,
  coherent,
  CUSTOMER_PARAMS,
  draftOf,
  estimatedTotal,
  featureValue,
  GROUPS,
  noTenure,
  amountProblems,
  pickerChoices,
  recordOf,
  showChanges,
  shownBy,
  strongest,
  waterfall,
} from './customer';

/** The API's bounds, as ``/model-info`` serves them. */
const BOUNDS = {
  tenure: { min: 0, max: 120 },
  MonthlyCharges: { min: 0, max: 1000 },
  TotalCharges: { min: 0, max: 100000 },
};

const README = customerFeatures({ tenure: 3, MonthlyCharges: 95, TotalCharges: 285 });

/** Six attributions, strongest first: base 0.25, served 0.485. */
const EXPLAINED: Explanation = {
  customer_id: null,
  base_value: 0.25,
  churn_probability: 0.485,
  contributions: [
    { feature: 'tenure', label: 'Tenure (months)', value: 3, shap_value: 0.3 },
    { feature: 'MonthlyCharges', label: 'Monthly charges', value: 95, shap_value: -0.1 },
    { feature: 'Contract', label: 'Contract type', value: 'Month-to-month', shap_value: 0.05 },
    {
      feature: 'InternetService',
      label: 'Internet service',
      value: 'Fiber optic',
      shap_value: -0.02,
    },
    { feature: 'TechSupport', label: 'Tech support add-on', value: 'No', shap_value: 0.01 },
    { feature: 'SeniorCitizen', label: 'Senior citizen', value: '0', shap_value: -0.005 },
  ],
};

describe('which customer the address shows', () => {
  it('reads a book customer, the new customer, or else the README example', () => {
    expect(shownBy({ id: 'DEMO-0001', live: null })).toEqual({ kind: 'book', id: 'DEMO-0001' });
    expect(shownBy({ id: '', live: null })).toEqual({ kind: 'readme' });
    // The new customer is a choice of its own; an ID left beside it is not shown.
    expect(shownBy({ id: 'DEMO-0001', live: 'true' })).toEqual({ kind: 'live' });
  });

  it('writes one choice and clears the other', () => {
    expect(showChanges({ kind: 'live' })).toEqual({ id: '', live: 'true' });
    expect(showChanges({ kind: 'book', id: 'DEMO-0002' })).toEqual({ id: 'DEMO-0002', live: null });
    expect(showChanges({ kind: 'readme' })).toEqual({ id: '', live: null });
  });

  it('never reads an ID the API would refuse, nor a stray flag', () => {
    expect(CUSTOMER_PARAMS.id.parse(['../x'])).toBe('');
    expect(CUSTOMER_PARAMS.id.parse(['A'.repeat(33)])).toBe('');
    expect(CUSTOMER_PARAMS.id.parse(['A'.repeat(32)])).toBe('A'.repeat(32));
    expect(CUSTOMER_PARAMS.live.parse(['yes'])).toBeNull();
    expect(CUSTOMER_PARAMS.live.parse(['true'])).toBe('true');
  });
});

describe('the picker', () => {
  it('lists the README example, the examples, then the new customer', () => {
    expect(pickerChoices(['DEMO-0001', 'DEMO-0002'], null)).toEqual([
      { kind: 'readme' },
      { kind: 'book', id: 'DEMO-0001' },
      { kind: 'book', id: 'DEMO-0002' },
      { kind: 'live' },
    ]);
  });

  it('puts a customer a link opened first, unless it is an example', () => {
    expect(pickerChoices(['DEMO-0001'], 'DEMO-0042')).toEqual([
      { kind: 'book', id: 'DEMO-0042' },
      { kind: 'readme' },
      { kind: 'book', id: 'DEMO-0001' },
      { kind: 'live' },
    ]);
    expect(pickerChoices(['DEMO-0001'], 'DEMO-0001')).toEqual([
      { kind: 'readme' },
      { kind: 'book', id: 'DEMO-0001' },
      { kind: 'live' },
    ]);
  });

  it('names every choice apart, for keys and ids', () => {
    expect(choiceKey({ kind: 'readme' })).toBe('readme');
    expect(choiceKey({ kind: 'live' })).toBe('live');
    expect(choiceKey({ kind: 'book', id: 'live' })).toBe('id-live');
  });
});

describe('the live form', () => {
  const draft = draftOf(README);

  it('holds the amounts as the text typed, the rest as the record has them', () => {
    expect(draft).toEqual({ ...README, tenure: '3', MonthlyCharges: '95', TotalCharges: '285' });
  });

  it('keeps the add-ons in step with the internet service', () => {
    const none = coherent({ ...draft, StreamingTV: 'Yes', InternetService: 'No' });
    for (const key of ADD_ONS) expect(none[key]).toBe('No internet service');
    const back = coherent({ ...none, InternetService: 'DSL' });
    for (const key of ADD_ONS) expect(back[key]).toBe('No');
    expect(coherent({ ...draft, StreamingTV: 'Yes' }).StreamingTV).toBe('Yes');
  });

  it('keeps multiple lines in step with the phone service', () => {
    const none = coherent({ ...draft, MultipleLines: 'Yes', PhoneService: 'No' });
    expect(none.MultipleLines).toBe('No phone service');
    expect(coherent({ ...none, PhoneService: 'Yes' }).MultipleLines).toBe('No');
    expect(coherent({ ...draft, MultipleLines: 'Yes' }).MultipleLines).toBe('Yes');
  });

  it('knows a customer with no tenure yet, however the zero is typed', () => {
    expect(noTenure({ ...draft, tenure: '0' })).toBe(true);
    expect(noTenure({ ...draft, tenure: '000' })).toBe(true);
    expect(noTenure({ ...draft, tenure: '' })).toBe(false);
    expect(noTenure({ ...draft, tenure: '1' })).toBe(false);
  });

  it('bills nothing to a customer with no tenure', () => {
    expect(coherent({ ...draft, tenure: '0' }).TotalCharges).toBe('0');
    expect(coherent({ ...draft, tenure: '00' }).TotalCharges).toBe('0');
    expect(coherent({ ...draft, tenure: '' }).TotalCharges).toBe('285');
    expect(coherent({ ...draft, tenure: '10' }).TotalCharges).toBe('285');
  });

  it('sends a record only when every amount reads as a number', () => {
    expect(recordOf(draft)).toEqual(README);
    expect(recordOf({ ...draft, tenure: '06' })?.tenure).toBe(6);
    expect(recordOf({ ...draft, MonthlyCharges: '95.5' })?.MonthlyCharges).toBe(95.5);
    expect(recordOf({ ...draft, MonthlyCharges: '.5' })?.MonthlyCharges).toBe(0.5);
    for (const tenure of ['', '2.5', '-1', '1e2', ' 3', '3 '])
      expect(recordOf({ ...draft, tenure })).toBeNull();
    for (const total of ['', '.', '-5', 'Infinity', '1,000', '1e3', '0x10', '1.2.3', '..5'])
      expect(recordOf({ ...draft, TotalCharges: total })).toBeNull();
  });

  it('names the amounts that cannot be sent, in the form’s order', () => {
    expect(amountProblems(draft, BOUNDS)).toEqual([]);
    expect(amountProblems({ ...draft, TotalCharges: 'x', tenure: '' }, BOUNDS)).toEqual([
      { field: 'tenure', reason: 'format' },
      { field: 'TotalCharges', reason: 'format' },
    ]);
  });

  it('refuses nothing billed to a customer with tenure, as the API does', () => {
    const unbilled = { ...draft, TotalCharges: '0' };
    expect(amountProblems(unbilled, BOUNDS)).toEqual([{ field: 'TotalCharges', reason: 'billed' }]);
    expect(recordOf(unbilled)).toBeNull();
    expect(amountProblems({ ...draft, TotalCharges: '0.0' }, BOUNDS)).toEqual([
      { field: 'TotalCharges', reason: 'billed' },
    ]);
    // With no tenure, nothing billed is what the contract asks for.
    const fresh = coherent({ ...draft, tenure: '0' });
    expect(amountProblems(fresh, BOUNDS)).toEqual([]);
    expect(recordOf(fresh)).toEqual({ ...README, tenure: 0, TotalCharges: 0 });
  });

  it('refuses an amount outside the API’s bounds once they are known, as the API would', () => {
    const edges = { ...draft, tenure: '120', MonthlyCharges: '1000', TotalCharges: '100000' };
    expect(amountProblems(edges, BOUNDS)).toEqual([]);
    expect(
      amountProblems(
        { ...draft, tenure: '121', MonthlyCharges: '1000.01', TotalCharges: '100000.5' },
        BOUNDS,
      ),
    ).toEqual([
      { field: 'tenure', reason: 'range', min: 0, max: 120 },
      { field: 'MonthlyCharges', reason: 'range', min: 0, max: 1000 },
      { field: 'TotalCharges', reason: 'range', min: 0, max: 100000 },
    ]);
    // A floor is a bound too.
    const floored = { ...BOUNDS, MonthlyCharges: { min: 18, max: 1000 } };
    expect(amountProblems({ ...draft, MonthlyCharges: '17.99' }, floored)).toEqual([
      { field: 'MonthlyCharges', reason: 'range', min: 18, max: 1000 },
    ]);
    expect(amountProblems({ ...draft, MonthlyCharges: '18' }, floored)).toEqual([]);
    // Until the bounds are in, the API is left to refuse; the record never depends on them.
    expect(amountProblems({ ...draft, tenure: '121' }, undefined)).toEqual([]);
    expect(recordOf({ ...draft, tenure: '121' })?.tenure).toBe(121);
  });

  it('estimates total charges as tenure times monthly charges, to the cent', () => {
    expect(estimatedTotal(draft)).toBe('285');
    expect(estimatedTotal({ ...draft, tenure: '12', MonthlyCharges: '70.35' })).toBe('844.2');
    expect(estimatedTotal({ ...draft, tenure: '7', MonthlyCharges: '0.335' })).toBe('2.35');
    expect(estimatedTotal({ ...draft, tenure: '' })).toBeNull();
    expect(estimatedTotal({ ...draft, MonthlyCharges: 'x' })).toBeNull();
  });
});

describe('the profile', () => {
  it('groups the inputs as the artboard does, each exactly once', () => {
    expect(GROUPS.map((group) => group.title)).toEqual(['Account', 'Services', 'Household']);
    const keys = GROUPS.flatMap((group) => group.features);
    expect([...keys].sort()).toEqual(Object.keys(README).sort());
  });

  it('writes each value the way the profile does', () => {
    const features = customerFeatures({
      tenure: 1,
      TotalCharges: 1810.25,
      PaymentMethod: 'Credit card (automatic)',
      SeniorCitizen: '1',
    });
    expect(featureValue('tenure', features)).toBe('1 month');
    expect(featureValue('TotalCharges', features)).toBe('$1,810.25');
    expect(featureValue('MonthlyCharges', features)).toBe('$90.00');
    expect(featureValue('PaymentMethod', features)).toBe('Credit card (auto)');
    expect(featureValue('SeniorCitizen', features)).toBe('Yes');
    expect(featureValue('InternetService', features)).toBe('Fiber optic');
  });

  it('names the four strongest attributions, in the API’s order', () => {
    expect([...strongest(EXPLAINED)]).toEqual([
      'tenure',
      'MonthlyCharges',
      'Contract',
      'InternetService',
    ]);
    expect(strongest(undefined).size).toBe(0);
  });

  it('has no gender among the inputs: the schema the API publishes leaves it out', () => {
    type HasGender = 'gender' extends keyof CustomerFeatures ? true : false;
    const hasGender: HasGender = false;
    expect(hasGender).toBe(false);
  });
});

describe('the waterfall', () => {
  it('runs from the base through each attribution shown, then the rest combined', () => {
    const drawn = waterfall(EXPLAINED, 3);
    expect(drawn.base).toBe(0.25);
    expect(drawn.steps.map((step) => step.feature)).toEqual([
      'tenure',
      'MonthlyCharges',
      'Contract',
    ]);
    const [tenure, monthly, contract] = drawn.steps;
    expect(tenure?.start).toBeCloseTo(0.25, 12);
    expect(tenure?.end).toBeCloseTo(0.55, 12);
    expect(monthly?.start).toBeCloseTo(0.55, 12);
    expect(monthly?.end).toBeCloseTo(0.45, 12);
    expect(contract?.end).toBeCloseTo(0.5, 12);
    expect(drawn.rest?.count).toBe(3);
    expect(drawn.rest?.shap).toBeCloseTo(-0.015, 12);
    expect(drawn.rest?.start).toBeCloseTo(0.5, 12);
    // The last bar ends at the served probability.
    expect(drawn.rest?.end).toBeCloseTo(EXPLAINED.churn_probability, 12);
    expect(drawn.total).toBe(0.485);
  });

  it('combines even a single attribution left over', () => {
    const drawn = waterfall(EXPLAINED, 5);
    expect(drawn.rest?.count).toBe(1);
    expect(drawn.rest?.shap).toBeCloseTo(-0.005, 12);
  });

  it('combines nothing when every attribution is shown', () => {
    expect(waterfall(EXPLAINED, 6).rest).toBeNull();
    expect(waterfall(EXPLAINED, 18).steps).toHaveLength(6);
    expect(waterfall(EXPLAINED, 18).rest).toBeNull();
  });

  it('draws a bar between two points of the scale, never too thin to see or past an end', () => {
    expect(bar(0.25, 0.55)).toEqual({ left: 0.25, width: expect.closeTo(0.3, 12) as number });
    expect(bar(0.55, 0.45)).toEqual({ left: 0.45, width: expect.closeTo(0.1, 12) as number });
    expect(bar(0.5, 0.5001)).toEqual({ left: 0.5, width: 0.004 });
    expect(bar(0.9, 1.2)).toEqual({ left: 0.9, width: expect.closeTo(0.1, 12) as number });
    expect(bar(0.1, -0.2)).toEqual({ left: 0, width: 0.1 });
    expect(bar(1, 1.3)).toEqual({ left: 0.996, width: 0.004 });
  });
});
