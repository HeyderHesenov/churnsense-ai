/** A page's title block: eyebrow, the page's one h1, and a lede. */
import type { ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './PageHeader.module.css';

interface PageHeaderProps {
  eyebrow: string;
  title: ReactNode;
  /** The lede paragraph. */
  children?: ReactNode;
  /** The simulator's money pages wear amber. */
  tone?: 'accent' | 'sim';
  /** 32 px on the overview-style pages, 30 on the dense ones. */
  size?: 'lg' | 'md';
  className?: string | undefined;
}

export function PageHeader({
  eyebrow,
  title,
  children,
  tone = 'accent',
  size = 'lg',
  className,
}: PageHeaderProps) {
  return (
    <div className={cx(styles.root, className)}>
      <p className={cx(styles.eyebrow, tone === 'sim' && styles.sim)}>{eyebrow}</p>
      <h1 className={cx(styles.title, size === 'md' && styles.md)}>{title}</h1>
      {children && <p className={styles.lede}>{children}</p>}
    </div>
  );
}
