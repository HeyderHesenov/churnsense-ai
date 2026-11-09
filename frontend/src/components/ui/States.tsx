/**
 * What a section shows instead of its content: loading, nothing to show, or
 * a failure. A failure is told in the page's own words with a retry; the
 * server's message is never shown (``api/client``).
 */
import type { ReactNode } from 'react';
import { cssVars, cx } from '../../lib/style';
import { icons } from '../icons';
import { Button } from './Button';
import styles from './States.module.css';

interface SkeletonProps {
  /** Read out instead of the placeholder shapes. */
  label: string;
  rows?: number;
  /** Each row's height in px. */
  rowHeight?: number;
  className?: string | undefined;
}

export function Skeleton({ label, rows = 3, rowHeight = 36, className }: SkeletonProps) {
  return (
    <div role="status" className={cx(styles.skeleton, className)}>
      <span className="sr">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <span
          key={i}
          className={styles.block}
          style={cssVars({ '--h': `${rowHeight}px` })}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  children: ReactNode;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div role="status" className={styles.empty}>
      <icons.emptySearch size={28} strokeWidth={1.6} className={styles.icon} />
      <p className={styles.title}>{title}</p>
      <p className={styles.body}>{children}</p>
      {action && (
        <Button size="md" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

type ErrorStateProps = {
  /** What could not be done, in the page's words. */
  children: ReactNode;
  /** One line inside a card, rather than a block in place of a table. */
  inline?: boolean;
} & (
  | {
      onRetry: () => void;
      /**
       * The id of a focusable element that stays while the retry loads: a query
       * asked again goes back to loading at once, which takes this state and its
       * button away, so the focus moves there instead of to the page's body.
       */
      focusTo: string;
      action?: never;
    }
  /** A way forward other than "Try again", such as a link that reloads. */
  | { action: ReactNode; onRetry?: never; focusTo?: never }
);

export function ErrorState({
  children,
  onRetry,
  focusTo,
  action,
  inline = false,
}: ErrorStateProps) {
  const retry = () => {
    onRetry?.();
    if (focusTo) document.getElementById(focusTo)?.focus();
  };
  return (
    <div role="alert" className={inline ? styles.errorInline : styles.error}>
      <p className={styles.body}>{children}</p>
      {action ?? (
        <Button size={inline ? 'xs' : 'md'} onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
