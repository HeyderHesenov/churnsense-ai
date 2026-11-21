/**
 * Three cuts of one response, side by side: the served model's cut (the
 * baseline), the recommendation under the assumptions as named, and the cut
 * selected. Each figure is that point's own field; nothing is subtracted, so
 * no difference is printed that the API did not send.
 */
import type { ReactNode } from 'react';
import type { Sweep, SweepPoint } from '../../api/queries';
import { Card, CardHeader, SimulatedBadge } from '../../components/ui/Surface';
import { cut, int, money, signedMoney } from '../../lib/format';
import { cx } from '../../lib/style';
import { f1Text, NOT_A_POINT, precisionText, recallText, SIDE_BY_SIDE } from './copy';
import { ForAssumptions } from './ForAssumptions';
import { pointIndex, type Selection } from './simulator';
import styles from './Simulator.module.css';

interface Column {
  name: string;
  threshold: number;
  point: SweepPoint | undefined;
}

interface ThreeCutsProps {
  sweep: Sweep;
  selected: Selection;
  stale: boolean;
}

export function ThreeCuts({ sweep, selected, stale }: ThreeCutsProps) {
  const currency = sweep.assumptions.currency;
  const points = sweep.points;
  const columns: Column[] = [
    {
      name: 'Served',
      threshold: sweep.shipped_threshold,
      point: points[pointIndex(points, sweep.shipped_threshold)],
    },
    {
      name: 'Recommended',
      threshold: sweep.recommended.threshold,
      point: points[selected.recommended],
    },
    {
      name: 'Selected',
      threshold: points[selected.index]?.threshold ?? NaN,
      point: points[selected.index],
    },
  ];
  const rows: { term: string; value: (p: SweepPoint) => ReactNode }[] = [
    { term: 'Flagged', value: (p) => int(p.flagged) },
    { term: 'Churners flagged', value: (p) => int(p.true_positives) },
    { term: 'Precision', value: (p) => precisionText(p) },
    { term: 'Recall', value: (p) => recallText(p, sweep.churners) },
    { term: 'F1', value: (p) => f1Text(p, sweep.churners) },
    { term: 'Retained value', value: (p) => money(p.retained_value, currency) },
    { term: 'Campaign cost', value: (p) => money(p.intervention_cost, currency) },
    { term: 'Net benefit', value: (p) => signedMoney(p.net_benefit, currency) },
  ];
  return (
    <Card titleId="three-cuts-title" className={cx(styles.panel, stale && styles.stale)}>
      <CardHeader
        id="three-cuts-title"
        title="Served, recommended and selected"
        subtitle={SIDE_BY_SIDE}
        aside={<SimulatedBadge />}
      />
      <ForAssumptions echo={sweep.assumptions} stale={stale} />
      {/* A table that scrolls sideways must take the keyboard's focus, or its hidden
          columns are out of reach (axe scrollable-region-focusable): nothing inside is focusable. */}
      {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex */}
      <div role="region" aria-label="Three cuts table" tabIndex={0} className={styles.tableWrap}>
        <table className={styles.threeCuts}>
          <caption className="sr">The response at three cuts</caption>
          <thead>
            <tr>
              <td />
              {columns.map((column) => (
                <th key={column.name} scope="col">
                  <span className={styles.columnName}>{column.name}</span>
                  <span className={styles.columnCut}>
                    {Number.isFinite(column.threshold) ? cut(column.threshold) : '—'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.term}>
                <th scope="row">{row.term}</th>
                {columns.map((column) => (
                  <td key={column.name}>
                    {column.point ? (
                      row.value(column.point)
                    ) : (
                      <span className={styles.notPoint}>{NOT_A_POINT}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.compareNote}>
        Counts, precision, recall and F1 are measured on {int(sweep.customers)} validation
        customers; the money is simulated from the assumptions as named.
      </p>
    </Card>
  );
}
