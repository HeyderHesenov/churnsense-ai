/**
 * A figure that counts up to its value (``lib/useCountUp``).
 *
 * A screen reader only ever reads the value itself. While the figure moves,
 * the moving text is hidden from it and the value is there instead, from the
 * first frame; once it has settled there is one plain text again. Nothing is
 * announced: there is no live region, so the frames in between are never read.
 */
import { useCountUp } from '../../lib/useCountUp';
import styles from './CountUp.module.css';

interface CountUpProps {
  value: number | null;
  /** The figure as shown, e.g. ``int`` or ``pct``; applied to each frame and to the value. */
  format: (value: number) => string;
  duration?: number;
}

export function CountUp({ value, format, duration }: CountUpProps) {
  const shown = useCountUp(value, duration);
  if (value === null || shown === null) return <span className={styles.figure}>—</span>;
  const final = format(value);
  if (shown === value) return <span className={styles.figure}>{final}</span>;
  return (
    <span className={styles.figure}>
      <span aria-hidden="true">{format(shown)}</span>
      <span className="sr">{final}</span>
    </span>
  );
}
