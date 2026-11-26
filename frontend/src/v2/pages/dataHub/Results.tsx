/**
 * What the server said the file would import: its verdict, its counts, its
 * MRR per snapshot date and currency, every listed problem and warning, the
 * first accepted rows and its notes. Every figure is the response's own.
 */
import { Card, CardHeader, StatTile } from '../../../components/ui/Surface';
import type { ImportPreview, RowIssue } from '../../api';
import { PREVIEW_STATUS, STATUS_LABEL } from '../../copy';
import { amount, count, plural, shortDate } from '../../format';
import { Notes } from '../../parts';
import styles from '../../V2.module.css';
import own from './DataHub.module.css';

export function Verdict({ preview }: { preview: ImportPreview }) {
  const words = PREVIEW_STATUS[preview.status];
  return (
    <div className={own.status} data-status={preview.status} role="status">
      <span className={own.statusTitle}>{words.title}</span>
      <span className={styles.body}>{words.body}</span>
      <span className={styles.muted}>
        Nothing was imported or stored: this is a preview against contract{' '}
        <span className={styles.mono}>{preview.contract_version}</span>.
      </span>
    </div>
  );
}

export function Counts({ preview }: { preview: ImportPreview }) {
  const { summary } = preview;
  return (
    <div className={styles.tiles} aria-label="Row counts" role="group">
      <StatTile compact label="Rows read" value={count(summary.rows_processed)} />
      <StatTile compact label="Accepted" value={count(summary.accepted)} />
      <StatTile compact label="Rejected" value={count(summary.rejected)} />
      <StatTile compact label="Warnings" value={count(summary.warnings)} />
      <StatTile compact label="Blank rows skipped" value={count(summary.blank_rows)} />
    </div>
  );
}

export function MrrTotals({ preview }: { preview: ImportPreview }) {
  const { mrr_totals: totals, mrr_totals_omitted: omitted } = preview.summary;
  if (totals.length === 0) return null;
  return (
    <Card titleId="totals-title" variant="flush">
      <div className={styles.stack}>
        <div className={own.panelInset}>
          <CardHeader
            id="totals-title"
            title="MRR of the accepted rows"
            subtitle="Per snapshot date and currency, as the server added them. Two currencies are never added together."
          />
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Snapshot date</th>
                <th scope="col">Currency</th>
                <th scope="col" className={styles.num}>
                  Accounts
                </th>
                <th scope="col" className={styles.num}>
                  MRR
                </th>
              </tr>
            </thead>
            <tbody>
              {totals.map((total) => (
                <tr key={`${total.as_of_date}-${total.currency}`}>
                  <td>{shortDate(total.as_of_date)}</td>
                  <td className={styles.mono}>{total.currency}</td>
                  <td className={styles.num}>{count(total.accounts)}</td>
                  <td className={`${styles.num} ${styles.strong}`}>
                    {amount(total.mrr, total.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {omitted > 0 && (
          <p className={`${styles.muted} ${own.panelInset}`}>
            {plural(omitted, 'more total')} not listed.
          </p>
        )}
      </div>
    </Card>
  );
}

export function Issues({
  id,
  title,
  issues,
  omitted,
  empty,
}: {
  id: string;
  title: string;
  issues: readonly RowIssue[];
  omitted: number;
  empty: string;
}) {
  return (
    <Card titleId={id} variant="flush">
      <div className={styles.stack}>
        <div className={own.panelInset}>
          <CardHeader
            id={id}
            title={title}
            subtitle={issues.length === 0 ? empty : 'Row numbers count the header as row 1.'}
          />
        </div>
        {issues.length > 0 && (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col" className={styles.num}>
                    Row
                  </th>
                  <th scope="col">Field</th>
                  <th scope="col">Code</th>
                  <th scope="col">What the server found</th>
                </tr>
              </thead>
              <tbody>
                {issues.map((issue, i) => (
                  <tr key={`${String(issue.row)}-${issue.code}-${String(i)}`}>
                    <td className={styles.num}>{count(issue.row)}</td>
                    <td className={styles.mono}>{issue.field ?? 'whole row'}</td>
                    <td className={styles.mono}>{issue.code}</td>
                    <td>{issue.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {omitted > 0 && (
          <p className={`${styles.muted} ${own.panelInset}`}>
            {count(omitted)} more not listed: the server keeps the list short.
          </p>
        )}
      </div>
    </Card>
  );
}

export function PreviewRows({ preview }: { preview: ImportPreview }) {
  const rows = preview.preview;
  if (rows.length === 0) return null;
  return (
    <Card titleId="rows-title" variant="flush">
      <div className={styles.stack}>
        <div className={own.panelInset}>
          <CardHeader
            id="rows-title"
            title="First accepted rows"
            subtitle={`${plural(rows.length, 'row')} as the contract reads them, in file order.`}
          />
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col" className={styles.num}>
                  Row
                </th>
                <th scope="col">Account</th>
                <th scope="col">Snapshot</th>
                <th scope="col">Status</th>
                <th scope="col" className={styles.num}>
                  MRR
                </th>
                <th scope="col">Renewal</th>
                <th scope="col">Segment</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.row}>
                  <td className={styles.num}>{count(row.row)}</td>
                  <td>
                    <span className={styles.strong}>
                      {row.account_name ?? row.account_external_id}
                    </span>
                    {row.account_name && (
                      <span className={`${styles.cellNote} ${styles.mono}`}>
                        {row.account_external_id}
                      </span>
                    )}
                  </td>
                  <td>{shortDate(row.as_of_date)}</td>
                  <td>{STATUS_LABEL[row.status]}</td>
                  <td className={styles.num}>{amount(row.mrr, row.currency)}</td>
                  <td>{row.renewal_date ? shortDate(row.renewal_date) : '—'}</td>
                  <td>{row.segment ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

export function PreviewNotes({ preview }: { preview: ImportPreview }) {
  if (preview.notes.length === 0) return null;
  return (
    <Card titleId="notes-title">
      <CardHeader id="notes-title" title="Notes from the server" />
      <Notes notes={preview.notes} label="Notes from the server" />
    </Card>
  );
}
