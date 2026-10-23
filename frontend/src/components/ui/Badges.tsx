/**
 * The small marks a customer row carries: its band, its outcome, a
 * probability meter and an attribution. Each shows a value the API returned;
 * a colour always comes with words, never instead of them.
 */
import type { Partition } from '../../api/queries';
import { probability, signedPoints } from '../../lib/format';
import { cssVars, cx } from '../../lib/style';
import styles from './Badges.module.css';

/** A band's colour square, beside its name. Decorative. */
export function BandSwatch({ band }: { band: string }) {
  return <span className={styles.swatch} data-band={band} aria-hidden="true" />;
}

/** A band as a table cell writes it: swatch and name, optionally in the band's ink. */
export function BandLabel({ band, inked = false }: { band: string; inked?: boolean }) {
  return (
    <span className={cx(styles.bandLabel, inked && styles.inked)} data-band={band}>
      <span className={styles.swatch} aria-hidden="true" />
      {band}
    </span>
  );
}

export type Outcome = 'churned' | 'stayed' | 'withheld';

const OUTCOME: Record<Outcome, string> = {
  churned: 'Churned',
  stayed: 'Stayed',
  withheld: 'Withheld',
};
/** "unknown" is a customer whose partition could not be verified. */
const PARTITION: Record<Partition, string> = {
  train: 'train',
  validation: 'val',
  test: 'test',
  unknown: 'unverified',
};

/** "Churned · train": a known outcome, or a dashed "Withheld" where none may be shown. */
export function OutcomePill({ outcome, partition }: { outcome: Outcome; partition: Partition }) {
  return (
    <span className={styles.outcome} data-outcome={outcome}>
      {OUTCOME[outcome]} · {PARTITION[partition]}
    </span>
  );
}

/**
 * One feature's signed contribution: "Tenure +31.7", orange when it raises
 * the score, blue when it lowers it. The colour follows the sign shown, so a
 * contribution that rounds to zero is neither.
 */
export function AttributionChip({ label, value }: { label: string; value: number }) {
  const shown = signedPoints(value);
  const direction = shown.startsWith('+') ? 'raises' : shown.startsWith('−') ? 'lowers' : 'none';
  return (
    <span className={styles.attribution} data-direction={direction}>
      {label} {shown}
    </span>
  );
}

/** A probability as a short bar in its band's colour, then the number. */
export function ProbabilityMeter({ value, band }: { value: number; band: string }) {
  return (
    <span className={styles.meter} data-band={band}>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.fill} style={cssVars({ '--p': value })} />
      </span>
      <span className={styles.value}>{probability(value)}</span>
    </span>
  );
}
