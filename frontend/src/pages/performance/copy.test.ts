import { curve, performance } from '../../test/performanceApi';
import * as copy from './copy';
import { CAUSAL } from '../../test/wording';

const calibration = performance().calibration;
const candidates = performance().candidates ?? [];
const served = performance().selection;
if (!served) throw new Error('the fixture serves a selection');
const selection = served;
const [forest, , logistic] = candidates;
const name = (model: string) => model.toLowerCase();

describe('the lede', () => {
  it('names the partitions’ jobs and, without a record, says no figure is a test result', () => {
    expect(copy.lede(5, 4225, 1409, false)).toBe(
      'Chosen by 5-fold cross-validation on 4,225 training customers; the cut, and whether to keep calibration, were decided on 1,409 validation customers. No test evaluation is shown for this model, so no figure here is a test result.',
    );
    expect(copy.lede(null, 4225, 1409, false)).toMatch(/^Chosen by cross-validation on 4,225/);
    expect(copy.lede(5, 4225, 1409, true)).toMatch(
      /one recorded release, counts and rates only, read on a re-used holdout\.$/,
    );
  });
});

describe('calibration', () => {
  it.each([
    [
      'kept',
      calibration,
      'Isotonic calibration was kept because validation ECE on raw probabilities fell from 0.144 to 0.028. Probabilities are clamped to [ε, 1 − ε], ε = 1.2e-4.',
    ],
    [
      'tried and not kept',
      { ...calibration, method: null },
      'Calibration was tried and not kept: validation ECE on raw probabilities was 0.028 calibrated against 0.144 without. Probabilities are clamped to [ε, 1 − ε], ε = 1.2e-4.',
    ],
    [
      'never tried',
      { ...calibration, method: null, uncalibrated_ece: null, calibrated_ece: null, epsilon: null },
      'The served probabilities are not calibrated.',
    ],
    [
      'not kept, with one figure missing',
      { ...calibration, method: null, uncalibrated_ece: null },
      'The served probabilities are not calibrated. Probabilities are clamped to [ε, 1 − ε], ε = 1.2e-4.',
    ],
    [
      'kept, with no figures recorded',
      { ...calibration, uncalibrated_ece: null, epsilon: null },
      'Isotonic calibration was kept.',
    ],
  ] as const)('says what happened when it was %s', (_, given, sentence) => {
    expect(copy.calibrationSentence(given)).toBe(sentence);
  });
});

describe('the one-standard-error rule', () => {
  it('writes the cutoff from the served cutoff, leader and standard error', () => {
    expect(copy.cutoffCaption(selection, forest)).toBe('cutoff 0.6633 = 0.6704 − 0.0071');
    expect(copy.cutoffCaption(selection, undefined)).toBe('cutoff 0.6633');
  });

  it('says by how much the leader leads, without calling the gap noise', () => {
    expect(copy.selectionNote(forest, logistic, 0.0052, name)).toBe(
      'random forest leads logistic regression by 0.0052, inside one standard error, so the simpler model is chosen.',
    );
    expect(copy.selectionNote(logistic, logistic, 0, name)).toBe(
      'logistic regression leads and is the simplest model inside the cutoff.',
    );
    expect(copy.selectionNote(undefined, logistic, null, name)).toBe(
      'The simplest model inside the cutoff is chosen.',
    );
    // The lead is the served figure; with none served, none is stated.
    expect(copy.selectionNote(forest, logistic, 0.1234, name)).toContain('by 0.1234,');
    expect(copy.selectionNote(forest, logistic, null, name)).toBe(
      'The simplest model inside the cutoff is chosen.',
    );
  });
});

describe('figures in sentences', () => {
  it('come from the served counts', () => {
    expect(copy.readoutAt(0.4, 512, '0.578', '0.955', '0.720')).toBe(
      'At 0.40: 512 flagged · precision 0.578 · recall 0.955 · F1 0.720.',
    );
    expect(copy.recordAt(0.36, 263, 111, 213)).toBe(
      'At 0.36: 263 churners caught, 111 missed; 213 offers to stayers.',
    );
    expect(copy.churnersFlagged(222, 310)).toBe('222 of 310 churners flagged');
    // The flagged are told as their two cells, never summed into a third figure.
    expect(copy.flaggedOutcomes(222, 163)).toBe('of the flagged, 222 churned and 163 stayed');
    expect(copy.offerCost(50, 'USD')).toBe('$50 each if offered, assumed');
    expect(copy.shortSha('0123456789abcdef'.repeat(4))).toBe('01234567…cdef');
  });

  it('name the served and the explored cut in the chart’s label', () => {
    const points = curve().points;
    const at36 = points[36];
    expect(copy.prLabel(at36, at36, 310)).toBe(
      `Validation precision against recall across cuts that flag someone; the served cut 0.36 has precision ${at36?.precision.toFixed(3)} and recall ${at36?.recall.toFixed(3)}.`,
    );
    expect(copy.prLabel(points[36], points[50], 310)).toMatch(/; the explored cut 0\.50 has /);
    // A cut that flags nobody has no precision; the API's 0.0 is not read out as one.
    expect(copy.prLabel(points[36], points[100], 310)).toMatch(
      /the explored cut 1\.00 has precision — and recall 0\.000\.$/,
    );
  });
});

describe('the words', () => {
  const CERTAIN = /\b(saved?|saves|rescued?|prevented|optimal model|excellent|best-in-class)\b/i;
  const points = curve().points;
  const sentences = [
    copy.TITLE,
    copy.lede(5, 4225, 1409, false),
    copy.lede(5, 4225, 1409, true),
    ...Object.values(copy.ROLES),
    copy.UNAVAILABLE,
    copy.MISMATCH,
    copy.FAILED,
    copy.VALIDATION_NOTE,
    copy.TEST_NOTE,
    copy.REUSED_HOLDOUT,
    copy.ONE_CUT,
    copy.waffleSubtitle(1409, 0.36),
    copy.waffleLabel({ tp: 263, fn: 111, fp: 213, tn: 822 }, 0.36),
    copy.plainCaught(263, 374),
    copy.plainOffers(263, 213),
    copy.plainMissed(111, 0.36),
    copy.readoutAt(0.4, 512, '0.578', '0.955', '0.720'),
    copy.recordAt(0.36, 263, 111, 213),
    copy.segmentsTitle(0.36),
    copy.NOT_OPTIMAL,
    copy.PR_HINT,
    copy.prLabel(points[36], points[50], 310),
    copy.AT_HALF_CAVEAT,
    copy.ECE_NOTE,
    copy.CANDIDATES_NOTE,
    copy.selectionSubtitle(5),
    copy.selectionNote(forest, logistic, 0.0052, name),
    copy.segmentsSubtitle(0.36),
    copy.UNSCANNED,
    copy.GENDER,
    copy.TEST_SCOPE,
    copy.FOOTER,
    copy.calibrationSentence(calibration),
    copy.calibrationSentence({ ...calibration, method: null }),
    copy.withheld('the split differs'),
  ];

  it.each(sentences)('claim no cause, rescue or verdict: %s', (sentence) => {
    expect(sentence).not.toMatch(CAUSAL);
    expect(sentence).not.toMatch(CERTAIN);
  });

  it('state no ratio, lift or "N in M" the record does not publish', () => {
    for (const sentence of sentences) {
      expect(sentence).not.toMatch(/\b\d+ in \d+\b|×|times (better|more)|\blift\b/i);
    }
  });

  it('never call a re-used holdout independent, or the cut optimal, without saying not', () => {
    for (const sentence of sentences) {
      if (/independent/i.test(sentence)) expect(sentence).toMatch(/not an independent/);
      if (/optim/i.test(sentence))
        expect(sentence).toMatch(/not a universal optimum|optimistic|test-optimal/);
    }
  });
});

describe('the test record in plain words', () => {
  it('writes each card from the record’s own cells', () => {
    expect(copy.plainCaught(263, 374)).toBe(
      'Of the 374 test customers who churned, 263 were flagged.',
    );
    expect(copy.plainOffers(263, 213)).toBe(
      'Among the customers flagged, 263 churned and 213 stayed.',
    );
    expect(copy.plainMissed(111, 0.36)).toBe(
      '111 test customers who churned scored below the cut 0.36 and were not flagged.',
    );
    expect(copy.waffleLabel({ tp: 263, fn: 111, fp: 213, tn: 822 }, 0.36)).toBe(
      'Test customers at the cut 0.36, one square each: 263 caught, 111 missed, 213 offers to stayers, 822 left alone.',
    );
  });
});

describe('the calibration chart’s subtitle', () => {
  const bins = (edges: number[]) =>
    edges.slice(1).map((high, i): copy.ReliabilityBin => ({ low: edges[i] ?? 0, high }));

  it('says tenths only where the bins are tenths', () => {
    expect(copy.calibrationSubtitle(bins([0, 0.1, 0.2, 0.30000000000000004, 0.4]))).toContain(
      'in tenths of probability',
    );
    const uneven = copy.calibrationSubtitle(bins([0, 0.24, 0.28, 0.35000000000000003, 0.5, 1]));
    expect(uneven).not.toContain('tenths');
    expect(uneven).toContain('between the cuts the sweep is published at');
  });

  it('claims no binning when there are no bins', () => {
    expect(copy.calibrationSubtitle(null)).not.toMatch(/tenths|cuts/);
  });
});
