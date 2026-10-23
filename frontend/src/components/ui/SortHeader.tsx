/**
 * A column header that sorts its table. It reports its state with
 * ``aria-sort``; what a click does (flip, or start a new column) is
 * ``nextSort``, kept apart so every table sorts the same way.
 */
import type { Direction } from '../../api/queries';
import { cx } from '../../lib/style';
import styles from './Table.module.css';

export interface Sort<C extends string> {
  column: C;
  dir: Direction;
}

/** The active column flips; another column starts at its own default direction. */
export function nextSort<C extends string>(
  current: Sort<C>,
  column: C,
  defaults: Record<C, Direction>,
): Sort<C> {
  if (current.column === column) return { column, dir: current.dir === 'asc' ? 'desc' : 'asc' };
  return { column, dir: defaults[column] };
}

const ARIA = { asc: 'ascending', desc: 'descending' } as const;
const ARROW = { asc: '↑', desc: '↓' } as const;

interface SortHeaderProps<C extends string> {
  column: C;
  label: string;
  sort: Sort<C>;
  onSort: (column: C) => void;
  align?: 'start' | 'end';
}

export function SortHeader<C extends string>({
  column,
  label,
  sort,
  onSort,
  align = 'start',
}: SortHeaderProps<C>) {
  const active = sort.column === column;
  return (
    <div
      role="columnheader"
      aria-sort={active ? ARIA[sort.dir] : 'none'}
      className={cx(styles.headerCell, align === 'end' && styles.end)}
    >
      <button
        type="button"
        className={styles.sortButton}
        data-active={active}
        onClick={() => onSort(column)}
      >
        {label}
        <span aria-hidden="true">{active ? ARROW[sort.dir] : '↕'}</span>
      </button>
    </div>
  );
}
