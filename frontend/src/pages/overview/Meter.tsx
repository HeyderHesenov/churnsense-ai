/**
 * A horizontal bar for a figure the caller has from the API: a track, the
 * figure's share of it, and an optional tick (an average). Drawn in SVG with
 * percentage attributes, so the width needs no style attribute; hidden from
 * screen readers, since the text beside it says the figure.
 */
import { cx } from '../../lib/style';
import styles from './Meter.module.css';

/** ``value`` as a percentage of the track, kept inside it. */
export function trackPercent(value: number): string {
  return `${Math.max(0, Math.min(1, value)) * 100}%`;
}

interface MeterProps {
  /** The bar's share of the track, 0 to 1; ``null`` draws the track alone. */
  value: number | null;
  /** Where the tick goes, 0 to 1. */
  tick?: number | null;
  tone?: 'accent' | 'sim' | 'risk';
  thin?: boolean;
  className?: string | undefined;
}

export function Meter({
  value,
  tick = null,
  tone = 'accent',
  thin = false,
  className,
}: MeterProps) {
  return (
    <svg
      className={cx(styles.meter, thin && styles.thin, className)}
      data-tone={tone}
      aria-hidden="true"
      focusable="false"
    >
      <rect className={styles.track} width="100%" />
      {value !== null && <rect className={styles.fill} width={trackPercent(value)} />}
      {tick !== null && (
        <line
          className={styles.tick}
          x1={trackPercent(tick)}
          x2={trackPercent(tick)}
          y1="0"
          y2="100%"
        />
      )}
    </svg>
  );
}
