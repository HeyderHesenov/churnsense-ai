/**
 * The batch page's state: where the chosen file stands, from choosing it to
 * the server's answer, and how the scored rows are arranged. Every figure is
 * the API's; nothing here validates or scores a row.
 *
 * Each choice of a file (the demo included) has an id. A read or an answer
 * that arrives for an earlier choice is dropped, so a slow file never takes
 * the place of the one chosen after it.
 */
import { ApiError } from '../../api/client';
import type { BatchScore, Direction } from '../../api/queries';
import type { Sort } from '../../components/ui/SortHeader';

export type BatchRow = BatchScore['rows'][number];

interface Chosen {
  name: string;
  demo: boolean;
}

/** Chosen and read: ``file`` is a copy of its bytes, so a retry sends exactly these. */
interface Loaded extends Chosen {
  file: File;
}

export type Stage =
  | { kind: 'idle' }
  | { kind: 'several'; count: number }
  | (Chosen & { kind: 'reading' | 'unreadable' })
  /** ``sent``: the server refused it (413); otherwise it was over the limit and never sent. */
  | (Chosen & { kind: 'tooLarge'; size: number; sent: boolean })
  | (Loaded & { kind: 'staged' | 'pending' | 'unavailable' })
  | (Loaded & { kind: 'accepted'; result: BatchScore; at: number })
  | (Loaded & { kind: 'refused'; problems: readonly string[]; omitted: number })
  | (Loaded & { kind: 'rateLimited'; wait: number | null; ready: boolean })
  /** ``status`` 0: no answer; ``null``: an answer that could not be read. */
  | (Loaded & { kind: 'failed'; status: number | null })
  /** Any other 4xx but 408 (403, 404…): the request itself is refused, whatever the file. */
  | (Loaded & { kind: 'denied'; status: number });

export interface BatchState {
  /** The current choice; actions for any other are dropped. */
  id: number;
  stage: Stage;
}

export type BatchAction =
  /** A new choice (a file, or the demo while it loads): whatever came before is dropped. */
  | { type: 'begin'; id: number; name: string; demo: boolean }
  | { type: 'several'; id: number; count: number }
  | { type: 'remove'; id: number }
  | { type: 'tooLarge'; id: number; size: number }
  | { type: 'read'; id: number; file: File }
  | { type: 'unreadable'; id: number }
  | { type: 'submit'; id: number }
  | { type: 'scored'; id: number; result: BatchScore; at: number }
  | { type: 'failed'; id: number; error: unknown }
  | { type: 'waited'; id: number };

export const INITIAL: BatchState = { id: 0, stage: { kind: 'idle' } };

/** Whether the file can be sent: once staged, and again after a failure that was not the file's. */
export function canScore(stage: Stage): stage is Loaded & Stage {
  return (
    stage.kind === 'staged' ||
    stage.kind === 'unavailable' ||
    stage.kind === 'failed' ||
    (stage.kind === 'rateLimited' && stage.ready)
  );
}

/** Where ``file`` stands after its request failed with ``error``. */
function afterFailure({ name, demo, file }: Loaded, error: unknown): Stage {
  const loaded = { name, demo, file };
  if (!(error instanceof ApiError)) return { ...loaded, kind: 'failed', status: null };
  switch (error.status) {
    case 422:
      return {
        ...loaded,
        kind: 'refused',
        problems: error.problems,
        omitted: error.problemsOmitted,
      };
    case 413:
      return { name, demo, kind: 'tooLarge', size: file.size, sent: true };
    case 429:
      return {
        ...loaded,
        kind: 'rateLimited',
        wait: error.retryAfter,
        ready: error.retryAfter === null,
      };
    case 503:
      return { ...loaded, kind: 'unavailable' };
    default:
      // 408 is a 4xx that asks to be sent again.
      return error.status >= 400 && error.status < 500 && error.status !== 408
        ? { ...loaded, kind: 'denied', status: error.status }
        : { ...loaded, kind: 'failed', status: error.status };
  }
}

type Step = Exclude<BatchAction, { type: 'begin' | 'several' | 'remove' }>;

function advance(stage: Stage, action: Step): Stage {
  switch (action.type) {
    case 'tooLarge':
      return stage.kind === 'reading'
        ? { name: stage.name, demo: stage.demo, kind: 'tooLarge', size: action.size, sent: false }
        : stage;
    case 'read':
      return stage.kind === 'reading'
        ? { name: stage.name, demo: stage.demo, kind: 'staged', file: action.file }
        : stage;
    case 'unreadable':
      return stage.kind === 'reading'
        ? { name: stage.name, demo: stage.demo, kind: 'unreadable' }
        : stage;
    case 'submit':
      return canScore(stage)
        ? { name: stage.name, demo: stage.demo, file: stage.file, kind: 'pending' }
        : stage;
    case 'scored':
      return stage.kind === 'pending'
        ? { ...stage, kind: 'accepted', result: action.result, at: action.at }
        : stage;
    case 'failed':
      return stage.kind === 'pending' ? afterFailure(stage, action.error) : stage;
    case 'waited':
      return stage.kind === 'rateLimited' ? { ...stage, ready: true } : stage;
  }
}

export function reducer(state: BatchState, action: BatchAction): BatchState {
  switch (action.type) {
    case 'begin':
      return { id: action.id, stage: { kind: 'reading', name: action.name, demo: action.demo } };
    case 'several':
      return { id: action.id, stage: { kind: 'several', count: action.count } };
    case 'remove':
      return { id: action.id, stage: { kind: 'idle' } };
  }
  if (action.id !== state.id) return state;
  const stage = advance(state.stage, action);
  return stage === state.stage ? state : { id: state.id, stage };
}

// --- the scored rows -----------------------------------------------------------------

export type Show = 'all' | 'flagged';
export type RowSortKey = 'row' | 'customer' | 'probability' | 'monthly';
export type RowSort = Sort<RowSortKey>;

export const DEFAULT_DIRECTION: Record<RowSortKey, Direction> = {
  row: 'asc',
  customer: 'asc',
  probability: 'desc',
  monthly: 'desc',
};
/** The riskiest first: the file's order is one click away. */
export const DEFAULT_SORT: RowSort = { column: 'probability', dir: 'desc' };
export const PAGE_SIZES = [12, 25, 50] as const;

const KEY: Record<RowSortKey, (row: BatchRow) => number | string> = {
  row: (r) => r.row,
  customer: (r) => r.customer_id ?? '',
  probability: (r) => r.churn_probability,
  monthly: (r) => Number(r.features.MonthlyCharges),
};

/** The rows ``show`` keeps, in ``sort`` order; ties keep the file's order whichever way it runs. */
export function arrange(rows: readonly BatchRow[], show: Show, sort: RowSort): BatchRow[] {
  const key = KEY[sort.column];
  const sign = sort.dir === 'asc' ? 1 : -1;
  return rows
    .filter((r) => show === 'all' || r.flagged)
    .sort((a, b) => {
      const [x, y] = [key(a), key(b)];
      return (x < y ? -sign : x > y ? sign : 0) || a.row - b.row;
    });
}
