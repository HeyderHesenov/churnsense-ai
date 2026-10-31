/**
 * Which assumptions a card's figures are for, from the response's echo, and,
 * while they are not the assumptions set, a mark saying so: a card never
 * shows figures for other assumptions unnamed.
 */
import { pinSummary } from './copy';
import type { Echo } from './simulator';
import styles from './Simulator.module.css';

export const STALE_MARK = 'Not the assumptions set.';

export function ForAssumptions({ echo, stale }: { echo: Echo; stale: boolean }) {
  return (
    <p className={styles.forLine}>
      <span>For {pinSummary(echo)}</span>
      {stale && <span className={styles.staleMark}>{STALE_MARK}</span>}
    </p>
  );
}
