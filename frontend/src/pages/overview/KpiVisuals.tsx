/**
 * The key figures' small drawings: each draws figures the overview already
 * returned, beside the figure the tile states. The tile hides them from
 * screen readers (``StatTile``'s ``visual``); its words say the same.
 */
import type { Overview } from '../../api/queries';
import { arcs, scaleTop, starts } from './scale';
import styles from './KpiVisuals.module.css';

/** Customers per 0.05 of probability, each bar in its band's colour. */
export function MiniHistogram({ bins }: { bins: Overview['histogram'] }) {
  const top = Math.max(1, ...bins.map((bin) => bin.customers));
  const width = 4;
  return (
    <svg
      className={styles.histogram}
      viewBox={`0 0 ${bins.length * width} 32`}
      preserveAspectRatio="none"
    >
      {bins.map((bin, i) => {
        const height = (bin.customers / top) * 32;
        return (
          <rect
            key={i}
            data-band={bin.band}
            className={styles.bar}
            x={i * width + 0.5}
            y={32 - height}
            width={width - 1}
            height={height}
          />
        );
      })}
    </svg>
  );
}

/**
 * Each band's observed churn (train and validation), the bands left to right.
 * A band whose rate the API does not publish has no bar: an empty slot, never a zero.
 */
export function BandRates({ bands }: { bands: Overview['bands'] }) {
  const rates = bands.map((band) => band.observed?.rate ?? null);
  const top = scaleTop(rates);
  const width = 10;
  return (
    <svg
      className={styles.rates}
      viewBox={`0 0 ${bands.length * width} 32`}
      preserveAspectRatio="none"
    >
      {bands.map((band, i) => {
        const rate = rates[i];
        if (rate === null || rate === undefined) return null;
        const height = (rate / top) * 32;
        return (
          <rect
            key={band.name}
            data-band={band.name}
            className={styles.bar}
            x={i * width + 1.5}
            y={32 - height}
            width={width - 3}
            height={height}
          />
        );
      })}
    </svg>
  );
}

/** Each band's share of monthly charges, stacked left to right. */
export function ChargeShares({ bands }: { bands: Overview['bands'] }) {
  const shares = bands.map((band) => band.monthly_share ?? 0);
  const at = starts(shares);
  return (
    <svg className={styles.stack} viewBox="0 0 100 10" preserveAspectRatio="none">
      {bands.map((band, i) => (
        <rect
          key={band.name}
          data-band={band.name}
          className={styles.segment}
          x={(at[i] ?? 0) * 100}
          y={0}
          width={(shares[i] ?? 0) * 100}
          height={10}
        />
      ))}
    </svg>
  );
}

/** A share as a ring: violet for the riskiest bands, the accent for everyone flagged. */
export function ShareRing({ share, tone }: { share: number; tone: 'accent' | 'violet' }) {
  const [arc] = arcs([share], 0);
  return (
    <svg className={styles.ring} viewBox="0 0 36 36" data-tone={tone}>
      <circle className={styles.ringTrack} cx={18} cy={18} r={14} pathLength={100} />
      {arc && (
        <circle
          className={styles.ringArc}
          cx={18}
          cy={18}
          r={14}
          pathLength={100}
          strokeDasharray={arc.dash}
          transform="rotate(-90 18 18)"
        />
      )}
    </svg>
  );
}
