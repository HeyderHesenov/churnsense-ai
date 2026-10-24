/**
 * The top of the overview: what the page is, the operating point on the
 * band scale, and the five key figures (two of which open the queue).
 */
import type { ModelInfo, Overview } from '../../api/queries';
import { icons } from '../../components/icons';
import { ButtonLink } from '../../components/ui/Button';
import { PageHeader } from '../../components/ui/PageHeader';
import { Card, StatTile } from '../../components/ui/Surface';
import { cut, int, money, pct } from '../../lib/format';
import { cssVars } from '../../lib/style';
import { riskiestLink, scaleDescription, scaleLabel } from './copy';
import styles from './Overview.module.css';

interface SummaryProps {
  book: Overview;
  info: ModelInfo | undefined;
  /** Addresses that show the queue for the riskiest bands, and for every flagged band. */
  links: { riskiest: string; flagged: string };
  /** Bring the queue into view once a tile has set its filter. */
  onShowQueue: () => void;
}

export function Summary({ book, info, links, onShowQueue }: SummaryProps) {
  return (
    <>
      <section className={styles.intro} aria-label="Overview">
        <PageHeader
          eyebrow="Executive overview"
          title="Where retention risk sits in the book today"
          className={styles.title}
        >
          {int(book.customers)} customers, scored by one {info?.calibrated ? 'calibrated ' : ''}
          model through the same path the API uses.{' '}
          {book.withheld_reason
            ? `Observed outcomes are withheld: ${book.withheld_reason}`
            : 'Observed outcomes come from the train and validation partitions only; test outcomes stay withheld.'}
        </PageHeader>
        <OperatingPoint book={book} />
      </section>
      <KeyFigures book={book} links={links} onShowQueue={onShowQueue} />
    </>
  );
}

function OperatingPoint({ book }: { book: Overview }) {
  const threshold = cut(book.threshold);
  return (
    <Card titleId="operating-point" className={styles.operating}>
      <div className={styles.operatingHead}>
        <div>
          <h2 id="operating-point" className={styles.operatingLabel}>
            Operating point
          </h2>
          <div className={styles.operatingValue}>
            <span className={styles.threshold}>{threshold}</span>
            <span className={styles.muted}>chosen on validation, stored with the artifact</span>
          </div>
        </div>
        <ButtonLink to="/simulator" variant="ghost" size="md">
          Tune
          <icons.arrowRight size={14} />
        </ButtonLink>
      </div>
      <div
        className={styles.scale}
        role="img"
        aria-label={scaleDescription(book.bands, book.threshold)}
      >
        <div className={styles.marker} style={cssVars({ '--t': book.threshold })}>
          <span className={styles.markerLabel}>flag ≥ {threshold}</span>
          <span className={styles.markerStem} />
        </div>
        <div className={styles.bar}>
          {book.bands.map((band) => (
            <span
              key={band.name}
              data-band={band.name}
              style={cssVars({ '--w': Math.min(band.max, 1) - band.min })}
            />
          ))}
        </div>
        <div className={styles.legend}>
          {book.bands.map((band, i) => (
            <span key={band.name} style={cssVars({ '--w': Math.min(band.max, 1) - band.min })}>
              {band.name} <span className={styles.range}>{scaleLabel(band, i)}</span>
            </span>
          ))}
        </div>
      </div>
      <p className={styles.note}>
        Bands are fixed ranges, independent of the threshold: retuning it never redefines what the
        team calls Critical.
      </p>
    </Card>
  );
}

function KeyFigures({
  book,
  links,
  onShowQueue,
}: Pick<SummaryProps, 'book' | 'links' | 'onShowQueue'>) {
  const threshold = cut(book.threshold);
  const share = (segment: Overview['flagged']) =>
    `${segment.share === null ? '—' : pct(segment.share)} · ${money(segment.monthly_charges)} / mo`;
  const observed = book.observed;
  return (
    <section aria-label="Key figures" className={styles.figures}>
      <StatTile
        label="Customers scored"
        value={int(book.customers)}
        sub="every row, one prediction path"
      />
      <StatTile
        label="Observed churn rate"
        value={observed?.rate == null ? 'Withheld' : pct(observed.rate)}
        sub={
          observed?.rate == null || observed.churned === null ? (
            'outcomes not shown'
          ) : (
            <>
              {int(observed.churned)} of {int(observed.customers)} ·{' '}
              <span className={styles.partitionTag}>TRAIN+VAL</span>
            </>
          )
        }
      />
      <StatTile
        label="Monthly charges"
        value={money(book.monthly_charges)}
        sub="recurring, across the book"
      />
      <StatTile
        label="High or Critical"
        value={int(book.high_or_critical.customers)}
        sub={share(book.high_or_critical)}
        link={{ to: links.riskiest, label: riskiestLink(book).label, onFollow: onShowQueue }}
      />
      <StatTile
        label={`Flagged at ${threshold}`}
        value={int(book.flagged.customers)}
        sub={share(book.flagged)}
        accent
        link={{
          to: links.flagged,
          label: `Flagged at ${threshold}: ${int(book.flagged.customers)} customers. Show them in the retention queue`,
          onFollow: onShowQueue,
        }}
      />
    </section>
  );
}
