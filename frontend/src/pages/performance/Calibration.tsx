/**
 * Does a probability mean what it says? The served model's validation
 * customers in tenths of probability, each bin's observed churn against its
 * mean stated probability, beside the diagonal where the two are equal. A bin
 * of fewer than 20 customers has no observed rate and is not drawn. Below it, training's record: the uncalibrated candidate and the
 * served model at a cut of 0.50, and why calibration was kept (or not).
 */
import type { Performance } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { int } from '../../lib/format';
import {
  AT_HALF_CAVEAT,
  calibrationSentence,
  ECE_NOTE,
  notDrawn,
  SMALL,
  UNAVAILABLE,
  withheld,
} from './copy';
import { binLabel, REL, relX, relY, score, type Bin } from './performance';
import styles from './Performance.module.css';

const TICKS = [0, 0.2, 0.4, 0.6, 0.8, 1];

function Reliability({ bins }: { bins: readonly Bin[] }) {
  const drawn = bins.filter((bin): bin is Bin & { rate: number } => bin.rate !== null);
  const line = drawn
    .map(
      (bin, i) =>
        `${i === 0 ? 'M' : 'L'}${relX(bin.mean_predicted).toFixed(1)} ${relY(bin.rate).toFixed(1)}`,
    )
    .join(' ');
  const hidden = bins.length - drawn.length;
  return (
    <ChartFrame
      className={styles.reliability}
      label={
        `Observed churn against mean stated probability in ${int(drawn.length)} validation bins` +
        (hidden
          ? `; ${int(hidden)} with too few customers ${hidden === 1 ? 'is' : 'are'} not drawn.`
          : '.')
      }
      table={{
        caption: 'Observed churn by probability bin, validation',
        columns: ['Bin', 'Customers', 'Mean stated', 'Observed'],
        rows: bins.map((bin) => [
          binLabel(bin),
          int(bin.customers),
          score(bin.mean_predicted),
          bin.rate === null ? SMALL : score(bin.rate),
        ]),
      }}
      legend={
        <>
          <span className={styles.legendItem}>
            <span className={styles.binKey} aria-hidden="true" />
            validation bin: mean stated against observed
          </span>
          <span className={styles.legendItem}>
            <span className={styles.diagonalKey} aria-hidden="true" />
            stated equals observed
          </span>
        </>
      }
    >
      <svg viewBox={`0 0 ${REL.width} ${REL.height}`}>
        {TICKS.map((t) => (
          <g key={t}>
            <line className={styles.grid} x1={relX(t)} x2={relX(t)} y1={REL.top} y2={REL.bottom} />
            <line className={styles.grid} x1={REL.left} x2={REL.right} y1={relY(t)} y2={relY(t)} />
            <text className={styles.tick} x={relX(t)} y={REL.bottom + 16} textAnchor="middle">
              {t === 0 ? '0' : t.toFixed(1)}
            </text>
            <text className={styles.tick} x={REL.left - 8} y={relY(t) + 4} textAnchor="end">
              {t === 0 ? '0' : t.toFixed(1)}
            </text>
          </g>
        ))}
        <line className={styles.diagonal} x1={relX(0)} y1={relY(0)} x2={relX(1)} y2={relY(1)} />
        <path className={styles.relLine} d={line} />
        {drawn.map((bin) => (
          <circle
            key={bin.low}
            className={styles.binMark}
            cx={relX(bin.mean_predicted)}
            cy={relY(bin.rate)}
            r={5}
          >
            <title>
              {`${binLabel(bin)}: ${int(bin.customers)} customers, stated ${score(bin.mean_predicted)}, observed ${score(bin.rate)}`}
            </title>
          </circle>
        ))}
        <text
          className={styles.axisTitle}
          x={(REL.left + REL.right) / 2}
          y={REL.height - 2}
          textAnchor="middle"
        >
          Mean stated probability
        </text>
        <text
          className={styles.axisTitle}
          transform={`translate(10 ${(REL.top + REL.bottom) / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          Observed churn rate
        </text>
      </svg>
    </ChartFrame>
  );
}

export function Calibration({ figures }: { figures: Performance }) {
  const calibration = figures.calibration;
  const measured = figures.validation_evaluation;
  const rows: { label: string; key: 'precision' | 'recall' | 'brier' }[] = [
    { label: 'Precision', key: 'precision' },
    { label: 'Recall', key: 'recall' },
    { label: 'Brier', key: 'brier' },
  ];
  const at = (side: 'uncalibrated' | 'calibrated', key: 'precision' | 'recall' | 'brier') => {
    const figuresAt = calibration[side];
    return figuresAt ? score(figuresAt[key]) : '—';
  };

  return (
    <Card titleId="calibration-title" className={styles.card}>
      <CardHeader
        id="calibration-title"
        title="Does a probability mean what it says?"
        subtitle="Observed churn against the mean stated probability, for validation customers in tenths of probability."
        aside={<Tag>VALIDATION</Tag>}
      />
      {measured ? (
        <>
          <Reliability bins={measured.reliability} />
          {notDrawn(measured.reliability) && (
            <p className={styles.note}>{notDrawn(measured.reliability)}</p>
          )}
          <p className={styles.note}>
            ECE {score(measured.ece)}. {ECE_NOTE}
          </p>
        </>
      ) : (
        <p className={styles.unavailable}>
          {figures.withheld_reason ? withheld(figures.withheld_reason) : UNAVAILABLE}
        </p>
      )}
      <table className={styles.halfTable}>
        <caption className={styles.tableCaption}>At a cut of 0.50</caption>
        <thead>
          <tr>
            <td />
            <th scope="col">Uncalibrated</th>
            <th scope="col">Served</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              <td>{at('uncalibrated', row.key)}</td>
              <td className={styles.servedCell}>{at('calibrated', row.key)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.note}>{AT_HALF_CAVEAT}</p>
      <p className={styles.sentence}>{calibrationSentence(calibration)}</p>
    </Card>
  );
}
