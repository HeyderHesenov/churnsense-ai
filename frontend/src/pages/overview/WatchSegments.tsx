/**
 * "Segments to watch": the groups with the highest observed churn in the
 * contract-by-tenure breakdown of the training partition, as the API ranks
 * them (``/api/segments``, the churn patterns page's default view, so the two
 * share one request). The API decides which groups are reported and in what
 * order; the card shows the first few and computes nothing.
 */
import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { useSegments } from '../../api/queries';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { int, pct, times } from '../../lib/format';
import { cellName } from '../patterns/copy';
import { watchNote } from './copy';
import { Meter } from './Meter';
import { scaleTop } from './scale';
import styles from './WatchSegments.module.css';

/** How many of the API's ranked groups the card shows. */
export const WATCHED = 4;

/** The breakdown the card reads: the churn patterns page's default view. */
export const WATCH_VIEW = { rows: 'Contract', cols: 'tenure' } as const;

/** The card's frame: its heading (with the partition tag once the API answers) and the patterns link. */
function WatchCard({
  tag,
  className,
  children,
}: {
  tag?: ReactNode;
  className?: string | undefined;
  children: ReactNode;
}) {
  return (
    <Card titleId="watch" className={className}>
      <CardHeader
        id="watch"
        focusable
        title="Segments to watch"
        subtitle="Highest observed churn, contract by tenure"
        aside={tag}
      />
      {children}
      <Link to="/patterns" className={styles.more}>
        All patterns
        <icons.arrowRight size={14} />
      </Link>
    </Card>
  );
}

function withheldNote(reason: string | null) {
  return (
    <p className={styles.note}>Observed outcomes are withheld{reason ? `: ${reason}` : '.'}</p>
  );
}

/**
 * While the overview already says outcomes are withheld, the card says so and
 * asks the API for nothing: the segments would be refused.
 */
export function WatchSegments({
  withheld,
  className,
}: {
  withheld: string | null;
  className?: string | undefined;
}) {
  if (withheld !== null)
    return <WatchCard className={className}>{withheldNote(withheld)}</WatchCard>;
  return <WatchedGroups className={className} />;
}

function WatchedGroups({ className }: { className?: string | undefined }) {
  const segments = useSegments(WATCH_VIEW.rows, WATCH_VIEW.cols);
  const data = segments.data;
  const groups = data?.ranked.slice(0, WATCHED) ?? [];
  const top = scaleTop(groups.map((cell) => cell.rate));
  const refused = segments.error instanceof ApiError && segments.error.code === 'outcomes_withheld';
  return (
    <WatchCard
      className={className}
      tag={data && <Tag>TRAIN · {int(data.partition_customers)}</Tag>}
    >
      {refused ? (
        withheldNote(null)
      ) : segments.isPending ? (
        <Skeleton label="Loading the segments to watch" rows={WATCHED} rowHeight={34} />
      ) : segments.isError || !data ? (
        <ErrorState inline onRetry={() => void segments.refetch()} focusTo="watch">
          The segments could not be loaded.
        </ErrorState>
      ) : groups.length === 0 ? (
        <p className={styles.note}>
          No group in this breakdown has enough customers for a rate to be shown.
        </p>
      ) : (
        <>
          <ol className={styles.list}>
            {groups.map((cell) => (
              <li key={`${cell.row}|${cell.column ?? ''}`} className={styles.row}>
                <span className={styles.head}>
                  <span className={styles.name}>{cellName(WATCH_VIEW, cell)}</span>
                  <span className={styles.rate}>{pct(cell.rate)}</span>
                </span>
                <Meter value={cell.rate / top} tone="risk" thin />
                <span className={styles.meta}>
                  {cell.lift === null ? '' : `${times(cell.lift, 1)} avg · `}n {int(cell.n)}
                </span>
              </li>
            ))}
          </ol>
          <p className={styles.note}>{watchNote(data.min_customers)}</p>
        </>
      )}
    </WatchCard>
  );
}
