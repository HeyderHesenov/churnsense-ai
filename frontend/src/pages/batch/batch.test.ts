import { ApiError } from '../../api/client';
import { batchRow, batchScore } from '../../test/batchApi';
import {
  arrange,
  canScore,
  INITIAL,
  reducer,
  type BatchAction,
  type BatchState,
  type Stage,
} from './batch';

const FILE = new File(['customerID\n'], 'crm.csv', { type: 'text/csv' });

function run(...actions: BatchAction[]): BatchState {
  return actions.reduce(reducer, INITIAL);
}

/** File 1 chosen, read and sent. */
const PENDING: BatchAction[] = [
  { type: 'begin', id: 1, name: 'crm.csv', demo: false },
  { type: 'read', id: 1, file: FILE },
  { type: 'submit', id: 1 },
];

function failedWith(error: unknown): Stage {
  return run(...PENDING, { type: 'failed', id: 1, error }).stage;
}

describe('choosing a file', () => {
  it('reads it, then stages it', () => {
    expect(run({ type: 'begin', id: 1, name: 'crm.csv', demo: false }).stage).toEqual({
      kind: 'reading',
      name: 'crm.csv',
      demo: false,
    });
    expect(run(...PENDING.slice(0, 2)).stage).toEqual({
      kind: 'staged',
      name: 'crm.csv',
      demo: false,
      file: FILE,
    });
  });

  it('never stages a file whose read finished after another was chosen', () => {
    const state = run(
      { type: 'begin', id: 1, name: 'old.csv', demo: false },
      { type: 'begin', id: 2, name: 'new.csv', demo: false },
      { type: 'read', id: 1, file: FILE },
    );
    expect(state).toEqual({ id: 2, stage: { kind: 'reading', name: 'new.csv', demo: false } });
  });

  it('says a file over the limit was not sent, and one it could not read was not read', () => {
    const begun: BatchAction = { type: 'begin', id: 1, name: 'big.csv', demo: false };
    expect(run(begun, { type: 'tooLarge', id: 1, size: 6_291_456 }).stage).toEqual({
      kind: 'tooLarge',
      name: 'big.csv',
      demo: false,
      size: 6_291_456,
      sent: false,
    });
    expect(run(begun, { type: 'unreadable', id: 1 }).stage.kind).toBe('unreadable');
  });

  it('refuses several files dropped at once', () => {
    expect(run({ type: 'several', id: 1, count: 3 }).stage).toEqual({ kind: 'several', count: 3 });
  });

  it('drops the file and every answer still due once it is removed', () => {
    const state = run(
      ...PENDING,
      { type: 'remove', id: 2 },
      { type: 'scored', id: 1, result: batchScore(), at: 0 },
    );
    expect(state).toEqual({ id: 2, stage: { kind: 'idle' } });
  });
});

describe('scoring', () => {
  it('sends a staged file once: a second press while it is pending does nothing', () => {
    const pending = run(...PENDING);
    expect(pending.stage.kind).toBe('pending');
    expect(reducer(pending, { type: 'submit', id: 1 })).toBe(pending);
  });

  it('keeps the answer for the file it was asked for, never a superseded one', () => {
    const result = batchScore();
    expect(run(...PENDING, { type: 'scored', id: 1, result, at: 5 }).stage).toEqual({
      kind: 'accepted',
      name: 'crm.csv',
      demo: false,
      file: FILE,
      result,
      at: 5,
    });
    const superseded = run(
      ...PENDING,
      { type: 'begin', id: 2, name: 'next.csv', demo: false },
      { type: 'scored', id: 1, result, at: 5 },
    );
    expect(superseded.stage.kind).toBe('reading');
  });

  it('lists a refusal’s problems and how many were left out', () => {
    expect(failedWith(new ApiError(422, 'invalid_input', ['p1', 'p2'], null, 4))).toMatchObject({
      kind: 'refused',
      problems: ['p1', 'p2'],
      omitted: 4,
    });
  });

  it('reads a 413 as too large, sent', () => {
    expect(failedWith(new ApiError(413, 'payload_too_large'))).toEqual({
      kind: 'tooLarge',
      name: 'crm.csv',
      demo: false,
      size: FILE.size,
      sent: true,
    });
  });

  it('waits out a rate limit before it can send again', () => {
    const limited = run(...PENDING, {
      type: 'failed',
      id: 1,
      error: new ApiError(429, 'rate_limited', [], 6),
    });
    expect(limited.stage).toMatchObject({ kind: 'rateLimited', wait: 6, ready: false });
    expect(canScore(limited.stage)).toBe(false);
    const waited = reducer(limited, { type: 'waited', id: 1 });
    expect(canScore(waited.stage)).toBe(true);
    expect(failedWith(new ApiError(429, 'rate_limited'))).toMatchObject({ ready: true });
  });

  it.each([
    [new ApiError(503, 'model_unavailable'), { kind: 'unavailable' }],
    [new ApiError(500, 'internal_error'), { kind: 'failed', status: 500 }],
    [new ApiError(408, 'request_timeout'), { kind: 'failed', status: 408 }],
    [new ApiError(0, 'network_error'), { kind: 'failed', status: 0 }],
    [new SyntaxError('not JSON'), { kind: 'failed', status: null }],
  ])('can send the same file again after %s', (error, expected) => {
    const stage = failedWith(error);
    expect(stage).toMatchObject(expected);
    expect(canScore(stage)).toBe(true);
  });

  it.each([400, 403, 404])('cannot send the same file again after a %i', (status) => {
    const stage = failedWith(new ApiError(status, 'refused'));
    expect(stage).toMatchObject({ kind: 'denied', status, name: 'crm.csv' });
    expect(canScore(stage)).toBe(false);
  });

  it.each(['refused', 'tooLarge', 'accepted'] as const)(
    'cannot send the same file again once it is %s',
    (kind) => {
      const outcome: BatchAction =
        kind === 'accepted'
          ? { type: 'scored', id: 1, result: batchScore(), at: 0 }
          : {
              type: 'failed',
              id: 1,
              error: new ApiError(kind === 'refused' ? 422 : 413, 'refused'),
            };
      const state = run(...PENDING, outcome);
      expect(state.stage.kind).toBe(kind);
      expect(reducer(state, { type: 'submit', id: 1 })).toBe(state);
    },
  );
});

describe('a step for the wrong moment', () => {
  const reading = run(PENDING[0] as BatchAction);
  const staged = run(...PENDING.slice(0, 2));
  const pending = run(...PENDING);
  const limited = run(...PENDING, {
    type: 'failed',
    id: 1,
    error: new ApiError(429, 'rate_limited', [], 6),
  });
  const scored: BatchAction = { type: 'scored', id: 1, result: batchScore(), at: 0 };
  const failed: BatchAction = { type: 'failed', id: 1, error: new Error('late') };

  it.each<[string, BatchState, BatchAction]>([
    ['a size check', staged, { type: 'tooLarge', id: 1, size: 1 }],
    ['a read', staged, { type: 'read', id: 1, file: FILE }],
    ['an unreadable file', staged, { type: 'unreadable', id: 1 }],
    ['a submission', reading, { type: 'submit', id: 1 }],
    ['an answer', staged, scored],
    ['a failure', staged, failed],
    ['the end of a wait', pending, { type: 'waited', id: 1 }],
  ])('ignores %s in a stage it does not belong to', (_, state, action) => {
    expect(reducer(state, action)).toBe(state);
  });

  it.each<[string, BatchState, BatchAction]>([
    ['a size check', reading, { type: 'tooLarge', id: 0, size: 1 }],
    ['a read', reading, { type: 'read', id: 0, file: FILE }],
    ['an unreadable file', reading, { type: 'unreadable', id: 0 }],
    ['a submission', staged, { type: 'submit', id: 0 }],
    ['an answer', pending, { ...scored, id: 0 }],
    ['a failure', pending, { ...failed, id: 0 }],
    ['the end of a wait', limited, { type: 'waited', id: 0 }],
  ])('ignores %s for an earlier choice', (_, state, action) => {
    expect(reducer(state, action)).toBe(state);
  });
});

describe('arranging the scored rows', () => {
  const rows = [
    batchRow(0, { churn_probability: 0.4, customer_id: 'B', flagged: true }),
    batchRow(1, { churn_probability: 0.9, customer_id: null, flagged: true }),
    batchRow(2, { churn_probability: 0.4, customer_id: 'A', flagged: false }),
  ];
  const order = (arranged: readonly { row: number }[]) => arranged.map((r) => r.row);

  it('puts the riskiest first, ties in file order whichever way the column runs', () => {
    expect(order(arrange(rows, 'all', { column: 'probability', dir: 'desc' }))).toEqual([1, 0, 2]);
    expect(order(arrange(rows, 'all', { column: 'probability', dir: 'asc' }))).toEqual([0, 2, 1]);
  });

  it('breaks a tie by the row’s position, whatever order the rows came in', () => {
    const reversed = [...rows].reverse();
    expect(order(arrange(reversed, 'all', { column: 'probability', dir: 'desc' }))).toEqual([
      1, 0, 2,
    ]);
    expect(order(arrange(reversed, 'all', { column: 'probability', dir: 'asc' }))).toEqual([
      0, 2, 1,
    ]);
  });

  it('keeps only the flagged rows when asked', () => {
    expect(order(arrange(rows, 'flagged', { column: 'row', dir: 'asc' }))).toEqual([0, 1]);
  });

  it('sorts customers by ID, a row without one first', () => {
    expect(order(arrange(rows, 'all', { column: 'customer', dir: 'asc' }))).toEqual([1, 2, 0]);
  });

  it('sorts monthly charges as numbers, not as text', () => {
    const charged = [
      batchRow(0, { features: { MonthlyCharges: 10 } }),
      batchRow(1, { features: { MonthlyCharges: 9.5 } }),
    ];
    expect(order(arrange(charged, 'all', { column: 'monthly', dir: 'asc' }))).toEqual([1, 0]);
    const written = [
      batchRow(0, { features: { MonthlyCharges: '10' } }),
      batchRow(1, { features: { MonthlyCharges: '9.5' } }),
    ];
    expect(order(arrange(written, 'all', { column: 'monthly', dir: 'asc' }))).toEqual([1, 0]);
  });

  it('leaves the server’s rows as they came', () => {
    const before = order(rows);
    arrange(rows, 'all', { column: 'probability', dir: 'desc' });
    expect(order(rows)).toEqual(before);
  });
});
