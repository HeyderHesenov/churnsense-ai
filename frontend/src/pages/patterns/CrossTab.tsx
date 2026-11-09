/**
 * The breakdown as a table: one row per level of the row dimension, one
 * column per level of the column dimension (or one column of the measure),
 * each margin in its header. Every cell is a button: pointing or focusing it
 * describes it above the table, pressing it pins it, Escape lets go.
 */
import { useId } from 'react';
import type { Cell, Segments } from '../../api/queries';
import { int, pct } from '../../lib/format';
import { level } from '../../lib/labels';
import { cssVars, cx } from '../../lib/style';
import { cellText, focusText, hiddenMark, legendText, measureName, viewName } from './copy';
import { grid, sameGroup, scaleOf, shadeOf, type Measure, type Scale, type View } from './patterns';
import styles from './Patterns.module.css';

interface Props {
  view: View;
  data: Segments;
  measure: Measure;
  pinned: Cell | null;
  /** The table belongs to the previous view while the next one loads. */
  stale: boolean;
  onActive: (cell: Cell | null) => void;
  onToggle: (cell: Cell) => void;
  /** Escape lets go of the pin; none while nothing is pinned. */
  onUnpin: (() => void) | undefined;
}

export function CrossTab({
  view,
  data,
  measure,
  pinned,
  stale,
  onActive,
  onToggle,
  onUnpin,
}: Props) {
  const g = grid(data);
  const scale = scaleOf(data, measure);
  const columns = g.columns;
  const margin = (cell: Cell) =>
    cell.suppressed ? hiddenMark(cell, data.min_customers) : pct(cell.rate);
  const button = (cell: Cell) => (
    <CellButton
      key={`${cell.row}|${cell.column ?? ''}`}
      cell={cell}
      description={focusText(view, cell, data)}
      measure={measure}
      scale={scale}
      minimum={data.min_customers}
      pinned={pinned !== null && sameGroup(pinned, cell)}
      onActive={onActive}
      onToggle={onToggle}
      onUnpin={onUnpin}
    />
  );

  return (
    <div
      className={cx(styles.tableWrap, stale && styles.stale)}
      // ``inert`` while the next view loads: these cells are the previous view's.
      inert={stale}
    >
      {/* Wide breakdowns scroll sideways; every cell is a button, so the keyboard reaches all. */}
      <div className={styles.scroll}>
        <table className={styles.table}>
          <caption className="sr">
            {`${viewName(view)}: observed churn of the training partition, ${measureName(measure).toLowerCase()} shown`}
          </caption>
          <thead>
            <tr>
              <th scope="col" className={styles.corner}>
                <span>All</span>
                <span className={styles.margin}>
                  {data.base_rate === null ? '—' : pct(data.base_rate)}
                </span>
              </th>
              {columns === null ? (
                <th scope="col">{measureName(measure)}</th>
              ) : (
                data.column_totals.map((total) => (
                  <th key={total.row} scope="col">
                    <span>{level(view.cols ?? '', total.row)}</span>
                    <span className={styles.margin}>
                      {int(total.n)} · {margin(total)}
                    </span>
                  </th>
                ))
              )}
            </tr>
          </thead>
          <tbody>
            {data.row_totals.map((total) => (
              <tr key={total.row}>
                <th scope="row">
                  <span>{level(view.rows, total.row)}</span>
                  {columns !== null && (
                    <span className={styles.margin}>
                      {int(total.n)} · {margin(total)}
                    </span>
                  )}
                </th>
                {columns === null ? (
                  <td>{button(total)}</td>
                ) : (
                  columns.map((column) => {
                    const cell = g.at(total.row, column);
                    return <td key={column}>{cell && button(cell)}</td>;
                  })
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Legend scale={scale} />
    </div>
  );
}

interface CellProps {
  cell: Cell;
  /** The cell's whole sentence; its visible figures are its name (WCAG 2.5.3). */
  description: string;
  measure: Measure;
  scale: Scale;
  minimum: number;
  pinned: boolean;
  onActive: (cell: Cell | null) => void;
  onToggle: (cell: Cell) => void;
  /** Escape lets go of the pinned group, from any cell. */
  onUnpin: (() => void) | undefined;
}

function CellButton({
  cell,
  description,
  measure,
  scale,
  minimum,
  pinned,
  onActive,
  onToggle,
  onUnpin,
}: CellProps) {
  const shade = shadeOf(cell, scale);
  const descriptionId = useId();
  return (
    <button
      type="button"
      className={cx(styles.cell, cell.suppressed && measure !== 'customers' && styles.hidden)}
      aria-describedby={descriptionId}
      aria-pressed={pinned}
      data-measure={measure}
      data-side={shade !== null && shade < 0 ? 'below' : 'above'}
      style={cssVars({ '--t': shade === null ? 0 : Math.abs(shade) })}
      onPointerEnter={() => onActive(cell)}
      onPointerLeave={() => onActive(null)}
      onFocus={() => onActive(cell)}
      onBlur={() => onActive(null)}
      onClick={() => onToggle(cell)}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && onUnpin) onUnpin();
      }}
    >
      <span className={styles.value}>{cellText(cell, measure)}</span>
      <span className={styles.count}>
        {cell.suppressed && measure !== 'customers'
          ? hiddenMark(cell, minimum)
          : `n ${int(cell.n)}`}
      </span>
      <span id={descriptionId} hidden>
        {description}
      </span>
    </button>
  );
}

function Legend({ scale }: { scale: Scale }) {
  const [low, high, what] = legendText(scale);
  return (
    <div className={styles.legend} aria-hidden="true">
      <span>{low}</span>
      <span className={styles.ramp} data-measure={scale.measure} />
      <span>{high}</span>
      <span className={styles.legendWhat}>{what}</span>
      <span className={styles.hatchSwatch} />
      <span>churn hidden</span>
    </div>
  );
}
