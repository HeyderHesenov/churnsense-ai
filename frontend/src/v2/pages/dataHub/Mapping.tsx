/**
 * The mapping the file was read with, and a form to change it: one choice of
 * field per column, and any status values to translate. The form only gathers
 * the reader's choices; the server decides whether they are a valid mapping
 * and says why not (``Refusal``).
 */
import { useId } from 'react';
import { Button } from '../../../components/ui/Button';
import { Card, CardHeader } from '../../../components/ui/Surface';
import type { AccountStatus, ColumnRef, ImportPreview } from '../../api';
import { STATUS_LABEL } from '../../copy';
import { CANONICAL_FIELDS, FIELD_LABELS, isRequired, STATUSES } from '../../fields';
import { Refusal } from '../../parts';
import styles from '../../V2.module.css';
import own from './DataHub.module.css';

/** The field chosen for each column, by the column's position; '' for none. */
export type Draft = Readonly<Record<number, string>>;

export interface StatusRow {
  key: number;
  source: string;
  target: AccountStatus;
}

/** The most translations the server accepts in one status mapping. */
export const MAX_STATUS_ROWS = 100;

/** The draft the server's own mapping gives: every applied column, its field. */
export function draftOf(preview: ImportPreview): Draft {
  return Object.fromEntries(preview.mapping.applied.map((m) => [m.column.position, m.field]));
}

/** A column whose name was withheld (it is itself an email address or a phone number). */
export function withheld(column: ColumnRef): boolean {
  return column.name === `[column ${String(column.position)}]`;
}

interface MappingProps {
  preview: ImportPreview;
  draft: Draft;
  onDraft: (draft: Draft) => void;
  statusRows: readonly StatusRow[];
  onStatusRows: (rows: StatusRow[]) => void;
  onCheck: () => void;
  busy: boolean;
  /** The last check's refusal, if it was refused. */
  refused: unknown;
}

export function Mapping({
  preview,
  draft,
  onDraft,
  statusRows,
  onStatusRows,
  onCheck,
  busy,
  refused,
}: MappingProps) {
  const { mapping, columns } = preview;
  const matchOf = new Map(mapping.applied.map((m) => [m.column.position, m.match]));
  const suggestionOf = new Map(mapping.suggestions.map((m) => [m.column.position, m.field]));
  const mappable = [...mapping.applied.map((m) => m.column), ...columns.ignored].sort(
    (a, b) => a.position - b.position,
  );
  const nextKey = statusRows.reduce((most, row) => Math.max(most, row.key), 0) + 1;

  return (
    <Card titleId="mapping-title">
      <CardHeader
        id="mapping-title"
        title="Column mapping"
        subtitle={
          mapping.source === 'explicit'
            ? 'Read with the mapping you sent: a column it leaves out is not read.'
            : 'Read with the server’s suggestions: exact and normalised name matches only.'
        }
      />
      {mapping.missing_required.length > 0 && (
        <p className={styles.body}>
          <strong>Required fields not mapped:</strong>{' '}
          {mapping.missing_required.map((f) => fieldName(f)).join(', ')}.
        </p>
      )}
      {mapping.unresolved.length > 0 && (
        <ul aria-label="Unresolved fields" className={styles.list}>
          {mapping.unresolved.map((u) => (
            <li key={u.field}>
              <span className={styles.mono}>{u.field}</span>
              {u.reason === 'ambiguous'
                ? ` matches more than one column (${u.candidates.map((c) => columnName(c)).join(', ')}): choose one.`
                : ' matches no column: choose the column that holds it.'}
            </li>
          ))}
        </ul>
      )}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption>Each column of your file and the contract field it is read into.</caption>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Read into</th>
              <th scope="col">How it was matched</th>
            </tr>
          </thead>
          <tbody>
            {mappable.map((column) => (
              <ColumnRow
                key={column.position}
                column={column}
                value={draft[column.position] ?? ''}
                match={matchOf.get(column.position)}
                suggestion={suggestionOf.get(column.position)}
                onChange={(field) => onDraft({ ...draft, [column.position]: field })}
                disabled={busy}
              />
            ))}
          </tbody>
        </table>
      </div>
      {columns.dropped_personal.length > 0 && (
        <p className={styles.muted}>
          <strong>Never read:</strong>{' '}
          {columns.dropped_personal.map((c) => columnName(c)).join(', ')}. Their names say they hold
          personal identifiers, so the server drops them before mapping.
        </p>
      )}
      <StatusTranslations
        rows={statusRows}
        nextKey={nextKey}
        onRows={onStatusRows}
        disabled={busy}
      />
      {refused !== null && (
        <div role="alert" className={own.refused}>
          <p className={styles.strong}>The server refused this mapping.</p>
          <Refusal
            error={refused}
            fallback={<p>The file could not be checked. Try again in a moment.</p>}
          />
        </div>
      )}
      <div className={styles.row}>
        <Button variant="primary" onClick={onCheck} disabled={busy}>
          {busy ? 'Checking…' : 'Check the file with this mapping'}
        </Button>
        <span className={styles.muted}>
          Sends the same file again with your mapping. The server applies it as complete: a column
          set to “Not read” is ignored.
        </span>
      </div>
    </Card>
  );
}

function ColumnRow({
  column,
  value,
  match,
  suggestion,
  onChange,
  disabled,
}: {
  column: ColumnRef;
  value: string;
  match: string | undefined;
  suggestion: string | undefined;
  onChange: (field: string) => void;
  disabled: boolean;
}) {
  const id = useId();
  const hidden = withheld(column);
  return (
    <tr>
      <th scope="row" className={styles.strong}>
        <label htmlFor={id}>{columnName(column)}</label>
      </th>
      <td>
        <select
          id={id}
          className={`${styles.control} ${own.select}`}
          value={value}
          disabled={disabled || hidden}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Not read</option>
          {CANONICAL_FIELDS.map((field) => (
            <option key={field} value={field}>
              {fieldName(field)}
            </option>
          ))}
        </select>
        {hidden && (
          <span className={styles.cellNote}>
            Its name is itself contact data, so it is withheld and cannot be mapped.
          </span>
        )}
      </td>
      <td>
        {match ? (
          <span className={styles.muted}>{match}</span>
        ) : suggestion ? (
          <span className={styles.muted}>suggested: {suggestion}</span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>
    </tr>
  );
}

function StatusTranslations({
  rows,
  nextKey,
  onRows,
  disabled,
}: {
  rows: readonly StatusRow[];
  nextKey: number;
  onRows: (rows: StatusRow[]) => void;
  disabled: boolean;
}) {
  const update = (key: number, change: Partial<StatusRow>) =>
    onRows(rows.map((row) => (row.key === key ? { ...row, ...change } : row)));
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>
        Status translations: a status value of your file, read as a contract status
      </legend>
      <div className={styles.stack}>
        {rows.map((row, i) => (
          <div key={row.key} className={own.statusRow}>
            <label className={styles.label}>
              Value in your file {i + 1}
              <input
                className={styles.control}
                value={row.source}
                maxLength={256}
                disabled={disabled}
                onChange={(event) => update(row.key, { source: event.target.value })}
              />
            </label>
            <label className={styles.label}>
              Read as
              <select
                className={styles.control}
                value={row.target}
                disabled={disabled}
                onChange={(event) =>
                  update(row.key, { target: event.target.value as AccountStatus })
                }
              >
                {STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {STATUS_LABEL[status]} ({status})
                  </option>
                ))}
              </select>
            </label>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => onRows(rows.filter((r) => r.key !== row.key))}
              aria-label={`Remove status translation ${String(i + 1)}`}
            >
              Remove
            </Button>
          </div>
        ))}
        <div className={styles.row}>
          <Button
            size="sm"
            disabled={disabled || rows.length >= MAX_STATUS_ROWS}
            onClick={() => onRows([...rows, { key: nextKey, source: '', target: 'active' }])}
          >
            Add a status translation
          </Button>
          <span className={styles.muted}>
            Values already in the contract’s statuses, in any case, need no translation.
          </span>
        </div>
      </div>
    </fieldset>
  );
}

function fieldName(field: string): string {
  const label = (FIELD_LABELS as Record<string, string>)[field];
  if (!label) return field;
  return `${label} (${field})${isRequired(field) ? ' · required' : ''}`;
}

function columnName(column: ColumnRef): string {
  return `${column.name} (#${String(column.position)})`;
}
