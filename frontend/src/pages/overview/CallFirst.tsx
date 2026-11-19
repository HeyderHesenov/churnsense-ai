/**
 * "Customers to call first": the first three customers of the retention
 * queue as it opens (every flagged band, most monthly revenue at risk first).
 * It reads the queue tab's own first page - one listing, shared with that tab -
 * so the order is the server's and the card never asks for a customer of its
 * own. It asks for no explanation: an explanation costs a turn of the rate
 * limit, so attributions are asked for only where the queue shows them, once
 * its tab is opened.
 */
import { useCustomers, type CustomerRow, type Overview } from '../../api/queries';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import { BandLabel } from '../../components/ui/Badges';
import { SyntheticTag } from '../../components/ui/DataSourceNote';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { Card, CardHeader } from '../../components/ui/Surface';
import { cents, cut, probability } from '../../lib/format';
import { profile } from '../../lib/labels';
import { DEFAULT_QUEUE, defaultBands, queueQuery } from './queue';
import { arcs } from './scale';
import styles from './CallFirst.module.css';

/** How many of the queue's first page the card shows. */
export const CALL_FIRST = 3;

interface CallFirstProps {
  book: Overview;
  /** The retention queue tab's address. */
  queue: string;
  onQueue: () => void;
  className?: string | undefined;
}

export function CallFirst({ book, queue, onQueue, className }: CallFirstProps) {
  const defaults = defaultBands(book);
  const listing = useCustomers(queueQuery(DEFAULT_QUEUE, defaults));
  const page = listing.data;
  const rows = page?.rows.slice(0, CALL_FIRST) ?? [];

  return (
    <Card titleId="call-first" className={className}>
      <CardHeader
        id="call-first"
        focusable
        title={
          <>
            Customers to call first <SyntheticTag />
          </>
        }
        subtitle="The retention queue’s first three: flagged, most monthly revenue at risk"
      />
      {defaults.length === 0 ? (
        <EmptyState title="Nobody is flagged">
          No customer reaches the {cut(book.threshold)} threshold.
        </EmptyState>
      ) : listing.isPending ? (
        <Skeleton label="Loading the customers to call first" rows={1} rowHeight={150} />
      ) : listing.isError || !page ? (
        <ErrorState inline onRetry={() => void listing.refetch()} focusTo="call-first">
          The customers to call first could not be loaded.
        </ErrorState>
      ) : rows.length === 0 ? (
        <EmptyState title="Nobody is in the queue">
          Everyone flagged is recorded as churned; the explorer still lists them.
        </EmptyState>
      ) : (
        <>
          <ol className={styles.cards}>
            {rows.map((row) => (
              <li key={row.id}>
                <CustomerCard row={row} />
              </li>
            ))}
          </ol>
          <p className={styles.explanations}>
            <Link to={queue} className={styles.all} onClick={onQueue}>
              View explanations in Retention Queue
              <icons.arrowRight size={14} />
            </Link>
          </p>
        </>
      )}
    </Card>
  );
}

function CustomerCard({ row }: { row: CustomerRow }) {
  const [arc] = arcs([row.churn_probability], 0);
  return (
    <Link
      to={`/customer?${new URLSearchParams({ id: row.id }).toString()}`}
      className={styles.card}
      data-band={row.risk_band}
    >
      <span className={styles.head}>
        <span className={styles.avatar} aria-hidden="true">
          {row.risk_band.charAt(0)}
        </span>
        <span className={styles.who}>
          <span className={styles.id}>{row.id}</span>
          <span className={styles.profile}>
            {profile(row.contract, row.internet_service, row.payment_method)}
          </span>
        </span>
        <svg className={styles.ring} viewBox="0 0 40 40" aria-hidden="true">
          <circle className={styles.ringTrack} cx={20} cy={20} r={16} />
          {arc && (
            <circle
              className={styles.ringArc}
              cx={20}
              cy={20}
              r={16}
              pathLength={100}
              strokeDasharray={arc.dash}
              transform="rotate(-90 20 20)"
            />
          )}
        </svg>
      </span>
      <span className={styles.score}>
        <span className={styles.probability}>{probability(row.churn_probability)}</span>{' '}
        <BandLabel band={row.risk_band} inked />
        {row.status === 'unknown' && (
          <>
            {' '}
            <span className={styles.status}>Status unknown</span>
          </>
        )}
      </span>
      <span className={styles.foot}>
        <span>
          {row.tenure} mo · {cents(row.monthly_charges)}/mo
        </span>{' '}
        <span className={styles.atRisk}>
          {row.at_risk === null ? 'at risk unknown' : `${cents(row.at_risk)} at risk / mo`}
        </span>
      </span>
    </Link>
  );
}
