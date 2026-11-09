/**
 * The customers the filters keep, a page at a time, in the order the API
 * sorted them. A row opens its customer beside the list (a disclosure: the
 * button says whether its panel is open). States are told in words: nothing
 * chosen, loading, failed, nothing matched.
 */
import type { UseQueryResult } from '@tanstack/react-query';
import { flushSync } from 'react-dom';
import type { CustomerPage, CustomerRow, ModelInfo, SortKey } from '../../api/queries';
import type { SetParams } from '../../app/params';
import { BandLabel, OutcomePill, ProbabilityMeter } from '../../components/ui/Badges';
import { Pagination } from '../../components/ui/Pagination';
import { nextSort, SortHeader } from '../../components/ui/SortHeader';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { cents } from '../../lib/format';
import { profile } from '../../lib/labels';
import { cx } from '../../lib/style';
import { emptyHint, pageNote } from './copy';
import {
  CUSTOMERS_ID,
  DEFAULT_DIRECTION,
  DETAIL_ID,
  PAGE_SIZES,
  pageKeeping,
  RESET,
  type EXPLORER_PARAMS,
  type ExplorerState,
  type IdleReason,
} from './explorer';
import styles from './CustomerTable.module.css';

interface CustomerTableProps {
  state: ExplorerState;
  set: SetParams<typeof EXPLORER_PARAMS>;
  listing: UseQueryResult<CustomerPage>;
  /** The listing's page; undefined while it has nothing to ask for (``idle``). */
  page: CustomerPage | undefined;
  idle: IdleReason;
  info: ModelInfo | undefined;
  withheld: string | null;
  /** The open customer's place in the whole list, when it is on this page. */
  openIndex: number | null;
  onOpen: (id: string) => void;
  onClose: () => void;
}

export function CustomerTable({
  state,
  set,
  listing,
  page,
  idle,
  info,
  withheld,
  openIndex,
  onOpen,
  onClose,
}: CustomerTableProps) {
  // The empty state, and its button, go with the filters: the focus goes to the list.
  const reset = () => {
    flushSync(() => set(RESET));
    document.getElementById(CUSTOMERS_ID)?.focus();
  };
  // While the last view's rows stand in, the page asked for is the address's,
  // not the one those rows came from.
  const pageShown = (shown: CustomerPage) => (listing.isPlaceholderData ? state.page : shown.page);
  const hint = emptyHint({
    filters: state,
    idle,
    bands: info?.risk_bands ?? undefined,
    threshold: info?.threshold,
    withheld,
  });
  return (
    <section
      id={CUSTOMERS_ID}
      tabIndex={-1}
      aria-label="Customers"
      aria-busy={idle === null && listing.isPlaceholderData}
      className={styles.list}
    >
      {idle ? (
        <EmptyState
          title="No customer matches these filters"
          action={{ label: 'Reset filters', onClick: reset }}
        >
          {hint}
        </EmptyState>
      ) : listing.isPending ? (
        <div className={styles.inset}>
          <Skeleton label="Loading scored customers…" rows={6} />
        </div>
      ) : listing.isError || !page ? (
        <ErrorState onRetry={() => void listing.refetch()} focusTo={CUSTOMERS_ID}>
          The customer book could not be loaded. Every figure on this page comes from the API, so
          none is shown until it answers.
        </ErrorState>
      ) : page.rows.length === 0 ? (
        <EmptyState
          title="No customer matches these filters"
          action={{ label: 'Reset filters', onClick: reset }}
        >
          {hint}
        </EmptyState>
      ) : (
        <>
          <Table
            rows={page.rows}
            state={state}
            set={set}
            openId={state.id}
            onOpen={onOpen}
            onClose={onClose}
          />
          <Pagination
            page={pageShown(page)}
            pages={page.pages}
            onPage={(next) => set({ page: next })}
            note={pageNote(page, page.rows.length, state.id !== '')}
            pageSize={{
              value: Number(state.size),
              options: PAGE_SIZES.map(Number),
              onChange: (size) => {
                const value = PAGE_SIZES.find((s) => Number(s) === size);
                if (!value) return;
                // The reader's place: the open customer if it is shown, else the
                // first row of the page asked for.
                const anchor = openIndex ?? (pageShown(page) - 1) * Number(state.size);
                set({ size: value, page: pageKeeping(anchor, size) });
              },
            }}
          />
        </>
      )}
    </section>
  );
}

interface TableProps {
  rows: readonly CustomerRow[];
  state: ExplorerState;
  set: SetParams<typeof EXPLORER_PARAMS>;
  openId: string;
  onOpen: (id: string) => void;
  onClose: () => void;
}

function Table({ rows, state, set, openId, onOpen, onClose }: TableProps) {
  const sort = { column: state.sort, dir: state.dir };
  const onSort = (column: SortKey) => {
    const next = nextSort(sort, column, DEFAULT_DIRECTION);
    set({ sort: next.column, dir: next.dir, page: 1 });
  };
  return (
    <div className={styles.scroll}>
      <div role="table" aria-label="Customers" className={styles.table}>
        <div role="rowgroup">
          <div role="row" className={cx(styles.row, styles.head)}>
            <SortHeader column="id" label="Customer" sort={sort} onSort={onSort} />
            <SortHeader column="probability" label="Probability" sort={sort} onSort={onSort} />
            <div role="columnheader" className={styles.headCell}>
              Band
            </div>
            <SortHeader column="tenure" label="Tenure" sort={sort} onSort={onSort} align="end" />
            <SortHeader column="monthly" label="Monthly" sort={sort} onSort={onSort} align="end" />
            <SortHeader column="at_risk" label="At risk" sort={sort} onSort={onSort} align="end" />
            <div role="columnheader" className={cx(styles.headCell, styles.last)}>
              Outcome
            </div>
          </div>
        </div>
        <div role="rowgroup">
          {rows.map((row) => (
            <Row
              key={row.id}
              row={row}
              open={row.id === openId}
              onToggle={() => (row.id === openId ? onClose() : onOpen(row.id))}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Row({ row, open, onToggle }: { row: CustomerRow; open: boolean; onToggle: () => void }) {
  return (
    <div role="row" className={styles.row} data-open={open}>
      <div role="cell" className={styles.cell}>
        <button
          type="button"
          id={`open-${row.id}`}
          className={styles.open}
          aria-expanded={open}
          aria-controls={open ? DETAIL_ID : undefined}
          onClick={onToggle}
        >
          <span className={styles.id}>{row.id}</span>{' '}
          <span className={styles.profile}>
            {profile(row.contract, row.internet_service, row.payment_method)}
          </span>
        </button>
      </div>
      <div role="cell" className={styles.cell}>
        <ProbabilityMeter value={row.churn_probability} band={row.risk_band} />
      </div>
      <div role="cell" className={styles.cell}>
        <BandLabel band={row.risk_band} inked />
      </div>
      <div role="cell" className={cx(styles.cell, styles.number)}>
        {row.tenure} mo
      </div>
      <div role="cell" className={cx(styles.cell, styles.number)}>
        {cents(row.monthly_charges)}
      </div>
      <div role="cell" className={cx(styles.cell, styles.number, styles.strong)}>
        {row.at_risk === null ? '—' : cents(row.at_risk)}
      </div>
      <div role="cell" className={cx(styles.cell, styles.last)}>
        <OutcomePill outcome={row.outcome} partition={row.partition} />
      </div>
    </div>
  );
}
