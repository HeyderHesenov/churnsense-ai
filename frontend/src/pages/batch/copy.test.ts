import { ApiError } from '../../api/client';
import { batchScore, ROWS } from '../../test/batchApi';
import { modelInfo } from '../../test/fixtures';
import { reducer, INITIAL, type BatchAction, type Stage } from './batch';
import {
  columnRule,
  DEMO_NOTE,
  fileSize,
  LEDE,
  labelTile,
  pageNote,
  ROWS_NOTE,
  scoredMeta,
  steps,
  uploadLimits,
  validation,
} from './copy';
import { CAUSAL } from '../../test/wording';

const FILE = new File(['x'.repeat(2048)], 'crm.csv');
const SERVED = { model: 'Logistic Regression', threshold: 0.36, maxBytes: 5_242_880 };

function stageAfter(...actions: BatchAction[]): Stage {
  return actions.reduce(reducer, INITIAL).stage;
}
const begun: BatchAction = { type: 'begin', id: 1, name: 'crm.csv', demo: false };
const sent: BatchAction[] = [begun, { type: 'read', id: 1, file: FILE }, { type: 'submit', id: 1 }];
const failing = (error: unknown) => stageAfter(...sent, { type: 'failed', id: 1, error });
const summary = batchScore().summary;

describe('sizes and limits', () => {
  it('writes a file size the way the limit is written', () => {
    expect(fileSize(512)).toBe('512 B');
    expect(fileSize(17_303)).toBe('16.9 KB');
    expect(fileSize(5_242_880)).toBe('5 MB');
    expect(fileSize(6_029_312)).toBe('5.8 MB');
  });

  it('states the served limits', () => {
    const { limits } = modelInfo();
    if (!limits) throw new Error('the fixture serves its limits');
    expect(uploadLimits(limits)).toBe(
      'Up to 10,000 rows, 1,000 columns and 5 MB. Columns outside the contract are ignored.',
    );
  });

  it('describes each column by the served contract', () => {
    const { upload_contract: contract, numeric_bounds: bounds } = modelInfo();
    if (!contract) throw new Error('the fixture serves a contract');
    expect(columnRule('Contract', contract, bounds)).toBe('Month-to-month · One year · Two year');
    expect(columnRule('tenure', contract, bounds)).toBe('0–120, whole');
    expect(columnRule('TotalCharges', contract, bounds)).toBe('0–100,000');
  });
});

describe('the steps', () => {
  const marks = (stage: Stage) => steps(stage).map((s) => `${s.mark}${s.current ? '*' : ''}`);

  it('starts at choosing a file, and stays there for a file that could not be read', () => {
    expect(marks({ kind: 'idle' })).toEqual(['todo*', 'todo', 'todo']);
    expect(marks(stageAfter(begun, { type: 'unreadable', id: 1 }))).toEqual([
      'todo*',
      'todo',
      'todo',
    ]);
  });

  it('marks a file over the served limit as refused by the contract, sent or not', () => {
    expect(marks(stageAfter(begun, { type: 'tooLarge', id: 1, size: 1 }))).toEqual([
      'done',
      'refused*',
      'todo',
    ]);
    expect(marks(failing(new ApiError(413, 'payload_too_large')))).toEqual([
      'done',
      'refused*',
      'todo',
    ]);
  });

  it('moves to validation once a file is read, and marks a refusal there', () => {
    expect(marks(stageAfter(begun, { type: 'read', id: 1, file: FILE }))).toEqual([
      'done',
      'todo*',
      'todo',
    ]);
    expect(marks(failing(new ApiError(422, 'invalid_input')))).toEqual([
      'done',
      'refused*',
      'todo',
    ]);
  });

  it('ends with every step done', () => {
    expect(
      marks(stageAfter(...sent, { type: 'scored', id: 1, result: batchScore(), at: 0 })),
    ).toEqual(['done', 'done', 'done*']);
  });
});

describe('the validation panel', () => {
  const panel = (stage: Stage) => validation(stage, SERVED);

  it('says what will happen to a staged file before it is sent', () => {
    expect(panel(stageAfter(begun, { type: 'read', id: 1, file: FILE }))).toEqual({
      code: '—',
      tone: 'neutral',
      title: 'Ready to score crm.csv',
      body: 'Logistic Regression at threshold 0.36 scores every row, or none if any value is refused. The file is not stored.',
    });
  });

  it('names a file never sent as such, not as an answer of the API', () => {
    expect(panel(stageAfter(begun, { type: 'tooLarge', id: 1, size: 6_291_456 }))).toEqual({
      code: 'Not sent',
      tone: 'bad',
      title: 'Too large: 6 MB',
      body: 'The limit is 5 MB. Nothing was sent: split the file and score each part.',
    });
    expect(panel(stageAfter(begun, { type: 'unreadable', id: 1 }))).toEqual({
      code: 'Not sent',
      tone: 'bad',
      title: 'The file could not be read',
      body: 'It may have changed or moved since it was chosen. Choose it again.',
    });
    const demo: BatchAction = { type: 'begin', id: 1, name: 'demo_customers.csv', demo: true };
    expect(panel(stageAfter(demo, { type: 'unreadable', id: 1 }))).toMatchObject({
      title: 'The demo file could not be loaded',
      body: 'Try the demo file again, or choose a file.',
    });
    expect(panel({ kind: 'several', count: 2 })).toMatchObject({
      code: 'Not sent',
      title: '2 files were dropped',
    });
  });

  it('gives the status the API answered with', () => {
    expect(
      panel(stageAfter(...sent, { type: 'scored', id: 1, result: batchScore(), at: 0 })),
    ).toMatchObject({
      code: '200',
      tone: 'ok',
      title: 'Accepted: 30 rows, 21 columns',
    });
    expect(panel(failing(new ApiError(422, 'invalid_input')))).toMatchObject({
      code: '422',
      title: 'Refused: nothing was scored',
    });
    expect(panel(failing(new ApiError(413, 'payload_too_large')))).toMatchObject({
      code: '413',
      body: 'The limit is 5 MB. The API refused it: split the file and score each part.',
    });
    expect(panel(failing(new ApiError(503, 'model_unavailable'))).code).toBe('503');
    expect(panel(failing(new ApiError(502, 'http_error')))).toMatchObject({
      code: '502',
      body: 'The API could not score it this time.',
    });
    expect(panel(failing(new ApiError(0, 'network_error')))).toMatchObject({
      code: 'No response',
      body: 'The API did not answer.',
    });
    expect(panel(failing(new SyntaxError('not JSON'))).code).toBe('Error');
  });

  it('says a request the API refused would be refused again', () => {
    expect(panel(failing(new ApiError(403, 'forbidden')))).toEqual({
      code: '403',
      tone: 'bad',
      title: 'The API refused the request',
      body: 'It refused the request itself, not a value in the file: any file would get the same answer.',
    });
  });

  it('says when a rate-limited file can be sent again, so the status line speaks then', () => {
    const limited = failing(new ApiError(429, 'rate_limited', [], 6));
    expect(panel(limited)).toEqual({
      code: '429',
      tone: 'bad',
      title: 'Too many files in a short time',
      body: 'Try again in 6 s.',
    });
    expect(panel(reducer({ id: 1, stage: limited }, { type: 'waited', id: 1 }).stage)).toEqual({
      code: '429',
      tone: 'neutral',
      title: 'Ready to send crm.csv again',
      body: 'The 6 s wait the API asked for is over.',
    });
    expect(panel(failing(new ApiError(429, 'rate_limited')))).toMatchObject({
      title: 'Too many files in a short time',
      body: 'Try again in a moment.',
    });
  });
});

describe('the label check', () => {
  it('gives precision and recall on the file’s own labels, with what was left out', () => {
    expect(labelTile(summary)).toEqual({
      value: `P ${(summary.labels?.precision ?? 0).toFixed(3)} · R ${(summary.labels?.recall ?? 0).toFixed(3)}`,
      sub: '29 labelled rows, 1 held-out test customer left out. Not a performance estimate.',
    });
  });

  const checked = (labels: NonNullable<typeof summary.labels>) => labelTile({ ...summary, labels });

  it('gives no rate where the API gives none, and its reason', () => {
    expect(labelTile(batchScore(ROWS.slice(0, 10)).summary)).toEqual({
      value: '—',
      sub: '9 labelled rows, 1 held-out test customer left out. A rate needs at least 20 labelled rows.',
    });
    const one = { labelled: 1, excluded_test: 0, precision: null, recall: null };
    expect(checked({ ...one, reason: 'A rate needs at least 20 labelled rows.' })).toEqual({
      value: '—',
      sub: '1 labelled row. A rate needs at least 20 labelled rows.',
    });
  });

  it('gives the rate there is, and why the other is missing', () => {
    const reason = 'No labelled customer was flagged, so precision is not defined.';
    expect(checked({ labelled: 20, excluded_test: 0, precision: null, recall: 0, reason })).toEqual(
      {
        value: 'P — · R 0.000',
        sub: `20 labelled rows. ${reason} Not a performance estimate.`,
      },
    );
    expect(
      checked({ labelled: 20, excluded_test: 0, precision: 0.5, recall: 0.25, reason: null }),
    ).toEqual({ value: 'P 0.500 · R 0.250', sub: '20 labelled rows. Not a performance estimate.' });
  });

  it('says why labels were not compared, and nothing when the file had none', () => {
    const reason = 'The dataset is not available.';
    expect(labelTile({ ...summary, labels: null, labels_reason: reason })).toEqual({
      value: 'Not compared',
      sub: reason,
    });
    expect(labelTile({ ...summary, labels: null, labels_reason: null })).toBeNull();
  });
});

describe('the scored rows', () => {
  it('says which rows a page shows', () => {
    expect(pageNote(1, 12, 30, 'all')).toBe('Showing 1–12 of 30');
    expect(pageNote(13, 4, 16, 'flagged')).toBe('Showing 13–16 of 16 flagged');
    expect(pageNote(1, 0, 0, 'flagged')).toBe('No rows');
  });

  it('names the file, its size and when it was scored', () => {
    const at = new Date(2026, 9, 30, 16, 59, 48).getTime();
    expect(scoredMeta('crm.csv', 17_303, at)).toBe('crm.csv · 16.9 KB · scored at 16:59:48');
  });
});

describe('the words', () => {
  it('make no causal claim, and claim no scoring in the browser', () => {
    const kinds: Stage[] = [
      { kind: 'idle' },
      { kind: 'several', count: 2 },
      stageAfter(begun),
      stageAfter(begun, { type: 'unreadable', id: 1 }),
      stageAfter(...sent),
      failing(new ApiError(503, 'model_unavailable')),
      failing(new ApiError(500, 'internal_error')),
      failing(new ApiError(403, 'forbidden')),
      failing(new ApiError(429, 'rate_limited', [], 6)),
      reducer(
        { id: 1, stage: failing(new ApiError(429, 'rate_limited', [], 6)) },
        { type: 'waited', id: 1 },
      ).stage,
    ];
    const sentences = [
      LEDE,
      DEMO_NOTE,
      ROWS_NOTE,
      ...kinds.flatMap((stage) => {
        const { code, title, body } = validation(stage, SERVED);
        return [code, title, body];
      }),
    ];
    for (const text of sentences) {
      expect(text).not.toMatch(CAUSAL);
      expect(text).not.toMatch(/in your browser|nothing is uploaded|nothing leaves/i);
    }
  });
});
