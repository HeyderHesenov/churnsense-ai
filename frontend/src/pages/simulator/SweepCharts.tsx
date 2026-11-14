/**
 * The response's sweep drawn: the simulated net at every cut, and what the
 * cut buys in precision and recall. The strip is the band of nets within one
 * offer of the peak (from ``band.floor`` to the peak's net): the cuts whose
 * nets fall in it qualify, and they need not sit side by side, so the strip
 * runs across the chart rather than between two cuts.
 *
 * The pointer layer is a shortcut for the eye and the mouse; the cut slider
 * beside the assumptions is the control, and the tables say what the
 * pictures show.
 */
import { scaleLinear } from 'd3-scale';
import { useState, type MouseEvent } from 'react';
import type { Sweep, SweepPoint } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { ChartTooltip, TipLine } from '../../components/charts/ChartTooltip';
import { Card, CardHeader, SimulatedBadge } from '../../components/ui/Surface';
import { tabledCuts } from '../../lib/cuts';
import { cut, int, moneyShort, signedMoney } from '../../lib/format';
import { cssVars, cx } from '../../lib/style';
import { precisionText, recallText } from './copy';
import { ForAssumptions, STALE_MARK } from './ForAssumptions';
import type { Selection } from './simulator';
import styles from './Simulator.module.css';

const WIDTH = 760;
const LEFT = 60;
const RIGHT = 744;
const NET = { height: 262, top: 14, bottom: 232 };
const RATES = { height: 150, top: 10, bottom: 124 };
const X_TICKS = [0, 0.2, 0.4, 0.6, 0.8, 1];

const x = scaleLinear().domain([0, 1]).range([LEFT, RIGHT]);

/** The point nearest a pointer, from where it is across the plot. */
function nearest(points: readonly SweepPoint[], event: MouseEvent<HTMLDivElement>): number {
  const box = event.currentTarget.getBoundingClientRect();
  const at = box.width > 0 ? (event.clientX - box.left) / box.width : 0;
  const threshold = Math.min(1, Math.max(0, at));
  let best = 0;
  points.forEach((point, i) => {
    const gap = Math.abs(point.threshold - threshold);
    if (gap < Math.abs((points[best]?.threshold ?? 0) - threshold)) best = i;
  });
  return best;
}

/** A line through ``points``, broken where ``value`` has nothing to draw. */
function path(
  points: readonly SweepPoint[],
  value: (point: SweepPoint) => number | null,
  y: (v: number) => number,
): string {
  let d = '';
  let drawing = false;
  for (const point of points) {
    const v = value(point);
    if (v === null) {
      drawing = false;
      continue;
    }
    d += `${drawing ? 'L' : 'M'}${x(point.threshold).toFixed(1)} ${y(v).toFixed(1)} `;
    drawing = true;
  }
  return d.trim();
}

interface ChartsProps {
  sweep: Sweep;
  selected: Selection;
  /** The figures are not for the assumptions set (they are named in the results). */
  stale: boolean;
  onPick: (index: number) => void;
}

export function SweepCharts({ sweep, selected, stale, onPick }: ChartsProps) {
  const [hover, setHover] = useState<number | null>(null);
  const points = sweep.points;
  const currency = sweep.assumptions.currency;
  const peak = points.findIndex((p) => cut(p.threshold) === cut(sweep.peak.threshold));
  const marked = [selected.recommended, peak, selected.index];
  const nets = points.map((p) => p.net_benefit);
  const y = scaleLinear()
    .domain([Math.min(0, ...nets), Math.max(0, ...nets, sweep.peak.net_benefit)])
    .range([NET.bottom, NET.top])
    .nice(4);
  const ticks = y.ticks(4);
  const rate = scaleLinear().domain([0, 1]).range([RATES.bottom, RATES.top]);
  const at = (i: number) => points[i];
  const shown = hover === null ? undefined : at(hover);
  const selectedPoint = at(selected.index);
  const recommendedPoint = at(selected.recommended);
  const peakPoint = at(peak);
  /** Every mark a cut carries in the tables: "recommended, selected". */
  const label = (i: number) =>
    [
      i === selected.recommended && 'recommended',
      i === peak && 'peak',
      i === selected.index && 'selected',
    ]
      .filter(Boolean)
      .join(', ');

  return (
    <Card titleId="sweep-title" className={cx(styles.chartCard, stale && styles.stale)}>
      <CardHeader
        id="sweep-title"
        title="Net benefit across every cut"
        subtitle="Where the campaign pays, and how flat the top is."
        aside={<SimulatedBadge />}
      />
      <ForAssumptions echo={sweep.assumptions} stale={stale} />
      <ChartFrame
        label={
          (stale ? `${STALE_MARK} ` : '') +
          `Simulated net benefit at ${int(points.length)} cuts from 0 to 1 on validation customers. ` +
          `Recommended ${recommendedPoint ? cut(recommendedPoint.threshold) : '—'}, ` +
          `peak ${peakPoint ? cut(peakPoint.threshold) : '—'}, ` +
          `selected ${selectedPoint ? cut(selectedPoint.threshold) : '—'}.`
        }
        table={{
          caption: 'Simulated net benefit by cut',
          columns: ['Cut', 'Flagged', 'Net benefit', 'Marked'],
          rows: tabledCuts(sweep.points, marked).map((p) => {
            const i = points.indexOf(p);
            return [
              cut(p.threshold),
              int(p.flagged),
              signedMoney(p.net_benefit, currency),
              label(i),
            ];
          }),
        }}
        overlay={
          <>
            {ticks.map((tick) => (
              <span
                key={tick}
                className={styles.yTick}
                style={cssVars({ '--y': y(tick) / NET.height })}
                aria-hidden="true"
              >
                {moneyShort(tick, currency)}
              </span>
            ))}
            <div
              aria-hidden="true"
              className={styles.capture}
              style={cssVars({
                '--top': NET.top / NET.height,
                '--h': (NET.bottom - NET.top) / NET.height,
              })}
              onPointerMove={(event) => setHover(nearest(points, event))}
              onPointerDown={(event) => setHover(nearest(points, event))}
              onPointerLeave={() => setHover(null)}
              onClick={(event) => onPick(nearest(points, event))}
            />
            <ChartTooltip
              visible={shown !== undefined}
              x={shown ? x(shown.threshold) / WIDTH : 0}
              y={NET.top / NET.height}
              flip={shown !== undefined && shown.threshold > 0.56}
              title={
                shown ? `${cut(shown.threshold)} · ${signedMoney(shown.net_benefit, currency)}` : ''
              }
            >
              {shown && (
                <>
                  <TipLine>
                    flagged {int(shown.flagged)} · P {precisionText(shown)}
                  </TipLine>
                  <TipLine>
                    churners flagged {int(shown.true_positives)} · R{' '}
                    {recallText(shown, sweep.churners)}
                  </TipLine>
                </>
              )}
            </ChartTooltip>
          </>
        }
        legend={
          <>
            <span className={styles.legendItem}>
              <span className={styles.netKey} aria-hidden="true" />
              simulated net benefit
            </span>
            <span className={styles.legendItem}>
              <span className={styles.bandKey} aria-hidden="true" />
              nets within one offer of the peak ({signedMoney(sweep.band.floor, currency)} to{' '}
              {signedMoney(sweep.peak.net_benefit, currency)})
            </span>
            <span className={styles.legendItem}>
              <span className={styles.selectedKey} aria-hidden="true" />
              selected cut
            </span>
            <span className={styles.legendItem}>
              <span className={styles.recKey} aria-hidden="true" />
              recommended
            </span>
            <span className={styles.legendItem}>
              <span className={styles.peakKey} aria-hidden="true" />
              peak
            </span>
            <span className={styles.legendItem}>
              <span className={styles.servedKey} aria-hidden="true" />
              served model’s cut {cut(sweep.shipped_threshold)}
            </span>
          </>
        }
      >
        <svg viewBox={`0 0 ${WIDTH} ${NET.height}`}>
          {ticks.map((tick) => (
            <line
              key={tick}
              className={styles.grid}
              x1={LEFT}
              x2={RIGHT}
              y1={y(tick)}
              y2={y(tick)}
            />
          ))}
          <rect
            className={styles.band}
            x={LEFT}
            width={RIGHT - LEFT}
            y={y(sweep.peak.net_benefit)}
            height={Math.max(0, y(sweep.band.floor) - y(sweep.peak.net_benefit))}
          />
          <line className={styles.zero} x1={LEFT} x2={RIGHT} y1={y(0)} y2={y(0)} />
          <line
            className={styles.served}
            x1={x(sweep.shipped_threshold)}
            x2={x(sweep.shipped_threshold)}
            y1={NET.top}
            y2={NET.bottom}
          />
          <path className={styles.netLine} d={path(points, (p) => p.net_benefit, y)} />
          {selectedPoint && (
            <line
              className={styles.selectedLine}
              x1={x(selectedPoint.threshold)}
              x2={x(selectedPoint.threshold)}
              y1={NET.top}
              y2={NET.bottom}
            />
          )}
          {peakPoint && (
            <circle
              className={styles.peak}
              cx={x(peakPoint.threshold)}
              cy={y(peakPoint.net_benefit)}
              r={5}
            />
          )}
          {recommendedPoint && (
            <circle
              className={styles.rec}
              cx={x(recommendedPoint.threshold)}
              cy={y(recommendedPoint.net_benefit)}
              r={6}
            />
          )}
          {shown && (
            <line
              className={styles.hoverLine}
              x1={x(shown.threshold)}
              x2={x(shown.threshold)}
              y1={NET.top}
              y2={NET.bottom}
            />
          )}
          {X_TICKS.map((tick) => (
            <text key={tick} className={styles.tick} x={x(tick)} y={252} textAnchor="middle">
              {tick === 0 ? '0' : tick.toFixed(1)}
            </text>
          ))}
        </svg>
      </ChartFrame>

      <h3 className={styles.subTitle}>What the cut buys</h3>
      <ChartFrame
        label={
          (stale ? `${STALE_MARK} ` : '') +
          'Precision and recall at each cut, measured on validation customers' +
          (selectedPoint ? `; selected ${cut(selectedPoint.threshold)}.` : '.')
        }
        table={{
          caption: 'Precision and recall by cut',
          columns: ['Cut', 'Precision', 'Recall'],
          rows: tabledCuts(sweep.points, marked).map((p) => [
            cut(p.threshold),
            precisionText(p),
            recallText(p, sweep.churners),
          ]),
        }}
        legend={
          <>
            <span className={styles.legendItem}>
              <span className={styles.precisionKey} aria-hidden="true" />
              precision (none where nothing is flagged)
            </span>
            <span className={styles.legendItem}>
              <span className={styles.recallKey} aria-hidden="true" />
              recall
            </span>
          </>
        }
      >
        <svg viewBox={`0 0 ${WIDTH} ${RATES.height}`}>
          {[0, 0.5, 1].map((tick) => (
            <g key={tick}>
              <line className={styles.grid} x1={LEFT} x2={RIGHT} y1={rate(tick)} y2={rate(tick)} />
              <text className={styles.tick} x={LEFT - 8} y={rate(tick) + 4} textAnchor="end">
                {tick === 0 ? '0' : tick.toFixed(1)}
              </text>
            </g>
          ))}
          <path
            className={styles.precisionLine}
            d={path(points, (p) => (p.flagged === 0 ? null : p.precision), rate)}
          />
          {sweep.churners > 0 && (
            <path className={styles.recallLine} d={path(points, (p) => p.recall, rate)} />
          )}
          {selectedPoint && (
            <line
              className={styles.selectedLine}
              x1={x(selectedPoint.threshold)}
              x2={x(selectedPoint.threshold)}
              y1={RATES.top}
              y2={RATES.bottom}
            />
          )}
          {X_TICKS.map((tick) => (
            <text key={tick} className={styles.tick} x={x(tick)} y={143} textAnchor="middle">
              {tick === 0 ? '0' : tick.toFixed(1)}
            </text>
          ))}
        </svg>
      </ChartFrame>
    </Card>
  );
}
