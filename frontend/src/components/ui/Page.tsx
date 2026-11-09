/** A page's content column, at one of the two densities the artboards use. */
import type { ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './Page.module.css';

interface PageProps {
  children: ReactNode;
  /** spacious: overview, performance, customer, simulator; dense: explorer, patterns, explainability. */
  density?: 'spacious' | 'dense';
}

export function Page({ children, density = 'spacious' }: PageProps) {
  return <div className={cx(styles.page, density === 'dense' && styles.dense)}>{children}</div>;
}
