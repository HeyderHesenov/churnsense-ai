/**
 * The light tooltip charts show on hover and keyboard focus. It is placed in
 * fractions of the chart's box, so it tracks the chart as the chart scales;
 * past ``flip`` it hangs to the left of the point so it never leaves the box.
 * The live region stays in place and is empty while hidden, so each new
 * point is announced.
 */
import type { ReactNode } from 'react';
import { cssVars, cx } from '../../lib/style';
import styles from './Chart.module.css';

interface ChartTooltipProps {
  visible: boolean;
  /** Where the point is, as fractions of the chart's width and height. */
  x: number;
  y: number;
  /** Hang to the left of the point instead of the right. */
  flip: boolean;
  title: ReactNode;
  children?: ReactNode;
}

export function ChartTooltip({ visible, x, y, flip, title, children }: ChartTooltipProps) {
  return (
    <div
      role="status"
      className={cx(styles.tooltip, flip && styles.flip)}
      data-visible={visible}
      style={cssVars({ '--x': x, '--y': y })}
    >
      {visible && (
        <>
          <span className={styles.tipTitle}>{title}</span>
          {children}
        </>
      )}
    </div>
  );
}

/** One line under a tooltip's title. */
export function TipLine({ children }: { children: ReactNode }) {
  return <span className={styles.tipLine}>{children}</span>;
}
