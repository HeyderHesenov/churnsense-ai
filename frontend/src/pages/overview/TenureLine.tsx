/**
 * Observed churn by tenure on the training partition: one dot per tenure
 * bucket the API reports, joined by a line that breaks at a bucket it hides
 * (too few customers), the partition's own rate as a dashed line, and the
 * median tenure of who left and who stayed. Every figure is the API's.
 */
import { useId } from 'react';
import type { Schemas } from '../../api/client';
import type { Overview } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { hiddenText, MIN_CUSTOMERS } from '../../lib/cells';
import { int, pct } from '../../lib/format';
import { level } from '../../lib/labels';
import { wholeMonths } from './copy';
import { scaleTop } from './scale';
import styles from './TenureLine.module.css';

type Bucket = Schemas['ReportedCell'] | Schemas['SuppressedCell'];

const WIDTH = 360;
const HEIGHT = 150;
const TOP = 24;
const BOTTOM = 140;

/** Where bucket ``i`` of ``n`` sits: the middle of its share of the width, as the axis labels below. */
function bucketX(i: number, n: number): number {
  return ((i + 0.5) / n) * WIDTH;
}

/** The line through the reported buckets, broken at each hidden one. */
export function tenurePath(buckets: readonly Bucket[], y: (rate: number) => number): string {
  let path = '';
  let drawing = false;
  buckets.forEach((cell, i) => {
    if (cell.suppressed) {
      drawing = false;
      return;
    }
    path += `${drawing ? 'L' : 'M'}${bucketX(i, buckets.length).toFixed(1)} ${y(cell.rate).toFixed(1)} `;
    drawing = true;
  });
  return path.trim();
}

function months(cell: Bucket): string {
  return `${level('tenure', cell.row)} months`;
}

function bucketText(cell: Bucket): string {
  return cell.suppressed
    ? hiddenText(cell, MIN_CUSTOMERS)
    : `${pct(cell.rate)} (${int(cell.churned)} of ${int(cell.n)})`;
}

export function TenureLine({
  lifecycle,
  withheld,
  className,
}: {
  lifecycle: Overview['lifecycle'];
  withheld: string | null;
  className?: string | undefined;
}) {
  const gradient = `tenure-${useId().replace(/[^A-Za-z0-9-]/g, '')}`;
  const buckets = lifecycle?.buckets ?? [];
  const top = scaleTop([
    ...buckets.map((cell) => (cell.suppressed ? null : cell.rate)),
    lifecycle?.base_rate,
  ]);
  const y = (rate: number) => BOTTOM - (rate / top) * (BOTTOM - TOP);
  const line = tenurePath(buckets, y);
  const reported = buckets.flatMap((cell, i) => (cell.suppressed ? [] : [{ cell, i }]));
  const first = reported[0];
  const last = reported[reported.length - 1];
  const area =
    first && last && line
      ? `${line} L${bucketX(last.i, buckets.length).toFixed(1)} ${BOTTOM} L${bucketX(first.i, buckets.length).toFixed(1)} ${BOTTOM} Z`
      : '';
  const continuous = reported.length === buckets.length;

  return (
    <Card titleId="tenure" className={className}>
      <CardHeader
        id="tenure"
        title="Churn by tenure"
        subtitle="Observed churn by months with the company"
        aside={
          lifecycle ? (
            <span className={styles.medians}>
              median <b>{wholeMonths(lifecycle.median_tenure_churned)}</b> mo churned ·{' '}
              <b>{wholeMonths(lifecycle.median_tenure_stayed)}</b> stayed
            </span>
          ) : undefined
        }
      />
      {!lifecycle ? (
        <p className={styles.note}>
          Observed outcomes are withheld{withheld ? `: ${withheld}` : '.'}
        </p>
      ) : (
        <>
          <ChartFrame
            label={`Churn by tenure, training partition: ${buckets.map((cell) => `${months(cell)} ${bucketText(cell)}`).join('; ')}.`}
            table={{
              caption: 'Observed churn by tenure, training partition',
              columns: ['Tenure', 'Observed churn'],
              rows: buckets.map((cell) => [months(cell), bucketText(cell)]),
            }}
            legend={
              <span className={styles.legend}>
                <Tag>TRAIN · {int(lifecycle.partition_customers)}</Tag>
                {lifecycle.base_rate !== null && (
                  <span>dashed: {pct(lifecycle.base_rate)}, the partition’s rate</span>
                )}
              </span>
            }
          >
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className={styles.svg}>
              <defs>
                <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0" className={styles.stopTop} />
                  <stop offset="1" className={styles.stopBottom} />
                </linearGradient>
              </defs>
              <line className={styles.axis} x1={0} x2={WIDTH} y1={BOTTOM} y2={BOTTOM} />
              {lifecycle.base_rate !== null && (
                <>
                  <line
                    className={styles.average}
                    x1={0}
                    x2={WIDTH}
                    y1={y(lifecycle.base_rate)}
                    y2={y(lifecycle.base_rate)}
                  />
                  <text
                    className={styles.averageLabel}
                    x={WIDTH - 2}
                    y={y(lifecycle.base_rate) - 5}
                    textAnchor="end"
                  >
                    avg {pct(lifecycle.base_rate)}
                  </text>
                </>
              )}
              {continuous && area && (
                <path className={styles.area} d={area} fill={`url(#${gradient})`} />
              )}
              {line && <path className={styles.line} d={line} pathLength={1} />}
              {buckets.map((cell, i) =>
                cell.suppressed ? (
                  <text
                    key={cell.row}
                    className={styles.hidden}
                    x={bucketX(i, buckets.length)}
                    y={BOTTOM - 8}
                    textAnchor="middle"
                  >
                    hidden
                  </text>
                ) : (
                  <g key={cell.row}>
                    <circle
                      className={styles.dot}
                      cx={bucketX(i, buckets.length)}
                      cy={y(cell.rate)}
                      r={4}
                    />
                    <text
                      className={styles.value}
                      x={bucketX(i, buckets.length)}
                      y={y(cell.rate) - 9}
                      textAnchor="middle"
                    >
                      {pct(cell.rate)}
                    </text>
                  </g>
                ),
              )}
            </svg>
          </ChartFrame>
          <div className={styles.ticks} aria-hidden="true">
            {buckets.map((cell, i) => (
              <span key={cell.row}>
                {level('tenure', cell.row)}
                {i === 0 ? ' mo' : ''}
              </span>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}
