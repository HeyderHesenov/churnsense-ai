/**
 * Every chart: a picture with one accessible name, the same figures as a
 * table for a screen reader, and an optional legend. A tooltip or HTML axis
 * labels go in ``overlay``: drawn over the picture, but outside it in the
 * accessibility tree, so a tooltip's live region is still announced.
 */
import type { ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './Chart.module.css';

export interface ChartTable {
  caption: string;
  columns: readonly string[];
  rows: readonly (readonly string[])[];
}

interface ChartFrameProps {
  /** What the picture shows, in a sentence. */
  label: string;
  table: ChartTable;
  /** The SVG (and any HTML drawn as part of it, such as axis labels). */
  children: ReactNode;
  overlay?: ReactNode;
  legend?: ReactNode;
  className?: string | undefined;
}

export function ChartFrame({
  label,
  table,
  children,
  overlay,
  legend,
  className,
}: ChartFrameProps) {
  return (
    <figure className={cx(styles.figure, className)}>
      <div className={styles.plot}>
        <div role="img" aria-label={label} className={styles.picture}>
          {children}
        </div>
        {overlay}
      </div>
      {/* A table ignores a 1px width, so the hiding box is a div around it. */}
      <div className="sr">
        <table>
          <caption>{table.caption}</caption>
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {legend && <figcaption className={styles.legend}>{legend}</figcaption>}
    </figure>
  );
}
