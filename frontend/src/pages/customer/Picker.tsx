/**
 * The customers the page can show, as links: each card is an address, so a
 * choice is a history entry and a card opens in a new tab like any link. The
 * one shown is the current page. A card's figures are the API's: an example
 * comes with its detail, the README example with its prediction.
 */
import { ApiError } from '../../api/client';
import { useCustomer, type CustomerDetail, type Prediction } from '../../api/queries';
import { Link } from '../../app/router';
import { probabilityPct } from '../../lib/format';
import { cssVars, cx } from '../../lib/style';
import { cardMeta, LIVE_META, README_META } from './copy';
import { choiceKey, type Shown } from './customer';
import type { Score } from './Prediction';
import styles from './Customer.module.css';

interface PickerProps {
  choices: readonly Shown[];
  shown: Shown;
  href: (choice: Shown) => string;
  readmeName: string;
  readme: Prediction | undefined;
  /** Whether the README example's prediction failed, rather than still loading. */
  readmeFailed: boolean;
  examples: readonly CustomerDetail[];
  /** The new customer's figures, once they are in for what was typed. */
  live: Score | undefined;
}

export function Picker({
  choices,
  shown,
  href,
  readmeName,
  readme,
  readmeFailed,
  examples,
  live,
}: PickerProps) {
  const current = choiceKey(shown);
  return (
    <nav aria-label="Customers">
      <ul className={styles.picker}>
        {choices.map((choice) => {
          const key = choiceKey(choice);
          const common = { to: href(choice), current: key === current };
          return (
            <li key={key}>
              {choice.kind === 'readme' ? (
                <Card
                  {...common}
                  name={readmeName}
                  score={
                    readme && { probability: readme.churn_probability, band: readme.risk_band }
                  }
                  meta={!readme && readmeFailed ? 'not loaded' : README_META}
                />
              ) : choice.kind === 'live' ? (
                <Card
                  {...common}
                  name="New customer"
                  score={live}
                  pending="live"
                  pendingBand="Edit any input"
                  meta={LIVE_META}
                  isLive
                />
              ) : (
                <BookCard
                  {...common}
                  id={choice.id}
                  example={examples.find((c) => c.id === choice.id)}
                />
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** A customer in the book: from the examples, or asked for (one a link opened). */
function BookCard({
  id,
  example,
  ...common
}: {
  to: string;
  current: boolean;
  id: string;
  example: CustomerDetail | undefined;
}) {
  const asked = useCustomer(example ? null : id);
  const detail = example ?? asked.data;
  return (
    <Card
      {...common}
      name={id}
      score={detail && { probability: detail.churn_probability, band: detail.risk_band }}
      meta={
        detail
          ? cardMeta(detail)
          : asked.error instanceof ApiError && asked.error.status === 404
            ? 'not in the book'
            : asked.isError
              ? 'not loaded'
              : '…'
      }
    />
  );
}

interface CardProps {
  to: string;
  current: boolean;
  name: string;
  score: { probability: number; band: string } | undefined;
  meta: string;
  /** What stands for the figure and the band before they are in. */
  pending?: string;
  pendingBand?: string;
  isLive?: boolean;
}

function Card({
  to,
  current,
  name,
  score,
  meta,
  pending = '…',
  pendingBand = '…',
  isLive = false,
}: CardProps) {
  return (
    <Link
      to={to}
      aria-current={current ? 'page' : undefined}
      className={cx(styles.card, isLive && styles.liveCard)}
      data-band={score?.band}
    >
      <span className={styles.cardTop}>
        <span className={styles.cardName}>{name}</span>{' '}
        <span className={styles.cardValue}>
          {score ? probabilityPct(score.probability) : pending}
        </span>
      </span>
      <span className={styles.cardTrack} aria-hidden="true">
        <span
          className={styles.cardFill}
          style={cssVars({ '--w': score ? score.probability : 0 })}
        />
      </span>{' '}
      <span className={styles.cardFoot}>
        <span className={styles.cardBand}>
          <span className={styles.swatch} aria-hidden="true" />
          {score ? score.band : pendingBand}
        </span>{' '}
        <span>{meta}</span>
      </span>
    </Link>
  );
}
