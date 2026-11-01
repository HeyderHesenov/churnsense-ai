import { curve, performance } from '../../test/performanceApi';
import * as copy from './copy';

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
      'Chosen by 5-fold cross-validation on 4,225 training customers; the cut, and whether to keep calibration, were decided on 1,409 validation customers. No test evaluation is recorded for this model, so no figure here is a test result.',
    );
    expect(copy.lede(null, 4225, 1409, false)).toMatch(/^Chosen by cross-validation on 4,225/);
    expect(copy.lede(5, 4225, 1409, true)).toMatch(
      /recorded final evaluation, read on a re-used holdout\.$/,
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
    expect(copy.selectionNote(forest, logistic, name)).toBe(
      'random forest leads logistic regression by 0.0052, inside one standard error, so the simpler model is chosen.',
    );
    expect(copy.selectionNote(logistic, logistic, name)).toBe(
      'logistic regression leads and is the simplest model inside the cutoff.',
    );
    expect(copy.selectionNote(undefined, logistic, name)).toBe(
      'The simplest model inside the cutoff is chosen.',
    );
  });
});

describe('figures in sentences', () => {
  it('come from the served counts', () => {
    expect(copy.errorsAt(0.36, 512, 216, 14)).toBe(
      'At 0.36: 512 flagged; 216 offers to customers who stayed, 14 churners not flagged.',
    );
    expect(copy.churnersFlagged(222, 310)).toBe('222 of 310 churners flagged');
    expect(copy.flaggedChurned(222, 385)).toBe('222 of 385 flagged churned');
    expect(copy.offerCost(50, 'USD')).toBe('$50 offer each, assumed');
    expect(copy.testOptimal(0.29)).toMatch(/^The test-optimal cut \(0\.29\) is reported/);
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
  const CAUSAL =
    /\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents|guarantees?|guaranteed)\b/i;
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
    copy.SCORE_NOTE,
    copy.NOT_OPTIMAL,
    copy.PR_HINT,
    copy.prLabel(points[36], points[50], 310),
    copy.AT_HALF_CAVEAT,
    copy.ECE_NOTE,
    copy.CANDIDATES_NOTE,
    copy.selectionSubtitle(5),
    copy.selectionNote(forest, logistic, name),
    copy.segmentsSubtitle(0.36),
    copy.UNSCANNED,
    copy.GENDER,
    copy.TEST_SEGMENTS,
    copy.FOOTER,
    copy.calibrationSentence(calibration),
    copy.calibrationSentence({ ...calibration, method: null }),
    copy.testOptimal(0.29),
    copy.withheld('the split differs'),
  ];

  it.each(sentences)('claim no cause, rescue or verdict: %s', (sentence) => {
    expect(sentence).not.toMatch(CAUSAL);
    expect(sentence).not.toMatch(CERTAIN);
  });

  it('never call a re-used holdout independent, or the cut optimal, without saying not', () => {
    for (const sentence of sentences) {
      if (/independent/i.test(sentence)) expect(sentence).toMatch(/not an independent/);
      if (/optim/i.test(sentence))
        expect(sentence).toMatch(/not a universal optimum|optimistic|test-optimal/);
    }
  });
});
