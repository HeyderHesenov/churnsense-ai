/**
 * The surfaces figures sit on: a card (a named region with its heading), a
 * stat tile, and the small tags that qualify a figure.
 */
import type { ReactNode } from 'react';
import { Link } from '../../app/router';
import { cx } from '../../lib/style';
import { icons } from '../icons';
import styles from './Surface.module.css';

interface CardProps {
  /** The id of the card's heading, which names the region. */
  titleId: string;
  children: ReactNode;
  /** dense: the explorer's tighter padding; sim: simulated money; flush: a table edge to edge. */
  variant?: 'standard' | 'dense' | 'sim' | 'flush';
  className?: string;
}

export function Card({ titleId, children, variant = 'standard', className }: CardProps) {
  return (
    <section aria-labelledby={titleId} className={cx(styles.card, styles[variant], className)}>
      {children}
    </section>
  );
}

interface CardHeaderProps {
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Right-aligned: a tag, a toggle or a legend. */
  aside?: ReactNode;
}

export function CardHeader({ id, title, subtitle, aside }: CardHeaderProps) {
  return (
    <div className={styles.header}>
      <div className={styles.heading}>
        <h2 id={id} className={styles.title}>
          {title}
        </h2>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>
      {aside}
    </div>
  );
}

interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  /** A tile that is also a way into the figure: where it goes, and what it says. */
  link?: { to: string; label: string; onFollow?: () => void };
  /** The accent line along the top: the tile the page is about. */
  accent?: boolean;
  compact?: boolean;
}

export function StatTile({
  label,
  value,
  sub,
  link,
  accent = false,
  compact = false,
}: StatTileProps) {
  const classes = cx(
    styles.tile,
    compact && styles.compact,
    accent && styles.accent,
    link && styles.linkTile,
  );
  const body = (
    <>
      <span className={styles.tileLabel}>
        {label}
        {link && <icons.arrowUpRight size={14} />}
      </span>
      <span className={styles.tileValue}>{value}</span>
      {sub && <span className={styles.tileSub}>{sub}</span>}
    </>
  );
  if (!link) return <div className={classes}>{body}</div>;
  return (
    <Link to={link.to} aria-label={link.label} className={classes} onClick={link.onFollow}>
      {body}
    </Link>
  );
}

/** A small uppercase qualifier: "TRAIN · 4,225", "TEST", "NOT SCANNED". */
export function Tag({ tone = 'meta', children }: { tone?: 'meta' | 'sim'; children: ReactNode }) {
  return <span className={cx(styles.tag, tone === 'sim' && styles.simTag)}>{children}</span>;
}

/** Every money figure that rests on assumptions says so. */
export function SimulatedBadge() {
  return <Tag tone="sim">SIMULATED</Tag>;
}
