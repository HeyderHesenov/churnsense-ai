/**
 * The row under a table: what is shown, and previous / next. Pages are
 * 1-based, as the API counts them; an empty result is one page with nowhere
 * to go. A button with nowhere to go is marked so and does nothing, but stays
 * focusable: the button that reached the end keeps the focus.
 */
import { icons } from '../icons';
import styles from './Table.module.css';

interface PaginationProps {
  page: number;
  pages: number;
  onPage: (page: number) => void;
  /** "Showing 1–8 of 2,306 flagged". */
  note: string;
  pageSize?: { value: number; options: readonly number[]; onChange: (size: number) => void };
}

export function Pagination({ page, pages, onPage, note, pageSize }: PaginationProps) {
  const last = Math.max(pages, 1);
  const first = page <= 1;
  const end = page >= last;
  return (
    <div className={styles.pagination}>
      <span className={styles.note}>{note}</span>
      <div className={styles.pager}>
        {pageSize && (
          <>
            <span className={styles.rowsLabel} aria-hidden="true">
              Rows
            </span>
            <select
              aria-label="Rows per page"
              className={styles.pageSize}
              value={pageSize.value}
              onChange={(event) => pageSize.onChange(Number(event.target.value))}
            >
              {pageSize.options.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </>
        )}
        <button
          type="button"
          className={styles.pageButton}
          aria-label="Previous page"
          aria-disabled={first}
          onClick={() => {
            if (!first) onPage(Math.min(page, last) - 1);
          }}
        >
          <icons.chevronLeft size={15} />
        </button>
        <span className={styles.position}>
          {Math.min(page, last)} / {last}
        </span>
        <button
          type="button"
          className={styles.pageButton}
          aria-label="Next page"
          aria-disabled={end}
          onClick={() => {
            if (!end) onPage(page + 1);
          }}
        >
          <icons.chevronRight size={15} />
        </button>
      </div>
    </div>
  );
}
