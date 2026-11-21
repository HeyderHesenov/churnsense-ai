/**
 * Does one cut serve every segment? Validation customers at the served cut,
 * with 95 % intervals. A segment of fewer than 20 customers shows no outcome; a
 * segment nobody is flagged in though some churned is marked NOT SCANNED;
 * precision where nothing is flagged is "—", undefined rather than zero. The test
 * partition has no such table: its customers are listed, so no group's figure is
 * published (`evaluation.disclosure`).
 */
import type { Performance } from '../../api/queries';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut, int, pct } from '../../lib/format';
import { GENDER, segmentsSubtitle, segmentsTitle, SMALL, UNSCANNED, unavailableText } from './copy';
import {
  dimensionName,
  groups,
  recallOf,
  score,
  segmentName,
  type SegmentRow,
} from './performance';
import styles from './Performance.module.css';
import { useSweepNote } from '../../components/ui/DataSourceNote';

/** A served rate on a small 0-to-1 bar; the cell's text says the figure. */
function RateBar({ value, kind }: { value: number; kind: 'precision' | 'recall' }) {
  return (
    <svg
      className={styles.rateBar}
      data-rate={kind}
      viewBox="0 0 100 5"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <rect className={styles.rateTrack} x={0} y={0} width={100} height={5} rx={2.5} />
      <rect className={styles.rateFill} x={0} y={0} width={value * 100} height={5} rx={2.5} />
    </svg>
  );
}

/** A rate with its interval, when the row carries one. */
function withInterval(value: string, low?: number | null, high?: number | null): string {
  return low == null || high == null ? value : `${value} (${score(low)}–${score(high)})`;
}

function Row({ row }: { row: SegmentRow }) {
  const name = (
    <th scope="row">
      {segmentName(row.dimension, row.segment)}
      {row.unscanned === true && (
        <>
          {' '}
          <span className={styles.unscanned}>NOT SCANNED</span>
        </>
      )}
    </th>
  );
  // The server nulls every outcome of a segment it may not report.
  if (row.churners === null) {
    return (
      <tr className={styles.smallRow}>
        {name}
        <td>{int(row.customers)}</td>
        <td colSpan={2}>{SMALL}</td>
        <td>{int(row.flagged)}</td>
        <td>—</td>
        <td>—</td>
        <td>—</td>
      </tr>
    );
  }
  const precision =
    row.flagged === 0 || row.precision === null
      ? '—'
      : withInterval(score(row.precision), row.precision_low, row.precision_high);
  const recall =
    row.recall === null
      ? '—'
      : row.churners === 0
        ? recallOf(0, row.recall)
        : withInterval(score(row.recall), row.recall_low, row.recall_high);
  return (
    <tr className={row.unscanned ? styles.unscannedRow : undefined}>
      {name}
      <td>{int(row.customers)}</td>
      <td>{int(row.churners)}</td>
      <td>{row.churn_rate === null ? '—' : pct(row.churn_rate)}</td>
      <td>{int(row.flagged)}</td>
      <td>
        <span className={styles.rateCell}>
          {precision}
          {row.flagged > 0 && row.precision !== null && (
            <RateBar value={row.precision} kind="precision" />
          )}
        </span>
      </td>
      <td>
        <span className={styles.rateCell}>
          {recall}
          {row.recall !== null && row.churners > 0 && <RateBar value={row.recall} kind="recall" />}
        </span>
      </td>
      <td>{row.roc_auc == null ? '—' : score(row.roc_auc)}</td>
    </tr>
  );
}

export function Segments({ figures }: { figures: Performance }) {
  const sweepNote = useSweepNote();
  const rows: readonly SegmentRow[] | null = figures.validation_evaluation?.segments ?? null;
  const threshold = figures.validation_evaluation?.metrics.threshold ?? figures.threshold;

  return (
    <Card titleId="segments-title">
      <CardHeader
        id="segments-title"
        title={segmentsTitle(threshold)}
        subtitle={segmentsSubtitle(threshold)}
        aside={<Tag>VALIDATION</Tag>}
      />
      {rows === null ? (
        <p className={styles.unavailable}>{unavailableText(figures.withheld_reason, sweepNote)}</p>
      ) : (
        <>
          {/* A region that scrolls sideways must take the keyboard's focus, or its
              hidden columns are out of reach (axe scrollable-region-focusable):
              nothing inside it is focusable. */}
          {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex */}
          <div role="region" aria-label="Segment table" tabIndex={0} className={styles.scroll}>
            <table className={styles.segmentTable}>
              <caption className="sr">Segments at {cut(threshold)}, validation</caption>
              <thead>
                <tr>
                  <th scope="col">Segment</th>
                  <th scope="col">Customers</th>
                  <th scope="col">Churners</th>
                  <th scope="col">Churn rate</th>
                  <th scope="col">Flagged</th>
                  <th scope="col">Precision (95% CI)</th>
                  <th scope="col">Recall (95% CI)</th>
                  <th scope="col">ROC-AUC within</th>
                </tr>
              </thead>
              {groups(rows).map((group) => (
                <tbody key={group.dimension}>
                  <tr className={styles.groupRow}>
                    <th scope="rowgroup" colSpan={8}>
                      {dimensionName(group.dimension)}
                    </th>
                  </tr>
                  {group.rows.map((row) => (
                    <Row key={row.segment} row={row} />
                  ))}
                </tbody>
              ))}
            </table>
          </div>
          {rows.some((row) => row.unscanned) && <p className={styles.note}>{UNSCANNED}</p>}
          {rows.some((row) => row.dimension === 'gender') && (
            <p className={styles.note}>{GENDER}</p>
          )}
        </>
      )}
    </Card>
  );
}
