import { DEFAULTS, sweepFor } from '../../test/simulatorApi';
import {
  boundsNote,
  fieldMessage,
  LEDE,
  netParts,
  pinSummary,
  precisionText,
  provenance,
  recallText,
  SIMULATED_NOTE,
  statusNote,
  sensitivityNote,
  whyNotPeak,
} from './copy';
import { FIELDS, type FieldKey, type FieldProblem, type Status } from './simulator';

function spec(key: FieldKey) {
  const found = FIELDS.find((field) => field.key === key);
  if (!found) throw new Error(key);
  return found;
}

describe('the fields', () => {
  it.each([
    ['cost', { reason: 'required' }, 'Enter the cost of one offer.'],
    [
      'cost',
      { reason: 'format' },
      'The cost of one offer is a number, written in digits with a point for any decimals.',
    ],
    ['cost', { reason: 'step' }, 'The cost of one offer has at most 2 decimal places.'],
    ['cost', { reason: 'long' }, 'The cost of one offer is written in at most 12 characters.'],
    ['cost', { reason: 'range', min: 1, max: 500 }, 'The cost of one offer is $1 to $500.'],
    ['success', { reason: 'step' }, 'The success rate is in whole percents.'],
    ['success', { reason: 'range', min: 0, max: 0.8 }, 'The success rate is 0% to 80%.'],
    ['horizon', { reason: 'whole' }, 'The horizon is a whole number of months.'],
    ['horizon', { reason: 'range', min: 2, max: 60 }, 'The horizon is 2 to 60 months.'],
    ['margin', { reason: 'range', min: 0.1, max: 0.9 }, 'The gross margin is 10% to 90%.'],
  ] as [FieldKey, FieldProblem, string][])(
    'says why %s cannot be sent (%j)',
    (key, problem, text) => {
      expect(fieldMessage(spec(key), problem, 'USD')).toBe(text);
    },
  );

  it('says a money step in the currency’s own decimals', () => {
    expect(fieldMessage(spec('cost'), { reason: 'step' }, 'JPY')).toBe(
      'The cost of one offer is a whole amount.',
    );
    expect(fieldMessage(spec('cost'), { reason: 'step' }, 'KWD')).toBe(
      'The cost of one offer has at most 3 decimal places.',
    );
  });

  it('states the served bounds and default in the field’s own units', () => {
    const range = { min: 0, max: 10_000, integer: false };
    expect(boundsNote(spec('cost'), range, 12.5, 'USD')).toBe('$0 to $10,000 · default $12.50');
    expect(boundsNote(spec('cost'), range, 50, 'EUR')).toBe('€0 to €10,000 · default €50');
    expect(boundsNote(spec('success'), { min: 0, max: 1, integer: false }, 0.3, 'USD')).toBe(
      '0% to 100% · default 30%',
    );
    expect(boundsNote(spec('horizon'), { min: 1, max: 120, integer: true }, 12, 'USD')).toBe(
      '1 to 120 months · default 12 months',
    );
  });
});

describe('what the figures are for', () => {
  it('names the echoed assumptions and the partition they were worked out on', () => {
    expect(provenance(sweepFor({ ...DEFAULTS, retention_offer_cost: 12.5 }))).toBe(
      'Figures for a $12.50 offer, 30% success, a 12-month horizon and 65% margin, ' +
        'on 1,250 validation customers (310 churned).',
    );
  });

  it.each([
    ['current', true, null],
    ['loading', false, null],
    ['withheld', false, null],
    ['failed', false, null],
    [
      'updating',
      true,
      'Updating. Until the new figures arrive, those below are for the assumptions they name.',
    ],
    [
      'unsent',
      true,
      'Not sent: correct the marked field. The figures below are for the assumptions they name.',
    ],
    ['unsent', false, 'Not sent: correct the marked field.'],
    [
      'mismatch',
      true,
      'The server answered for other assumptions than those set: the figures below are for the ones they name.',
    ],
  ] as [Status, boolean, string | null][])(
    'says, when %s (figures shown: %s), %j',
    (status, shown, text) => {
      expect(statusNote(status, shown)).toBe(text);
    },
  );
});

describe('a figure at the cut', () => {
  it('signs the net and says in words which way it goes, from the figure shown', () => {
    expect(netParts(44_721.5, 'USD')).toEqual({ amount: '+$44,722', word: 'net gain' });
    expect(netParts(-1_200, 'USD')).toEqual({ amount: '−$1,200', word: 'net loss' });
    expect(netParts(0.4, 'USD')).toEqual({ amount: '$0', word: 'break-even' });
    expect(netParts(-0.4, 'USD')).toEqual({ amount: '$0', word: 'break-even' });
  });

  it('shows no precision where nothing is flagged, and no recall without churners', () => {
    expect(precisionText({ flagged: 0, precision: 0 })).toBe('—');
    expect(precisionText({ flagged: 12, precision: 0 })).toBe('0.000');
    expect(precisionText({ flagged: 12, precision: 0.57143 })).toBe('0.571');
    expect(recallText({ recall: 0 }, 0)).toBe('—');
    expect(recallText({ recall: 0 }, 310)).toBe('0.000');
    expect(recallText({ recall: 1 }, 310)).toBe('1.000');
  });

  it('sums up a pinned scenario in the units its fields are set in', () => {
    expect(pinSummary({ ...DEFAULTS, retention_offer_cost: 7.5 })).toBe(
      '$7.50 · 30% · 12 mo · 65%',
    );
  });
});

describe('why not the peak', () => {
  const scenario = (threshold: number, net: number, flagged = 100) => ({
    threshold,
    flagged,
    true_positives: 50,
    precision: 0.5,
    recall: 0.2,
    intervention_cost: 1,
    retained_value: 2,
    net_benefit: net,
  });
  const base = sweepFor(DEFAULTS);

  it('says when the recommendation is the peak', () => {
    const sweep = { ...base, peak: scenario(0.3, 45_000), recommended: scenario(0.3, 45_000) };
    expect(whyNotPeak(sweep)).toBe(
      'The recommendation is the peak: 0.30, at +$45,000. No higher cut comes within one offer ($50) of it.',
    );
  });

  it('gives both cuts and both nets, and the rule between them', () => {
    const sweep = { ...base, peak: scenario(0.3, 45_000), recommended: scenario(0.36, 44_980) };
    expect(whyNotPeak(sweep)).toBe(
      'The peak sits at 0.30 (+$45,000). The recommendation, 0.36 (+$44,980), is the highest ' +
        'cut whose net is within one offer ($50) of it: on a figure built from an assumed ' +
        'success rate, cuts that close are treated as indistinguishable, and the tie goes to ' +
        'the higher cut, which makes fewer offers.',
    );
  });

  it('allows no tolerance when an offer costs nothing', () => {
    const free = { ...DEFAULTS, retention_offer_cost: 0 };
    const sweep = {
      ...sweepFor(free),
      peak: scenario(0.05, 90_000),
      recommended: scenario(0.12, 90_000),
    };
    expect(whyNotPeak(sweep)).toBe(
      'With an offer that costs nothing there is no tolerance: the recommendation, 0.12 ' +
        '(+$90,000), is the highest cut whose net equals the peak’s (0.05, +$90,000).',
    );
  });

  it('says a free offer’s recommendation is the peak without naming a tolerance', () => {
    const free = { ...DEFAULTS, retention_offer_cost: 0 };
    const sweep = {
      ...sweepFor(free),
      peak: scenario(0.05, 90_000),
      recommended: scenario(0.05, 90_000),
    };
    expect(whyNotPeak(sweep)).toBe(
      'With an offer that costs nothing there is no tolerance, and the recommendation is the ' +
        'peak: 0.05, at +$90,000.',
    );
  });

  it('says when no cut pays, and how many the recommendation flags', () => {
    const sweep = { ...base, peak: scenario(1, 0, 0), recommended: scenario(1, 0, 0) };
    expect(whyNotPeak(sweep)).toBe(
      'No cut pays under these assumptions: the best simulated net is $0, at 1.00. ' +
        'The recommendation, 1.00, flags no one.',
    );
    const losing = { ...base, peak: scenario(0.9, -50, 1), recommended: scenario(0.9, -50, 1) };
    expect(whyNotPeak(losing)).toBe(
      'No cut pays under these assumptions: the best simulated net is −$50, at 0.90. ' +
        'The recommendation, 0.90, flags 1 customer.',
    );
  });
});

describe('if the success rate were…', () => {
  const row = (rate: number, threshold: number) => ({
    offer_success_rate: rate,
    threshold,
    flagged: 1,
    precision: 0.5,
    recall: 0.5,
    net_benefit: 1,
  });

  it('says how far the recommended cut moves across the rates the API tried', () => {
    expect(sensitivityNote([row(0.1, 0.88), row(0.3, 0.36), row(0.5, 0.22)])).toBe(
      'Across these rates the recommended cut moves from 0.88 at 10% to 0.22 at 50%: ' +
        'where to cut is a business decision as much as a model’s.',
    );
  });

  it('gives the span, not a move, when the first and last rates share a cut', () => {
    expect(sensitivityNote([row(0.1, 0.3), row(0.3, 0.45), row(0.5, 0.3)])).toBe(
      'Across these rates the recommended cut moves between 0.30 and 0.45.',
    );
  });

  it('says when it does not move at all', () => {
    expect(sensitivityNote([row(0.1, 1), row(0.5, 1)])).toBe(
      'Across these rates the recommended cut stays at 1.00.',
    );
    expect(sensitivityNote([])).toBeNull();
  });
});

describe('the words', () => {
  // tests/test_wording.py's CAUSAL, which reads the built bundle too.
  const CAUSAL =
    /\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents|guarantees?|guaranteed)\b/i;
  /** A simulated retention is never told as customers kept for certain. */
  const CERTAIN = /\b(saved?|saves|rescued?|prevented)\b/i;

  it('make no causal claim, and never say customers were saved', () => {
    const sweep = sweepFor(DEFAULTS);
    const statuses: Status[] = ['updating', 'unsent', 'mismatch'];
    const sentences = [
      LEDE,
      SIMULATED_NOTE.title,
      SIMULATED_NOTE.body,
      provenance(sweep),
      whyNotPeak(sweep),
      sensitivityNote(sweep.sensitivity) ?? '',
      ...statuses.map((status) => statusNote(status, true) ?? ''),
    ];
    for (const text of sentences) {
      expect(text).not.toMatch(CAUSAL);
      expect(text).not.toMatch(CERTAIN);
    }
  });
});
