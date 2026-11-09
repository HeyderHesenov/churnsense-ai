/**
 * "Where the book sits on the probability scale": the histogram, and a card
 * per band. A bin or a band card filters the queue to that band; pressing
 * the band shown alone brings every flagged band back.
 */
import { useId, useState } from 'react';
import type { ModelInfo, Overview } from '../../api/queries';
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
  /** The one band the queue shows, if it shows exactly one. */
  only: string | null;
  /** Show this band alone, or every flagged band again when it already is. */
  onPickBand: (band: string) => void;
}

export function Distribution({ book, info, only, onPickBand }: DistributionProps) {
  const [measure, setMeasure] = useState<Measure>('customers');
  return (
    <Card titleId="distribution" className={styles.card}>
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
      <Histogram book={book} measure={measure} only={only} onPickBand={onPickBand} />
      <div className={styles.bands}>
        {book.bands.map((band) => (
          <BandCard
            key={band.name}
            band={band}
            measure={measure}
            alone={only === band.name}
            withheld={book.withheld_reason !== null}
            onPick={() => onPickBand(band.name)}
          />
        ))}
      </div>
      <p className={styles.note}>
        {info?.calibration_method === 'isotonic' &&
          "The isotonic step shows: probabilities bunch at the calibrator's levels. "}
        {book.withheld_reason === null &&
          'Validation customers informed the cut and the calibration choice, so this is not an independent test. Held-out figures appear only on Performance, once recorded. '}
        Select a bin or a band to filter the queue.
      </p>
    </Card>
  );
}

interface BandCardProps {
  band: Overview['bands'][number];
  measure: Measure;
  alone: boolean;
  withheld: boolean;
  onPick: () => void;
}

/**
 * A band's figures, which are also its accessible name; what pressing it does
 * is its description, so a screen reader hears the figures first.
 */
function BandCard({ band, measure, alone, withheld, onPick }: BandCardProps) {
  const share = measure === 'customers' ? band.share : band.monthly_share;
  const churned = band.observed?.rate;
  const action = useId();
  return (
    <button
      type="button"
      className={styles.band}
      data-band={band.name}
      aria-pressed={alone}
      aria-describedby={action}
      onClick={onPick}
    >
      {/* Out of the name (aria-hidden), still the description by reference. */}
      <span id={action} className="sr" aria-hidden="true">
        {alone ? 'Shows every flagged band in the queue again.' : 'Filters the queue to this band.'}
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
    </button>
  );
}
