/**
 * The retention queue: flagged customers, most monthly revenue at risk first.
 *
 * Its filters and page live in the address. A selection lives in memory (up
 * to 200 customers, the export's limit); its total is the server's, so the
 * browser sums nothing. A page's attributions come from one explanation
 * request for its eight customers.
 */
import { useEffect, useRef, useState } from 'react';
import { exportUrl } from '../../api/client';
import {
  useCustomers,
  useExplanations,
  type CustomerRow,
  type Explanation,
  type Overview,
} from '../../api/queries';
import type { SetParams } from '../../app/params';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import {
  AttributionChip,
  BandLabel,
  OutcomePill,
  ProbabilityMeter,
} from '../../components/ui/Badges';
import { Button, ButtonLink } from '../../components/ui/Button';
import { Select } from '../../components/ui/Field';
import { Pagination } from '../../components/ui/Pagination';
import { nextSort, SortHeader } from '../../components/ui/SortHeader';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { CardHeader } from '../../components/ui/Surface';
import { RemovableChip, ToggleChip } from '../../components/ui/Toggle';
import type { BandName } from '../../lib/bands';
import { cents, cut, int } from '../../lib/format';
import { CHIP, isFeature, PARTITION_OPTIONS, profile } from '../../lib/labels';
import { cx } from '../../lib/style';
import {
  chipBands,
  defaultBands,
  MAX_SELECTED,
  PAGE_SIZE,
  queueQuery,
  select,
  toggleBand,
  type QUEUE_PARAMS,
  type QueueState,
} from './queue';
import { unflaggedNote } from './copy';
import styles from './RetentionQueue.module.css';

/** The queue's heading: where the reader continues when its controls go away. */
export const QUEUE_TITLE_ID = 'queue-title';

type Column = QueueState['sort'];
const DEFAULT_DIRECTION = {
  probability: 'desc',
  tenure: 'asc',
  monthly: 'desc',
  at_risk: 'desc',
} as const;

interface RetentionQueueProps {
  book: Overview;
  state: QueueState;
  set: SetParams<typeof QUEUE_PARAMS>;
}

export function RetentionQueue({ book, state, set }: RetentionQueueProps) {
  const defaults = defaultBands(book);
  const bands = state.bands ?? defaults;
  const listing = useCustomers(queueQuery(state, defaults));
  const page = listing.data;
  // With no band chosen the listing asks nothing, but the chips still say what each
  // would list: the counts do not depend on the bands asked for.
  const scope = useCustomers(
    bands.length > 0
      ? queueQuery(state, defaults)
      : queueQuery({ ...state, bands: null }, defaults),
  );
  const chipCount = (name: string) =>
    scope.data && !scope.isPlaceholderData ? int(scope.data.band_counts[name] ?? 0) : '…';
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const narrowed = state.part !== null || state.q !== '';
  const filtered = state.bands !== null || narrowed;
  // A reset or a cleared search takes its button away: the reader continues at the queue's heading.
  const clear = (changes: Parameters<typeof set>[0]) => {
    set(changes);
    document.getElementById(QUEUE_TITLE_ID)?.focus();
  };
  const reset = () => clear({ bands: null, part: null, q: '', page: 1 });

  const toggle = (ids: readonly string[], on: boolean) => setSelected(select(selected, ids, on));
  const nobodyFlagged = chipBands(book).length === 0 && state.bands === null;
  // Said once the figures are the new ones, never while the last view's stand in.
  const status =
    bands.length === 0
      ? 'No band selected.'
      : page && !listing.isPlaceholderData
        ? `${inScope(page)}, in scope.`
        : '';

  return (
    <section
      id="queue"
      aria-labelledby={QUEUE_TITLE_ID}
      aria-busy={listing.isPlaceholderData}
      className={styles.queue}
    >
      <p role="status" className="sr">
        {status}
      </p>
      <div className={styles.inset}>
        <CardHeader
          id={QUEUE_TITLE_ID}
          focusable
          title={
            <>
              Retention queue{' '}
              <span className={styles.count}>
                {bands.length === 0
                  ? '0 flagged'
                  : page && !listing.isPlaceholderData
                    ? inScope(page)
                    : '…'}
              </span>
            </>
          }
          subtitle="Which flagged customers carry the most monthly revenue at risk? It is known only for customers recorded active. Test customers stay listed, status unknown: neither confirmed current nor churned."
          aside={
            <Link to="/explorer?flagged=true" className={styles.arrowLink}>
              See all {int(book.flagged.customers)} flagged, churned included, in the explorer
              <icons.arrowRight size={14} />
            </Link>
          }
        />
      </div>

      <div className={cx(styles.inset, styles.filters)}>
        <span className={styles.filterLabel}>Band</span>
        <div role="group" aria-label="Band" className={styles.chips}>
          {chipBands(book).map((band) => (
            <ToggleChip
              key={band.name}
              band={band.name}
              pressed={bands.includes(band.name)}
              count={chipCount(band.name)}
              onToggle={() => set({ bands: toggleBand(bands, band.name, book), page: 1 })}
            >
              {band.name}
            </ToggleChip>
          ))}
        </div>
        <span className={styles.divider} aria-hidden="true" />
        <Select
          label="Partition"
          value={state.part ?? 'all'}
          options={PARTITION_OPTIONS}
          onChange={(part) => set({ part: part === 'all' ? null : part, page: 1 })}
        />
        {state.q && (
          <RemovableChip label="Clear search" onRemove={() => clear({ q: '', page: 1 })}>
            id ∋ “{state.q}”
          </RemovableChip>
        )}
        {filtered && (
          <Button variant="quiet" size="sm" onClick={reset}>
            Reset filters
          </Button>
        )}
      </div>

      {selected.size > 0 && <BulkBar selected={selected} onClear={() => setSelected(new Set())} />}

      {nobodyFlagged ? (
        <EmptyState title="Nobody is flagged">
          No customer reaches the {cut(book.threshold)} threshold.
        </EmptyState>
      ) : bands.length === 0 ? (
        <EmptyState title="No band selected" action={{ label: 'Reset filters', onClick: reset }}>
          Choose at least one band to see flagged customers.
        </EmptyState>
      ) : listing.isPending ? (
        <div className={styles.inset}>
          <Skeleton label="Loading the queue" rows={PAGE_SIZE} rowHeight={44} />
        </div>
      ) : listing.isError || !page ? (
        <div className={styles.inset}>
          <ErrorState inline onRetry={() => void listing.refetch()} focusTo={QUEUE_TITLE_ID}>
            The queue could not be loaded.
          </ErrorState>
        </div>
      ) : page.rows.length === 0 ? (
        <EmptyState
          title={emptyTitle(bands, book, narrowed)}
          {...(filtered && { action: { label: 'Reset filters', onClick: reset } })}
        >
          {emptyBody(bands, book, narrowed)}
        </EmptyState>
      ) : (
        <>
          <Table
            rows={page.rows}
            state={state}
            set={set}
            selected={selected}
            full={selected.size >= MAX_SELECTED}
            onSelect={toggle}
          />
          <Pagination
            page={page.page}
            pages={page.pages}
            onPage={(next) => set({ page: next })}
            note={`Showing ${int((page.page - 1) * page.page_size + 1)}–${int((page.page - 1) * page.page_size + page.rows.length)} of ${int(page.total)} flagged`}
          />
        </>
      )}
    </section>
  );
}

/** The one band chosen, if it is a band nobody in is flagged. */
function unflaggedBand(bands: readonly BandName[], book: Overview) {
  const only = bands.length === 1 ? book.bands.find((b) => b.name === bands[0]) : undefined;
  return only && only.flagged === 0 ? only : undefined;
}

/** "300 flagged: 225 active, 75 status unknown": the API's counts, never worked out here. */
function inScope(page: { total: number; status_counts: { active: number; unknown: number } }) {
  return `${int(page.total)} flagged: ${int(page.status_counts.active)} active, ${int(page.status_counts.unknown)} status unknown`;
}

/** The bands chosen, if everyone flagged in them is recorded as churned. */
function churnedBands(bands: readonly BandName[], book: Overview) {
  const chosen = book.bands.filter((b) => bands.includes(b.name as BandName));
  const all =
    chosen.length > 0 &&
    chosen.every((b) => b.flagged_not_churned === 0) &&
    chosen.some((b) => b.flagged > 0);
  return all ? chosen : undefined;
}

function churnedName(chosen: readonly { name: string }[]): string {
  return chosen.length === 1
    ? `the ${chosen[0]?.name ?? ''} band`
    : `the ${chosen.map((b) => b.name).join(' and ')} bands`;
}

function emptyTitle(bands: readonly BandName[], book: Overview, narrowed: boolean): string {
  // Book-level counts say nothing of a partition or a search: those read as no match.
  const churned = narrowed ? undefined : churnedBands(bands, book);
  if (churned) return `Everyone flagged in ${churnedName(churned)} is recorded as churned`;
  const band = unflaggedBand(bands, book);
  return band ? `Nobody in the ${band.name} band is flagged` : 'No flagged customer matches';
}

function emptyBody(bands: readonly BandName[], book: Overview, narrowed: boolean): string {
  if (!narrowed && churnedBands(bands, book))
    return 'The explorer still lists them, with their outcomes.';
  const band = unflaggedBand(bands, book);
  if (!band) return 'Try another band, partition or ID; the explorer searches the whole book.';
  return unflaggedNote(band, book.threshold);
}

function BulkBar({ selected, onClear }: { selected: ReadonlySet<string>; onClear: () => void }) {
  const ids = [...selected].sort();
  const summary = useCustomers({ ids, sort: 'id', dir: 'asc', page: 1, page_size: 1 });
  // A total that belongs to the last selection is never shown for this one.
  const atRisk = summary.isPlaceholderData ? undefined : summary.data?.summary.at_risk;
  return (
    <div role="region" aria-label="Bulk actions" className={styles.bulk}>
      <span className={styles.bulkCount}>{int(ids.length)} selected</span>
      <span className={styles.bulkValue}>
        {atRisk === undefined ? '…' : `${cents(atRisk)} / mo at risk, recorded active`}
      </span>
      {ids.length >= MAX_SELECTED && (
        <span className={styles.bulkNote}>The most a selection can hold.</span>
      )}
      <span className={styles.spacer} />
      <ButtonLink href={exportUrl({ ids })} download size="sm">
        Export selection
      </ButtonLink>
      <Button variant="quiet" size="sm" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}

interface TableProps {
  rows: readonly CustomerRow[];
  state: QueueState;
  set: SetParams<typeof QUEUE_PARAMS>;
  selected: ReadonlySet<string>;
  /** The selection is at its limit: unselected rows cannot join. */
  full: boolean;
  onSelect: (ids: readonly string[], on: boolean) => void;
}

function Table({ rows, state, set, selected, full, onSelect }: TableProps) {
  const ids = rows.map((row) => row.id);
  const explained = useExplanations(ids);
  const byId = new Map(explained.data?.explanations.map((e) => [e.customer_id, e]));
  const sort = { column: state.sort, dir: state.dir };
  const onSort = (column: Column) => {
    const next = nextSort(sort, column, DEFAULT_DIRECTION);
    set({ sort: next.column, dir: next.dir, page: 1 });
  };
  const all = ids.every((id) => selected.has(id));
  const some = !all && ids.some((id) => selected.has(id));

  return (
    <>
      {explained.isError && (
        <p role="status" className={cx(styles.inset, styles.notice)}>
          Attributions are unavailable right now.
        </p>
      )}
      <div className={styles.scroll}>
        <div role="table" aria-label="Flagged customers" className={styles.table}>
          <div role="rowgroup">
            <div role="row" className={cx(styles.row, styles.head)}>
              <div role="columnheader" className={cx(styles.headCell, styles.first)}>
                <PageCheckbox
                  checked={all}
                  mixed={some}
                  // At the limit it can still clear the page; it cannot add to it.
                  disabled={full && !all && !some}
                  onChange={(on) => onSelect(ids, on)}
                />
              </div>
              <div role="columnheader" className={styles.headCell}>
                Customer
              </div>
              <SortHeader column="probability" label="Probability" sort={sort} onSort={onSort} />
              <div role="columnheader" className={styles.headCell}>
                Band
              </div>
              <SortHeader column="tenure" label="Tenure" sort={sort} onSort={onSort} align="end" />
              <SortHeader
                column="monthly"
                label="Monthly"
                sort={sort}
                onSort={onSort}
                align="end"
              />
              <SortHeader
                column="at_risk"
                label="At risk / mo"
                sort={sort}
                onSort={onSort}
                align="end"
              />
              <div role="columnheader" className={styles.headCell}>
                Strongest attributions
              </div>
              <div role="columnheader" className={styles.headCell}>
                Outcome
              </div>
              <div role="columnheader" className={cx(styles.headCell, styles.last)}>
                <span className="sr">Action</span>
              </div>
            </div>
          </div>
          <div role="rowgroup">
            {rows.map((row) => (
              <Row
                key={row.id}
                row={row}
                selected={selected.has(row.id)}
                disabled={full && !selected.has(row.id)}
                onSelect={(on) => onSelect([row.id], on)}
                explanation={byId.get(row.id)}
                attributions={
                  explained.isError ? 'failed' : explained.isPending ? 'loading' : 'ready'
                }
              />
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/** "Select all rows on this page", mixed when only some are. */
function PageCheckbox({
  checked,
  mixed,
  disabled,
  onChange,
}: {
  checked: boolean;
  mixed: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <input
      ref={box}
      type="checkbox"
      className={styles.checkbox}
      aria-label="Select all rows on this page"
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}

interface RowProps {
  row: CustomerRow;
  selected: boolean;
  disabled: boolean;
  onSelect: (on: boolean) => void;
  explanation: Explanation | undefined;
  attributions: 'loading' | 'ready' | 'failed';
}

function Row({ row, selected, disabled, onSelect, explanation, attributions }: RowProps) {
  const strongest = explanation?.contributions.slice(0, 2) ?? [];
  return (
    <div role="row" className={styles.row} data-selected={selected}>
      <div role="cell" className={cx(styles.cell, styles.first)}>
        <input
          type="checkbox"
          className={styles.checkbox}
          aria-label={`Select ${row.id}`}
          checked={selected}
          disabled={disabled}
          onChange={(event) => onSelect(event.target.checked)}
        />
      </div>
      <div role="cell" className={cx(styles.cell, styles.customer)}>
        <span className={styles.id}>{row.id}</span>
        {row.status === 'unknown' && <span className={styles.statusTag}>Status unknown</span>}
        <span className={styles.profile}>
          {profile(row.contract, row.internet_service, row.payment_method)}
        </span>
      </div>
      <div role="cell" className={styles.cell}>
        <ProbabilityMeter value={row.churn_probability} band={row.risk_band} />
      </div>
      <div role="cell" className={styles.cell}>
        <BandLabel band={row.risk_band} />
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
      <div role="cell" className={cx(styles.cell, styles.attributions)}>
        {attributions === 'loading' ? (
          <>
            <span className={styles.chipSkeleton} aria-hidden="true" />
            <span className={styles.chipSkeleton} aria-hidden="true" />
            <span className="sr">Loading attributions</span>
          </>
        ) : strongest.length === 0 ? (
          <span className={styles.none}>—</span>
        ) : (
          strongest.map((c) => (
            <AttributionChip
              key={c.feature}
              label={isFeature(c.feature) ? CHIP[c.feature] : c.label}
              value={c.shap_value}
            />
          ))
        )}
      </div>
      <div role="cell" className={styles.cell}>
        <OutcomePill outcome={row.outcome} partition={row.partition} />
      </div>
      <div role="cell" className={cx(styles.cell, styles.number, styles.last)}>
        <Link
          to={`/customer?${new URLSearchParams({ id: row.id }).toString()}`}
          aria-label={`Explain ${row.id}`}
          className={styles.explain}
        >
          Explain
        </Link>
      </div>
    </div>
  );
}
