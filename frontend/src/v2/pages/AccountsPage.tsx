/**
 * Accounts (M2): ``GET /api/v2/accounts``, the synthetic portfolio's accounts
 * filtered, ordered and paged by the API. The filters live in the address, so
 * a view can be linked and Back undoes a change; the order, the levels and
 * every figure are the API's.
 */
import { useId } from 'react';
import { ApiError } from '../../api/client';
import { param, useParams, type Values } from '../../app/params';
import { Link } from '../../app/router';
import { useSettledDraft } from '../../app/useSettledDraft';
import { Header } from '../../components/shell/Header';
import { SearchField } from '../../components/ui/Field';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { Pagination } from '../../components/ui/Pagination';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { Card } from '../../components/ui/Surface';
import { ToggleChip } from '../../components/ui/Toggle';
import {
  useAccounts,
  usePortfolioSummary,
  type AccountPage as Listing,
  type AccountQuery,
  type AccountRow,
} from '../api';
import { LEVEL_LABEL, SECTION, STATUS_LABEL } from '../copy';
import { STATUSES } from '../fields';
import { amount, count, plural, shortDate } from '../format';
import { DatasetBanner, LevelBadge, Notes, Refusal } from '../parts';
import styles from '../V2.module.css';

const LEVELS = ['attention', 'watch', 'no_signals', 'not_assessed'] as const;
const SORTS = ['priority', 'renewal_asc', 'mrr_desc'] as const;
const PAGE_SIZES = [10, 25, 50, 100] as const;
const RENEWAL_WINDOWS = [30, 60, 90, 180, 366] as const;
/** Printable text, as the API's search and segment filters require. */
const PRINTABLE = /^[^\p{Cc}]*$/u;

/** The list's state in the address, held to the bounds the API enforces. */
export const ACCOUNT_PARAMS = {
  level: param.list(LEVELS, [] as readonly (typeof LEVELS)[number][]),
  status: param.list(STATUSES, [] as readonly (typeof STATUSES)[number][]),
  currency: param.text({ pattern: /^[A-Z]{3}$/, maxLength: 3 }),
  segment: param.text({ pattern: PRINTABLE, maxLength: 64 }),
  renewal_within_days: param.optionalInt({ min: 0, max: 366 }),
  q: param.text({ pattern: PRINTABLE, maxLength: 100 }),
  sort: param.choice(SORTS, 'priority'),
  page: param.int({ min: 1, max: 10_000, fallback: 1 }),
  page_size: param.int({ min: 1, max: 100, fallback: 25 }),
};
type State = Values<typeof ACCOUNT_PARAMS>;

const SORT_LABEL = {
  priority: 'Priority (rule level, then nearest renewal)',
  renewal_asc: 'Nearest renewal',
  mrr_desc: 'Highest MRR (one currency)',
} as const;

/** The API's query for this state: empty filters left out. */
export function accountQuery(state: State): AccountQuery {
  return {
    ...(state.level.length > 0 && { level: [...state.level] }),
    ...(state.status.length > 0 && { status: [...state.status] }),
    ...(state.currency && { currency: state.currency }),
    ...(state.segment && { segment: state.segment }),
    ...(state.renewal_within_days !== null && { renewal_within_days: state.renewal_within_days }),
    ...(state.q && { q: state.q }),
    sort: state.sort,
    page: state.page,
    page_size: state.page_size,
  };
}

const TITLE_ID = 'accounts-title';

export default function AccountsPage() {
  const [state, set] = useParams(ACCOUNT_PARAMS);
  const listing = useAccounts(accountQuery(state));
  const summary = usePortfolioSummary();
  const [search, setSearch] = useSettledDraft(state.q, (q) => set({ q, page: 1 }));
  const filtered =
    state.level.length + state.status.length > 0 ||
    Boolean(state.currency || state.segment || state.q) ||
    state.renewal_within_days !== null;
  const clear = () =>
    set({
      level: [],
      status: [],
      currency: '',
      segment: '',
      renewal_within_days: null,
      q: '',
      sort: state.sort === 'mrr_desc' ? 'priority' : state.sort,
      page: 1,
    });
  const toggle = <T extends string>(chosen: readonly T[], value: T): T[] =>
    chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value];
  const currencies = summary.data?.currencies.map((c) => c.currency) ?? [];
  if (state.currency && !currencies.includes(state.currency)) currencies.push(state.currency);

  return (
    <>
      <Header section={SECTION} page="Accounts" />
      <Page density="dense">
        <PageHeader eyebrow="Synthetic demo · rules, not a model" title="Accounts" size="md">
          The accounts the rules point at first: attention, then watch, then no signals, each with
          the nearest renewal first. Open an account for its evidence and history.
        </PageHeader>
        {listing.data && <DatasetBanner dataset={listing.data.dataset} />}

        <section aria-label="Filters" className={styles.stack}>
          <div className={styles.filters}>
            <SearchField
              label="Search account ID or name"
              value={search}
              onChange={setSearch}
              placeholder="Search ID or name"
              maxLength={100}
            />
            <NativeSelect
              label="Currency"
              value={state.currency}
              onChange={(currency) =>
                set({
                  currency,
                  page: 1,
                  sort: !currency && state.sort === 'mrr_desc' ? 'priority' : state.sort,
                })
              }
              options={[
                { value: '', label: 'All currencies' },
                ...currencies.map((c) => ({ value: c, label: c })),
              ]}
            />
            <NativeSelect
              label="Renewing within"
              value={state.renewal_within_days === null ? '' : String(state.renewal_within_days)}
              onChange={(days) =>
                set({ renewal_within_days: days === '' ? null : Number(days), page: 1 })
              }
              options={[
                { value: '', label: 'Any time' },
                ...windowsWith(state.renewal_within_days).map((d) => ({
                  value: String(d),
                  label: `${count(d)} days`,
                })),
              ]}
            />
            <SegmentField value={state.segment} onSettle={(segment) => set({ segment, page: 1 })} />
            <NativeSelect
              label="Order"
              value={state.sort}
              onChange={(sort) => set({ sort: sort as State['sort'], page: 1 })}
              options={SORTS.map((s) => ({
                value: s,
                label: SORT_LABEL[s],
                disabled: s === 'mrr_desc' && !state.currency,
              }))}
            />
          </div>
          <fieldset className={styles.fieldset}>
            <legend className={styles.legend}>Rule level</legend>
            <div className={styles.chips}>
              {LEVELS.map((level) => (
                <ToggleChip
                  key={level}
                  pressed={state.level.includes(level)}
                  onToggle={() => set({ level: toggle(state.level, level), page: 1 })}
                >
                  {LEVEL_LABEL[level]}
                </ToggleChip>
              ))}
            </div>
          </fieldset>
          <fieldset className={styles.fieldset}>
            <legend className={styles.legend}>Status</legend>
            <div className={styles.chips}>
              {STATUSES.map((status) => (
                <ToggleChip
                  key={status}
                  pressed={state.status.includes(status)}
                  onToggle={() => set({ status: toggle(state.status, status), page: 1 })}
                >
                  {STATUS_LABEL[status]}
                </ToggleChip>
              ))}
            </div>
          </fieldset>
          <p className={styles.muted}>
            Highest MRR orders within one currency only, since amounts in two currencies are not
            comparable: choose a currency first.
          </p>
        </section>

        <h2 id={TITLE_ID} tabIndex={-1} className="sr">
          Account list
        </h2>
        {listing.isPending ? (
          <Skeleton label="Loading the accounts" rows={6} rowHeight={48} />
        ) : listing.isError ? (
          listing.error instanceof ApiError && listing.error.status === 422 ? (
            <div role="alert" className={styles.stack}>
              <p className={styles.strong}>The server refused these filters.</p>
              <Refusal error={listing.error} fallback={null} />
              <div className={styles.row}>
                <button type="button" className={styles.link} onClick={clear}>
                  Clear the filters
                </button>
              </div>
            </div>
          ) : (
            <ErrorState onRetry={() => void listing.refetch()} focusTo={TITLE_ID}>
              The accounts could not be loaded. The local server may be unreachable, or its V2
              preview switch may be off.
            </ErrorState>
          )
        ) : (
          <Results
            listing={listing.data}
            stale={listing.isPlaceholderData}
            filtered={filtered}
            onClear={clear}
            onPage={(page) => set({ page })}
            onPageSize={(page_size) => set({ page_size, page: 1 })}
          />
        )}
      </Page>
    </>
  );
}

/** The usual windows, and the address's own if it is another. */
function windowsWith(days: number | null): number[] {
  const windows: number[] = [...RENEWAL_WINDOWS];
  if (days !== null && !windows.includes(days)) windows.push(days);
  return windows.sort((a, b) => a - b);
}

function Results({
  listing,
  stale,
  filtered,
  onClear,
  onPage,
  onPageSize,
}: {
  listing: Listing;
  stale: boolean;
  filtered: boolean;
  onClear: () => void;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  if (listing.total === 0) {
    return (
      <EmptyState
        title="No account matches"
        {...(filtered ? { action: { label: 'Clear the filters', onClick: onClear } } : {})}
      >
        {filtered ? 'No synthetic account matches every filter chosen.' : 'The dataset is empty.'}
      </EmptyState>
    );
  }
  return (
    <Card titleId={TITLE_ID} variant="flush">
      <div className={styles.tableWrap} aria-busy={stale}>
        <table className={styles.table}>
          <caption>
            {plural(listing.total, 'account')} match. Levels, order and amounts are the API’s.
          </caption>
          <thead>
            <tr>
              <th scope="col">Account</th>
              <th scope="col">Level</th>
              <th scope="col">Signals</th>
              <th scope="col">Status</th>
              <th scope="col" className={styles.num}>
                MRR
              </th>
              <th scope="col">Renewal</th>
              <th scope="col">Where to look</th>
            </tr>
          </thead>
          <tbody>
            {listing.rows.map((row) => (
              <Row key={row.account_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={listing.page}
        pages={listing.pages}
        onPage={onPage}
        note={`Page ${count(listing.page)} of ${count(listing.pages)} · ${plural(listing.total, 'account')}`}
        pageSize={{ value: listing.page_size, options: PAGE_SIZES, onChange: onPageSize }}
      />
      <div className={styles.inset}>
        <Notes notes={listing.notes} />
      </div>
    </Card>
  );
}

function Row({ row }: { row: AccountRow }) {
  const { indicators } = row.assessment;
  return (
    <tr>
      <th scope="row">
        <Link
          to={`/v2/account?id=${encodeURIComponent(row.account_id)}`}
          className={`${styles.link} ${styles.strong}`}
        >
          {row.account_name ?? row.account_id}
        </Link>
        <span className={`${styles.cellNote} ${styles.mono}`}>{row.account_id}</span>
        {row.segment && <span className={styles.cellNote}>{row.segment}</span>}
      </th>
      <td>
        <LevelBadge level={row.assessment.level} />
      </td>
      <td>
        {indicators.length === 0 ? (
          <span className={styles.muted}>
            {row.assessment.eligibility === 'not_assessed' ? 'Not assessed' : 'None'}
          </span>
        ) : (
          <ul aria-label="Signals" className={styles.signalList}>
            {indicators.map((indicator) => (
              <li key={indicator.code}>{indicator.title}</li>
            ))}
          </ul>
        )}
      </td>
      <td>{STATUS_LABEL[row.status]}</td>
      <td className={styles.num}>{amount(row.mrr, row.currency)}</td>
      <td>
        {row.renewal_date ? shortDate(row.renewal_date) : '—'}
        {row.days_to_renewal !== null && (
          <span className={styles.cellNote}>in {plural(row.days_to_renewal, 'day')}</span>
        )}
      </td>
      <td className={styles.context}>{row.next_step_context}</td>
    </tr>
  );
}

interface NativeOption {
  value: string;
  label: string;
  disabled?: boolean;
}

function NativeSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly NativeOption[];
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <span className={styles.label}>
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className={styles.control}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </span>
  );
}

/** The exact segment name the API filters by, written once typing pauses. */
function SegmentField({ value, onSettle }: { value: string; onSettle: (value: string) => void }) {
  const [text, setText] = useSettledDraft(value, onSettle);
  const id = useId();
  return (
    <span className={styles.label}>
      <label htmlFor={id}>Segment (exact name)</label>
      <input
        id={id}
        className={styles.control}
        value={text}
        maxLength={64}
        placeholder="e.g. Enterprise"
        autoComplete="off"
        onChange={(event) => setText(event.target.value)}
      />
    </span>
  );
}
