/**
 * "Risk at a glance": the book by risk band as a ring (each band's share of
 * customers, as the API gives it), everyone flagged as an inner ring, and a
 * legend with each band's range, count and observed churn.
 */
import type { Overview } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { icons } from '../../components/icons';
import { BandSwatch } from '../../components/ui/Badges';
import { Card, CardHeader } from '../../components/ui/Surface';
import { Link } from '../../app/router';
import { cut, int, pct } from '../../lib/format';
import { bandRange, donutDescription, observedShort } from './copy';
import { arcs } from './scale';
import styles from './RiskDonut.module.css';

/** The gap between two band arcs, in hundredths of the ring. */
const GAP = 0.8;

interface RiskDonutProps {
  book: Overview;
  /** The risk distribution tab's address. */
  more: string;
  className?: string | undefined;
}

export function RiskDonut({ book, more, className }: RiskDonutProps) {
  const bandArcs = arcs(
    book.bands.map((band) => band.share),
    GAP,
  );
  const [flaggedArc] = arcs([book.flagged.share], 0);
  const withheld = book.withheld_reason !== null;
  return (
    <Card titleId="glance" className={className}>
      <CardHeader id="glance" title="Risk at a glance" subtitle="Every customer, by risk band" />
      <ChartFrame
        className={styles.chart}
        label={donutDescription(book)}
        table={{
          caption: 'Customers by risk band',
          columns: [
            'Band',
            'Probability',
            'Customers',
            'Share',
            'Observed churn, train + validation',
          ],
          rows: book.bands.map((band) => [
            band.name,
            bandRange(band),
            int(band.customers),
            band.share === null ? '—' : pct(band.share),
            observedShort(band, withheld),
          ]),
        }}
      >
        <div className={styles.ring}>
          <svg viewBox="0 0 200 200" className={styles.svg}>
            <circle className={styles.track} cx={100} cy={100} r={82} />
            {book.bands.map((band, i) => {
              const arc = bandArcs[i];
              return arc ? (
                <circle
                  key={band.name}
                  data-band={band.name}
                  className={styles.band}
                  cx={100}
                  cy={100}
                  r={82}
                  pathLength={100}
                  strokeDasharray={arc.dash}
                  strokeDashoffset={arc.offset}
                  transform="rotate(-90 100 100)"
                />
              ) : null;
            })}
            <circle className={styles.innerTrack} cx={100} cy={100} r={62} />
            {flaggedArc && (
              <circle
                className={styles.flagged}
                cx={100}
                cy={100}
                r={62}
                pathLength={100}
                strokeDasharray={flaggedArc.dash}
                transform="rotate(-90 100 100)"
              />
            )}
          </svg>
          <div className={styles.centre} aria-hidden="true">
            <span className={styles.count}>{int(book.flagged.customers)}</span>
            <span className={styles.at}>flagged at {cut(book.threshold)}</span>
            {book.flagged.share !== null && (
              <span className={styles.share}>
                {pct(book.flagged.share)}
                <span className={styles.at}>of the book</span>
              </span>
            )}
          </div>
        </div>
      </ChartFrame>
      <ul className={styles.legend}>
        {book.bands.map((band) => (
          <li key={band.name} data-band={band.name}>
            <BandSwatch band={band.name} />
            <span className={styles.name}>{band.name}</span>
            <span className={styles.range}>{bandRange(band)}</span>
            <span className={styles.figure}>{int(band.customers)}</span>
            <span className={styles.left}>{observedShort(band, withheld)}</span>
          </li>
        ))}
      </ul>
      <p className={styles.note}>Observed churn counts train and validation customers only.</p>
      <Link to={more} className={styles.more}>
        Explore the full distribution
        <icons.arrowRight size={14} />
      </Link>
    </Card>
  );
}
