/**
 * The batch page's words. Every number in them is the API's or the file's
 * own size; the fixed sentences are the artboard's, made true of a page that
 * sends the file to the API (the artboard scored it in the browser).
 */
import type { BatchScore, ModelInfo } from '../../api/queries';
import { cut, int } from '../../lib/format';
import type { Show, Stage } from './batch';

type Summary = BatchScore['summary'];
type Contract = NonNullable<ModelInfo['upload_contract']>;
type Bounds = ModelInfo['numeric_bounds'];
type Limits = NonNullable<ModelInfo['limits']>;

export const LEDE =
  'Checked against the same contract as POST /predict/batch, then every row is scored by the served model. One refused value and nothing is scored; the file is not stored. The service has no authentication: never upload real or personally identifiable customer data.';

export const DEMO_NOTE = 'Synthetic customers from tests/fixtures, labels included.';

export const ROWS_NOTE =
  'Every row is scored, duplicates included, and keeps its 0-based position among the file’s data rows: a refusal’s data row 1 is row 0 here.';

/** "16.9 KB", "5 MB": a size as the limit is written. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${int(bytes)} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  const mb = bytes / 1_048_576;
  return `${Number.isInteger(mb) ? String(mb) : mb.toFixed(1)} MB`;
}

export function uploadLimits(limits: Limits): string {
  return `Up to ${int(limits.max_batch_rows)} rows, ${int(limits.max_upload_columns)} columns and ${fileSize(limits.max_upload_bytes)}. Columns outside the contract are ignored.`;
}

/** What a column may hold: its categories, or its range and whether it is whole. */
export function columnRule(column: string, contract: Contract, bounds: Bounds | undefined): string {
  const values = contract.categories[column];
  if (values) return values.join(' · ');
  const range = bounds?.[column as keyof NonNullable<Bounds>];
  if (!range) return 'number';
  const whole = contract.integer_columns.includes(column) ? ', whole' : '';
  return `${int(range.min)}–${int(range.max)}${whole}`;
}

export interface Step {
  label: string;
  mark: 'todo' | 'done' | 'refused';
  current: boolean;
}

/**
 * Choose, validate, score: a file that was read, or found over the served
 * limit, has been chosen; the API does the rest at once.
 */
export function steps(stage: Stage): Step[] {
  const chosen = 'file' in stage || stage.kind === 'tooLarge';
  const accepted = stage.kind === 'accepted';
  const refused = stage.kind === 'refused' || stage.kind === 'tooLarge';
  const current = accepted ? 2 : chosen ? 1 : 0;
  return [
    { label: 'Choose a file', mark: chosen ? 'done' : 'todo', current: current === 0 },
    {
      label: 'Validate against the contract',
      mark: accepted ? 'done' : refused ? 'refused' : 'todo',
      current: current === 1,
    },
    { label: 'Score', mark: accepted ? 'done' : 'todo', current: current === 2 },
  ];
}

export interface Served {
  model: string;
  threshold: number;
  maxBytes: number;
}

export interface Panel {
  /** The status the API answered with, or what stands for one. */
  code: string;
  tone: 'neutral' | 'ok' | 'bad';
  title: string;
  body: string;
}

const NOT_SENT = 'Not sent';

/** The validation panel for ``stage``: its title is also what the status line says. */
export function validation(stage: Stage, served: Served): Panel {
  const panel = (code: string, tone: Panel['tone'], title: string, body = '') => ({
    code,
    tone,
    title,
    body,
  });
  switch (stage.kind) {
    case 'idle':
      return panel(
        '—',
        'neutral',
        'Waiting for a file',
        'Choose a CSV or use the demo file. Every problem in its values is listed at once, so a file can be fixed in one pass.',
      );
    case 'several':
      return panel(
        NOT_SENT,
        'bad',
        `${int(stage.count)} files were dropped`,
        'Choose or drop one file at a time.',
      );
    case 'reading':
      return panel('…', 'neutral', `Reading ${stage.name}…`);
    case 'unreadable':
      return panel(
        NOT_SENT,
        'bad',
        stage.demo ? 'The demo file could not be loaded' : 'The file could not be read',
        stage.demo
          ? 'Try the demo file again, or choose a file.'
          : 'It may have changed or moved since it was chosen. Choose it again.',
      );
    case 'tooLarge':
      return panel(
        stage.sent ? '413' : NOT_SENT,
        'bad',
        `Too large: ${fileSize(stage.size)}`,
        `The limit is ${fileSize(served.maxBytes)}. ${stage.sent ? 'The API refused it' : 'Nothing was sent'}: split the file and score each part.`,
      );
    case 'staged':
      return panel(
        '—',
        'neutral',
        `Ready to score ${stage.name}`,
        `${served.model} at threshold ${cut(served.threshold)} scores every row, or none if any value is refused. The file is not stored.`,
      );
    case 'pending':
      return panel(
        '…',
        'neutral',
        `Scoring ${stage.name}…`,
        'Every value is checked, then every row is scored.',
      );
    case 'accepted':
      return panel(
        '200',
        'ok',
        `Accepted: ${int(stage.result.summary.rows)} rows, ${int(stage.result.columns)} columns`,
        'Every row passed the contract and was scored by the served model.',
      );
    case 'refused':
      return panel(
        '422',
        'bad',
        'Refused: nothing was scored',
        'Fix every item below and choose the file again.',
      );
    case 'rateLimited':
      // Once the wait is over the title changes, so the status line says so.
      if (stage.wait === null) {
        return panel('429', 'bad', 'Too many files in a short time', 'Try again in a moment.');
      }
      return stage.ready
        ? panel(
            '429',
            'neutral',
            `Ready to send ${stage.name} again`,
            `The ${int(stage.wait)} s wait the API asked for is over.`,
          )
        : panel(
            '429',
            'bad',
            'Too many files in a short time',
            `Try again in ${int(stage.wait)} s.`,
          );
    case 'unavailable':
      return panel(
        '503',
        'bad',
        'No model is being served',
        'Nothing can be scored until the API serves a model.',
      );
    case 'failed':
      return panel(
        stage.status === 0 ? 'No response' : stage.status === null ? 'Error' : String(stage.status),
        'bad',
        'The file could not be scored',
        stage.status === 0 ? 'The API did not answer.' : 'The API could not score it this time.',
      );
    case 'denied':
      return panel(
        String(stage.status),
        'bad',
        'The API refused the request',
        'It refused the request itself, not a value in the file: any file would get the same answer.',
      );
  }
}

/** The refusal's problems beyond those listed. */
export function moreProblems(omitted: number): string {
  return `and ${int(omitted)} more`;
}

/** The label check's tile, or ``null`` when the file had no labels to compare. */
export function labelTile({
  labels,
  labels_reason: notCompared,
}: Summary): { value: string; sub: string } | null {
  if (notCompared !== null) return { value: 'Not compared', sub: notCompared };
  if (labels === null) return null;
  const excluded = labels.excluded_test;
  const left = excluded
    ? `, ${int(excluded)} held-out test ${excluded === 1 ? 'customer' : 'customers'} left out`
    : '';
  const counted = `${int(labels.labelled)} labelled ${labels.labelled === 1 ? 'row' : 'rows'}${left}.`;
  // The API says why a rate is missing whenever one is.
  const said = labels.reason === null ? counted : `${counted} ${labels.reason}`;
  if (labels.precision === null && labels.recall === null) return { value: '—', sub: said };
  const rate = (value: number | null) => (value === null ? '—' : value.toFixed(3));
  return {
    value: `P ${rate(labels.precision)} · R ${rate(labels.recall)}`,
    sub: `${said} Not a performance estimate.`,
  };
}

/** "Showing 13–16 of 16 flagged": places in the list, as the explorer words them, not row numbers. */
export function pageNote(first: number, shown: number, total: number, show: Show): string {
  if (total === 0) return 'No rows';
  const which = show === 'flagged' ? ' flagged' : '';
  return `Showing ${int(first)}–${int(first + shown - 1)} of ${int(total)}${which}`;
}

/** "crm.csv · 16.9 KB · scored at 16:59:48", the time on this machine's clock. */
export function scoredMeta(name: string, size: number, at: number): string {
  const time = new Date(at).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  return `${name} · ${fileSize(size)} · scored at ${time}`;
}
