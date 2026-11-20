/**
 * The recorded test release as one square per customer: as many caught,
 * missed, offered-to-stayers and left-alone squares as the record counts,
 * never scaled to a hundred or rounded. The legend states the record's own
 * counts; the picture adds no figure to them.
 */
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { int } from '../../lib/format';
import { OUTCOME_NAMES, WAFFLE_MISMATCH, waffleLabel, waffleSubtitle } from './copy';
import {
  OUTCOMES,
  WAFFLE_CELL,
  waffleAt,
  waffleCells,
  waffleSize,
  type Recorded,
} from './performance';
import styles from './Performance.module.css';

export function Waffle({ record }: { record: Recorded }) {
  const m = record.metrics;
  const counts = { tp: m.tp, fn: m.fn, fp: m.fp, tn: m.tn };
  const cells = waffleCells(counts);
  const { width, height } = waffleSize(cells.length);
  // A square per customer only holds if the four cells are the record's customers.
  const whole = cells.length === m.n;
  return (
    <Card titleId="waffle-title" className={styles.card}>
      <CardHeader
        id="waffle-title"
        title="Every test customer, by outcome"
        subtitle={whole ? waffleSubtitle(m.n, m.threshold) : undefined}
        aside={<Tag>TEST</Tag>}
      />
      <div className={styles.waffleLayout}>
        {whole ? (
          <svg
            role="img"
            aria-label={waffleLabel(counts, m.threshold)}
            className={styles.waffle}
            viewBox={`0 0 ${width} ${height}`}
          >
            {cells.map((outcome, i) => {
              const { x, y } = waffleAt(i);
              return (
                <rect
                  key={i}
                  data-outcome={outcome}
                  x={x}
                  y={y}
                  width={WAFFLE_CELL}
                  height={WAFFLE_CELL}
                  rx={1.2}
                />
              );
            })}
          </svg>
        ) : (
          <p className={styles.unavailable}>{WAFFLE_MISMATCH}</p>
        )}
        <dl className={styles.waffleLegend}>
          {OUTCOMES.map((outcome) => (
            <div key={outcome} data-outcome={outcome}>
              <dt>
                <span className={styles.swatch} aria-hidden="true" />
                {OUTCOME_NAMES[outcome].short}
              </dt>
              <dd>
                <span className={styles.legendCount}>{int(counts[outcome])}</span>{' '}
                <span className={styles.legendMeaning}>{OUTCOME_NAMES[outcome].long}</span>
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </Card>
  );
}
