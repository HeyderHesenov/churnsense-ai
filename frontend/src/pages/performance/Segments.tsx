/**
 * Does one cut serve every segment? Validation customers at the served cut,
 * with 95 % intervals, and the recorded test breakdown beside it when there
 * is one. A segment of fewer than 20 customers shows no outcome; a segment
 * nobody is flagged in though some churned is marked NOT SCANNED; precision
 * where nothing is flagged is "—", undefined rather than zero.
 */
import { useState } from 'react';
import type { Performance } from '../../api/queries';
import { Card, CardHeader } from '../../components/ui/Surface';
import { Segmented } from '../../components/ui/Toggle';
import { cut, int, pct } from '../../lib/format';
import { cssVars } from '../../lib/style';
import {
  GENDER,
  segmentsSubtitle,
  SMALL,
  TEST_SEGMENTS,
  UNAVAILABLE,
  UNSCANNED,
  withheld,
} from './copy';
import {
  dimensionName,
  groups,
  recallOf,
  score,
  segmentName,
  type SegmentRow,
} from './performance';
import styles from './Performance.module.css';

type View = 'validation' | 'test';

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
  // The server nulls every outcome of a segment too small to report.
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
      <td>{precision}</td>
      <td>
        <span className={styles.recallCell}>
          {recall}
          {row.recall !== null && row.churners > 0 && (
            <span className={styles.recallBar} aria-hidden="true">
              <span style={cssVars({ '--w': row.recall })} />
            </span>
          )}
        </span>
      </td>
      <td>{row.roc_auc == null ? '—' : score(row.roc_auc)}</td>
    </tr>
  );
}

export function Segments({ figures }: { figures: Performance }) {
  const [view, setView] = useState<View>('validation');
  const test = figures.test;
  const shown: View = test ? view : 'validation';
  const rows: readonly SegmentRow[] | null =
    shown === 'test' && test ? test.segments : (figures.validation_evaluation?.segments ?? null);
  const threshold =
    shown === 'test' && test
      ? test.metrics.threshold
      : (figures.validation_evaluation?.metrics.threshold ?? figures.threshold);

  return (
    <Card titleId="segments-title">
      <CardHeader
        id="segments-title"
        title="Does one cut serve every segment?"
        subtitle={segmentsSubtitle(threshold)}
        aside={
          test && (
            <Segmented
              label="Partition for the segments"
              small
              options={[
                { value: 'validation', label: 'Validation' },
                { value: 'test', label: 'Test · recorded' },
              ]}
              value={shown}
              onChange={setView}
            />
          )
        }
      />
      {rows === null ? (
        <p className={styles.unavailable}>
          {figures.withheld_reason ? withheld(figures.withheld_reason) : UNAVAILABLE}
        </p>
      ) : (
        <>
          {/* A region that scrolls sideways must take the keyboard's focus, or its
              hidden columns are out of reach (axe scrollable-region-focusable):
              nothing inside it is focusable. */}
          {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex */}
          <div role="region" aria-label="Segment table" tabIndex={0} className={styles.scroll}>
            <table className={styles.segmentTable}>
              <caption className="sr">
                Segments at {cut(threshold)}, {shown === 'test' ? 'recorded test' : 'validation'}
              </caption>
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
          {shown === 'test' && <p className={styles.note}>{TEST_SEGMENTS}</p>}
        </>
      )}
    </Card>
  );
}
