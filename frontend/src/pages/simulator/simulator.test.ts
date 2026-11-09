import { ApiError } from '../../api/client';
import { DEFAULTS, sweepFor } from '../../test/simulatorApi';
import {
  atDefaults,
  cutAfter,
  echoes,
  readField,
  samePin,
  scenarioOf,
  selection,
  SIMULATOR_PARAMS,
  statusOf,
  textsOf,
  typedNumber,
  type State,
} from './simulator';

const BOUNDS = {
  retention_offer_cost: { min: 0, max: 10_000, integer: false },
  offer_success_rate: { min: 0, max: 1, integer: false },
  expected_horizon_months: { min: 1, max: 120, integer: true },
  gross_margin: { min: 0, max: 1, integer: false },
};

const NOTHING_SET: State = { cost: null, success: null, horizon: null, margin: null, t: '' };

describe('reading a typed assumption', () => {
  it.each([
    ['12.5', 12.5],
    ['12.', 12],
    ['.5', 0.5],
    ['0', 0],
    ['10000', 10_000],
    ['12.340', 12.34],
  ])('reads the cost %s as %d', (text, value) => {
    expect(readField(text, BOUNDS.retention_offer_cost, 2)).toEqual({ value, problem: null });
  });

  it.each([
    ['12,50', { reason: 'format' }],
    ['1e3', { reason: 'format' }],
    ['$50', { reason: 'format' }],
    ['', { reason: 'required' }],
    ['-', { reason: 'format' }],
    ['.', { reason: 'format' }],
    ['-.', { reason: 'format' }],
    ['12.345', { reason: 'step' }],
    ['-1', { reason: 'range', min: 0, max: 10_000 }],
    ['10000.01', { reason: 'range', min: 0, max: 10_000 }],
    // Longer than any number a field takes: refused whole, never cut to one it takes.
    ['0000000000050', { reason: 'long' }],
    ['50.00000000001', { reason: 'long' }],
  ])('refuses the cost %j', (text, problem) => {
    expect(readField(text, BOUNDS.retention_offer_cost, 2)).toEqual({ value: null, problem });
  });

  it('holds a whole number of months to whole numbers, "12.0" being twelve', () => {
    const range = BOUNDS.expected_horizon_months;
    expect(readField('12', range, 2)).toEqual({ value: 12, problem: null });
    expect(readField('12.0', range, 2)).toEqual({ value: 12, problem: null });
    expect(readField('12.5', range, 2)).toEqual({ value: null, problem: { reason: 'whole' } });
    expect(readField('0', range, 2).problem).toEqual({ reason: 'range', min: 1, max: 120 });
    expect(readField('121', range, 2).problem).toEqual({ reason: 'range', min: 1, max: 120 });
  });

  it('takes a rate in whole percents, as the slider shows it', () => {
    const range = BOUNDS.offer_success_rate;
    expect(readField('0.29', range, 2)).toEqual({ value: 0.29, problem: null });
    expect(readField('1', range, 2)).toEqual({ value: 1, problem: null });
    expect(readField('0.295', range, 2)).toEqual({ value: null, problem: { reason: 'step' } });
    expect(readField('1.01', range, 2).problem).toEqual({ reason: 'range', min: 0, max: 1 });
  });

  it('takes as many decimals as the money’s minor unit has', () => {
    const range = BOUNDS.retention_offer_cost;
    expect(readField('12.5', range, 0).problem).toEqual({ reason: 'step' });
    expect(readField('12', range, 0).value).toBe(12);
    expect(readField('1.125', range, 3).value).toBe(1.125);
  });

  it('holds the money in a served currency to that currency’s minor unit', () => {
    const yen = scenarioOf({ ...NOTHING_SET, cost: '12.5' }, BOUNDS, {
      ...DEFAULTS,
      currency: 'JPY',
    });
    expect(yen.readings.cost.problem).toEqual({ reason: 'step' });
    const dollars = scenarioOf({ ...NOTHING_SET, cost: '12.5' }, BOUNDS, DEFAULTS);
    expect(dollars.readings.cost.value).toBe(12.5);
  });

  it('checks the range the server serves, not a copy of it', () => {
    const narrow = { min: 5, max: 200, integer: false };
    expect(readField('4.99', narrow, 2).problem).toEqual({ reason: 'range', min: 5, max: 200 });
    expect(readField('5', narrow, 2).value).toBe(5);
    expect(readField('200', narrow, 2).value).toBe(200);
    const whole = { min: 0, max: 10_000, integer: true };
    expect(readField('12.5', whole, 2).problem).toEqual({ reason: 'whole' });
  });
});

describe('the scenario the page asks for', () => {
  it('sends the defaults when nothing is set, all four of them', () => {
    expect(scenarioOf(NOTHING_SET, BOUNDS, DEFAULTS).assumptions).toEqual({
      cost: 50,
      success: 0.3,
      horizon: 12,
      margin: 0.65,
    });
  });

  it('asks for the same thing whether a default is left out or written', () => {
    const written = scenarioOf({ ...NOTHING_SET, cost: '50.00' }, BOUNDS, DEFAULTS);
    expect(written.assumptions).toEqual(scenarioOf(NOTHING_SET, BOUNDS, DEFAULTS).assumptions);
  });

  it('asks for nothing while any field cannot be sent, and says which', () => {
    const scenario = scenarioOf({ ...NOTHING_SET, horizon: '2.5', margin: '' }, BOUNDS, DEFAULTS);
    expect(scenario.assumptions).toBeNull();
    expect(scenario.readings.horizon.problem).toEqual({ reason: 'whole' });
    expect(scenario.readings.margin.problem).toEqual({ reason: 'required' });
    expect(scenario.readings.cost.problem).toBeNull();
  });

  it('shows what was typed, and the default where nothing was', () => {
    const scenario = scenarioOf({ ...NOTHING_SET, cost: '12.' }, BOUNDS, DEFAULTS);
    expect(scenario.texts).toEqual({ cost: '12.', success: '0.3', horizon: '12', margin: '0.65' });
  });

  it('holds a configured default to the same rules as a typed value', () => {
    const scenario = scenarioOf(NOTHING_SET, BOUNDS, { ...DEFAULTS, offer_success_rate: 0.295 });
    expect(scenario.readings.success.problem).toEqual({ reason: 'step' });
    expect(scenario.assumptions).toBeNull();
  });

  it('is at the defaults only with every default in place and the cut following', () => {
    const at = (state: State) => atDefaults(scenarioOf(state, BOUNDS, DEFAULTS), DEFAULTS, state.t);
    expect(at(NOTHING_SET)).toBe(true);
    expect(at({ ...NOTHING_SET, cost: '50' })).toBe(true);
    expect(at({ ...NOTHING_SET, cost: '51' })).toBe(false);
    expect(at({ ...NOTHING_SET, cost: '' })).toBe(false);
    expect(at({ ...NOTHING_SET, t: '0.40' })).toBe(false);
  });
});

describe('whether the figures are for the assumptions on screen', () => {
  const assumptions = { cost: 50, success: 0.3, horizon: 12, margin: 0.65 };
  const sweep = sweepFor(DEFAULTS);

  it('matches the echo field by field', () => {
    expect(echoes(sweep, assumptions)).toBe(true);
    for (const change of [{ cost: 51 }, { success: 0.31 }, { horizon: 13 }, { margin: 0.6 }]) {
      expect(echoes(sweep, { ...assumptions, ...change })).toBe(false);
    }
  });

  const base = { withheld: false, assumptions, sweep, placeholder: false, error: null };

  it.each([
    ['current', base],
    ['loading', { ...base, sweep: undefined }],
    ['updating', { ...base, placeholder: true }],
    ['mismatch', { ...base, assumptions: { ...assumptions, cost: 60 } }],
    ['unsent', { ...base, assumptions: null }],
    ['unsent', { ...base, assumptions: null, sweep: undefined }],
    ['failed', { ...base, sweep: undefined, error: new ApiError(500, 'internal_error') }],
    ['withheld', { ...base, withheld: true }],
  ] as const)('is %s', (status, input) => {
    expect(statusOf(input)).toBe(status);
  });
});

describe('the cut', () => {
  const sweep = sweepFor(DEFAULTS);
  const recommended = sweep.points.findIndex(
    (point) => point.threshold === sweep.recommended.threshold,
  );

  it('follows the recommendation until a cut is set', () => {
    expect(recommended).toBeGreaterThan(0);
    expect(selection(sweep, '')).toEqual({ index: recommended, recommended, following: true });
  });

  it('finds a cut by its label, inexact grid values included', () => {
    expect(sweep.points[35]?.threshold).toBe(0.35000000000000003);
    expect(selection(sweep, '0.35')).toEqual({ index: 35, recommended, following: false });
  });

  it('follows the recommendation for a label that names no point', () => {
    expect(selection(sweep, '0.3').index).toBe(recommended);
    expect(selection(sweep, '1.5').index).toBe(recommended);
  });

  it('does not follow a cut the address sets, even at the recommendation', () => {
    // Set by hand, it stays where it is when the assumptions, and so the recommendation, move.
    const label = sweep.recommended.threshold.toFixed(2);
    expect(selection(sweep, label)).toEqual({ index: recommended, recommended, following: false });
  });

  it('reads no cut beyond the grid from the address', () => {
    expect(SIMULATOR_PARAMS.t.parse(['1.50'])).toBe('');
    expect(SIMULATOR_PARAMS.t.parse(['9.99'])).toBe('');
    expect(SIMULATOR_PARAMS.t.parse(['1.00'])).toBe('1.00');
    expect(SIMULATOR_PARAMS.t.parse(['0.07'])).toBe('0.07');
  });

  it('writes no cut for the recommended point, so the page keeps following it', () => {
    expect(cutAfter(sweep, recommended)).toBe('');
    expect(cutAfter(sweep, 35)).toBe('0.35');
    expect(cutAfter(sweep, 100)).toBe('1.00');
  });
});

describe('pinned scenarios', () => {
  const sweep = sweepFor(DEFAULTS);

  it('are the same only for the same assumptions and the same cut', () => {
    const pin = { assumptions: sweep.assumptions, t: '0.35' };
    expect(samePin(pin, { assumptions: { ...sweep.assumptions }, t: '0.35' })).toBe(true);
    expect(samePin(pin, { ...pin, t: '0.36' })).toBe(false);
    expect(samePin(pin, { ...pin, assumptions: { ...sweep.assumptions, gross_margin: 0.6 } })).toBe(
      false,
    );
    expect(samePin(pin, { ...pin, assumptions: { ...sweep.assumptions, currency: 'EUR' } })).toBe(
      false,
    );
  });

  it('give back the assumptions the server echoed, as the address holds them', () => {
    expect(textsOf({ ...DEFAULTS, retention_offer_cost: 12.5, offer_success_rate: 0.15 })).toEqual({
      cost: '12.5',
      success: '0.15',
      horizon: '12',
      margin: '0.65',
    });
  });
});

describe('what a number box keeps of what is typed', () => {
  it.each([
    ['12.5', '12.5'],
    ['−5', '-5'],
    [' 7 ', '7'],
    ['1 250', '1250'],
    ['12,50', '12,50'],
    ['1e3', '1e3'],
    ['$50', '$50'],
    ['5−', '5−'],
    ['0000000000050', '0000000000050'],
  ])('keeps %j as %j: nothing typed is turned into another number', (typed, kept) => {
    expect(typedNumber(typed)).toBe(kept);
  });

  it('cuts only a paste far longer than any number, and that is refused as too long', () => {
    const kept = typedNumber('5'.repeat(200));
    expect(kept.length).toBeLessThan(200);
    expect(readField(kept, BOUNDS.retention_offer_cost, 2).problem).toEqual({ reason: 'long' });
    // The address holds what the box keeps, so the box never falls back to the default.
    expect(SIMULATOR_PARAMS.cost.parse([kept])).toBe(kept);
  });
});
