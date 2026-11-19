/**
 * "Where the book sits on the probability scale": the histogram, and a card
 * per band. A bin or a band card opens the retention queue tab on that band.
 */
import { useId, useState } from 'react';
import type { ModelInfo, Overview } from '../../api/queries';
import { Link } from '../../app/router';
import { Card, CardHeader } from '../../components/ui/Surface';
import { Segmented } from '../../components/ui/Toggle';
import { int, moneyK, pct } from '../../lib/format';
import { cx } from '../../lib/style';
import { bandRange } from './copy';
import { Histogram, type Measure } from './Histogram';
import styles from './Distribution.module.css';

interface DistributionProps {
  book: Overview;
  info: ModelInfo | undefined;
  /** The queue tab's address on one band. */
  bandLink: (band: string) => string;
  /** Open the queue tab on one band (a bin chosen in the histogram). */
  onPickBand: (band: string) => void;
  /** Following a band card's link: the page then focuses the queue. */
  onFollow: () => void;
  className?: string | undefined;
}

export function Distribution({
  book,
  info,
  bandLink,
  onPickBand,
  onFollow,
  className,
}: DistributionProps) {
  const [measure, setMeasure] = useState<Measure>('customers');
  return (
    <Card titleId="distribution" className={cx(styles.card, className)}>
      <CardHeader
        id="distribution"
        title="Where the book sits on the probability scale"
        subtitle={
          book.withheld_reason === null
            ? `How is risk spread across ${int(book.customers)} customers, and how do stated probabilities compare with validation outcomes?`
            : `How is risk spread across ${int(book.customers)} customers?`
        }
        aside={
          <Segmented
            label="Measure"
            value={measure}
            onChange={setMeasure}
            options={[
              { value: 'customers', label: 'Customers' },
              { value: 'charges', label: 'Monthly charges, all statuses' },
            ]}
          />
        }
      />
      <Histogram book={book} measure={measure} onPickBand={onPickBand} />
      <div className={styles.bands}>
        {book.bands.map((band) => (
          <BandCard
            key={band.name}
            band={band}
            measure={measure}
            withheld={book.withheld_reason !== null}
            to={bandLink(band.name)}
            onFollow={onFollow}
          />
        ))}
      </div>
      <p className={styles.note}>
        {info?.calibration_method === 'isotonic' &&
          "The isotonic step shows: probabilities bunch at the calibrator's levels. "}
        {book.withheld_reason === null &&
          'Validation customers informed the cut and the calibration choice, so this is not an independent test. Held-out figures appear only on Performance, once recorded. '}
        Select a bin or a band to see its flagged customers in the retention queue.
      </p>
    </Card>
  );
}

interface BandCardProps {
  band: Overview['bands'][number];
  measure: Measure;
  withheld: boolean;
  to: string;
  onFollow: () => void;
}

/**
 * A band's figures, which are also its accessible name; where following it
 * goes is its description, so a screen reader hears the figures first.
 */
function BandCard({ band, measure, withheld, to, onFollow }: BandCardProps) {
  const share = measure === 'customers' ? band.share : band.monthly_share;
  const churned = band.observed?.rate;
  const action = useId();
  return (
    <Link
      to={to}
      className={styles.band}
      data-band={band.name}
      aria-describedby={action}
      onClick={onFollow}
    >
      {/* Out of the name (aria-hidden), still the description by reference. */}
      <span id={action} className="sr" aria-hidden="true">
        Shows its flagged customers in the retention queue.
      </span>
      <span className={styles.bandHead}>
        <span className={styles.bandName}>{band.name}</span>{' '}
        <span className={styles.range}>{bandRange(band)}</span>
      </span>{' '}
      <span className={styles.bandValue}>
        <span className={styles.value}>
          {measure === 'customers' ? int(band.customers) : moneyK(band.monthly_charges)}
        </span>{' '}
        <span className={styles.share}>{share === null ? '—' : pct(share)}</span>
      </span>{' '}
      <span className={styles.bandFoot}>
        <span>
          churned, train + val{' '}
          <b className={styles.figure}>
            {withheld ? 'withheld' : churned === null || churned === undefined ? '—' : pct(churned)}
          </b>
        </span>{' '}
        <span>
          flagged{' '}
          <b className={cx(styles.figure, band.flagged === 0 && styles.none)}>
            {band.flagged === 0 ? 'none' : int(band.flagged)}
          </b>
        </span>
      </span>
    </Link>
  );
}
