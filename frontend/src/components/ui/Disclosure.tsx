/**
 * "Read more": a summary that opens to show the detail under it. The browser's
 * own ``<details>``, so it opens from the keyboard (Enter or Space), a press
 * or a tap, says whether it is open to a screen reader, and works before any
 * script has run. The page does not own its state: it is the reader's.
 */
import type { ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './Disclosure.module.css';

interface DisclosureProps {
  /** What opening it shows, e.g. "Required columns (18)". */
  summary: ReactNode;
  children: ReactNode;
  /** Open when first shown. */
  defaultOpen?: boolean;
  className?: string | undefined;
}

export function Disclosure({ summary, children, defaultOpen = false, className }: DisclosureProps) {
  return (
    <details className={cx(styles.disclosure, className)} open={defaultOpen || undefined}>
      <summary className={styles.summary}>
        <span className={styles.marker} aria-hidden="true" />
        {summary}
      </summary>
      <div className={styles.body}>{children}</div>
    </details>
  );
}
