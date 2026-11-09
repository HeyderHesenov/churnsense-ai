/**
 * Reading the recommendation: why it is not simply the peak, and how it moves
 * with the one assumption the data cannot inform. Both in the response's own
 * figures; a success rate the API tried can be applied in one step.
 */
import type { Sweep } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { Card, CardHeader, SimulatedBadge, Tag } from '../../components/ui/Surface';
import { cut, pct, signedMoney } from '../../lib/format';
import { cx } from '../../lib/style';
import { sensitivityNote, whyNotPeak } from './copy';
import { ForAssumptions } from './ForAssumptions';
import { readField, type Range } from './simulator';
import styles from './Simulator.module.css';

export function WhyNotPeak({ sweep, stale }: { sweep: Sweep; stale: boolean }) {
  return (
    <Card titleId="peak-title" className={cx(styles.reading, stale && styles.stale)}>
      <CardHeader id="peak-title" title="Why not simply take the peak?" level={2} />
      <ForAssumptions echo={sweep.assumptions} stale={stale} />
      <p className={styles.readingText}>{whyNotPeak(sweep)}</p>
    </Card>
  );
}

interface SensitivityProps {
  sweep: Sweep;
  /** The success rates the API accepts, as served. */
  range: Range;
  /** The rows are for the assumptions set; otherwise they are marked, and cannot be applied. */
  current: boolean;
  onApply: (rate: number) => void;
}

export function Sensitivity({ sweep, range, current, onApply }: SensitivityProps) {
  const currency = sweep.assumptions.currency;
  const note = sensitivityNote(sweep.sensitivity);
  // A rate the served bounds refuse could not be sent, so it is not offered.
  const rows = sweep.sensitivity.map((row) => ({
    row,
    allowed: readField(String(row.offer_success_rate), range, 2).problem === null,
    yours: row.offer_success_rate === sweep.assumptions.offer_success_rate,
  }));
  return (
    <Card titleId="sensitivity-title" className={cx(styles.reading, !current && styles.stale)}>
      <CardHeader
        id="sensitivity-title"
        title="If the success rate were…"
        subtitle="The API’s recommended cut at each rate, the other assumptions as named."
        aside={<SimulatedBadge />}
      />
      <ForAssumptions echo={sweep.assumptions} stale={!current} />
      <ul className={styles.rates}>
        {rows.map(({ row, allowed, yours }) => {
          const usable = current && allowed && !yours;
          const why = !current
            ? 'rates-stale'
            : yours
              ? 'rates-yours'
              : allowed
                ? undefined
                : 'rates-bounds';
          const rate = pct(row.offer_success_rate, 0);
          return (
            <li key={row.offer_success_rate} className={styles.rate}>
              <span className={styles.rateName}>
                {rate}
                {yours && current && <Tag>YOURS</Tag>}
              </span>
              <span className={styles.rateCut}>cut {cut(row.threshold)}</span>
              <span className={styles.rateNet}>{signedMoney(row.net_benefit, currency)}</span>
              <Button
                size="xs"
                variant="ghost"
                aria-label={`Apply ${rate} success`}
                aria-disabled={!usable}
                aria-describedby={why}
                onClick={() => {
                  if (usable) onApply(row.offer_success_rate);
                }}
              >
                Apply
              </Button>
            </li>
          );
        })}
      </ul>
      {note && <p className={styles.readingNote}>{note}</p>}
      {current && rows.some(({ yours }) => yours) && (
        <p id="rates-yours" className="sr">
          The rate tagged YOURS is the one set.
        </p>
      )}
      {!current && (
        <p id="rates-stale" className={cx(styles.readingNote, styles.why)}>
          A rate can be applied once the figures are for the assumptions set.
        </p>
      )}
      {current && rows.some(({ allowed }) => !allowed) && (
        <p id="rates-bounds" className={cx(styles.readingNote, styles.why)}>
          A rate outside the served bounds cannot be applied.
        </p>
      )}
    </Card>
  );
}
