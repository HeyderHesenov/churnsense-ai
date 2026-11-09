/**
 * Customers (or monthly charges) per 0.05 of probability, in band colours,
 * with the churn observed in each bin among validation customers against the
 * diagonal where a stated probability equals the observed rate.
 *
 * The geometry is the artboard's (a 640 x 214 box, the plot from x 40 to 600
 * and y 180 up to 30); the scales come from the data. A bin is chosen by
 * pointer or keyboard (the plot is a slider over the 20 bins), and choosing
 * it filters the queue to the bin's band.
 */
import { scaleLinear } from 'd3-scale';
import { useState, type KeyboardEvent, type MouseEvent } from 'react';
import type { Overview } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { ChartTooltip, TipLine } from '../../components/charts/ChartTooltip';
import { cut, int, money, moneyShort, pct } from '../../lib/format';
import { cssVars, cx } from '../../lib/style';
import { binIndex } from './bins';
import styles from './Histogram.module.css';

const WIDTH = 640;
const HEIGHT = 214;
const LEFT = 40;
const RIGHT = 600;
const BOTTOM = 180;
const TOP = 30;
/** A bin's slot is 28 units wide; its bar leaves 2 on each side. */
const GUTTER = 2;

export type Measure = 'customers' | 'charges';
type Bin = Overview['histogram'][number];

const x = scaleLinear().domain([0, 1]).range([LEFT, RIGHT]);
const rateY = scaleLinear().domain([0, 1]).range([BOTTOM, TOP]);

function binRange(bin: Bin): string {
  return `${cut(bin.low)}–${cut(bin.high)}`;
}

function observedText(bin: Bin, withheld: boolean): string {
  const observed = bin.observed;
  if (withheld || observed === null) return 'withheld';
  if (observed.rate === null || observed.churned === null)
    return 'fewer than 20 validation customers';
  return `${pct(observed.rate)} (${int(observed.churned)} of ${int(observed.customers)} validation)`;
}

/** Everything the tooltip shows, for the slider's value text. */
function binDescription(bin: Bin, withheld: boolean): string {
  return (
    `p ${binRange(bin)}, ${bin.band}: ${int(bin.customers)} customers, ` +
    `${money(bin.monthly_charges)} a month, observed on validation ${observedText(bin, withheld)}`
  );
}

/** Where a bin's rate is drawn: at what its validation customers were told, on the diagonal's scale. */
function dotX(bin: Bin): number {
  return x(bin.observed_mean ?? (bin.low + bin.high) / 2);
}

/** The observed-churn line, broken wherever a bin has no rate to show. */
function observedPath(bins: readonly Bin[]): string {
  let path = '';
  let drawing = false;
  for (const bin of bins) {
    const rate = bin.observed?.rate;
    if (rate === null || rate === undefined) {
      drawing = false;
      continue;
    }
    const px = dotX(bin).toFixed(1);
    const py = rateY(rate).toFixed(1);
    path += `${drawing ? 'L' : 'M'}${px} ${py} `;
    drawing = true;
  }
  return path.trim();
}

interface HistogramProps {
  book: Overview;
  measure: Measure;
  /** The one band the queue shows, if it shows exactly one. */
  only: string | null;
  onPickBand: (band: string) => void;
}

export function Histogram({ book, measure, only, onPickBand }: HistogramProps) {
  const [active, setActive] = useState<number | null>(null);
  const bins = book.histogram;
  const valueOf = (bin: Bin) => (measure === 'customers' ? bin.customers : bin.monthly_charges);
  const y = scaleLinear()
    .domain([0, Math.max(1, ...bins.map(valueOf))])
    .range([BOTTOM, TOP])
    .nice(2);
  const top = y.domain()[1] ?? 1;
  const axis = (value: number) => (measure === 'customers' ? int(value) : moneyShort(value));
  const withheld = book.withheld_reason !== null;
  const bin = active === null ? undefined : bins[active];

  const binAt = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return binIndex(event.clientX, box.left, box.width, bins.length);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = active ?? -1;
    const moves: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowUp: current + 1,
      ArrowLeft: current - 1,
      ArrowDown: current - 1,
      Home: 0,
      End: bins.length - 1,
    };
    const next = moves[event.key];
    if (next !== undefined) {
      event.preventDefault();
      setActive(Math.max(0, Math.min(bins.length - 1, next)));
    } else if ((event.key === 'Enter' || event.key === ' ') && bin) {
      event.preventDefault();
      onPickBand(bin.band);
    } else if (event.key === 'Escape') {
      setActive(null);
    }
  };

  const label =
    `Histogram of churn probability for ${int(book.customers)} customers in ${bins.length} bins` +
    (book.withheld_reason === null
      ? ', with the churn observed in each bin among validation customers.'
      : '.');
  const ticks = [...book.bands.map((band) => band.min), 1];

  return (
    <ChartFrame
      label={label}
      table={{
        caption: 'Customers per 0.05 of probability',
        columns: [
          'Probability',
          'Band',
          'Customers',
          'Monthly charges',
          'Observed churn, validation',
        ],
        rows: bins.map((b) => [
          binRange(b),
          b.band,
          int(b.customers),
          money(b.monthly_charges),
          observedText(b, withheld),
        ]),
      }}
      overlay={
        <>
          <span
            className={styles.yTick}
            style={cssVars({ '--y': TOP / HEIGHT })}
            aria-hidden="true"
          >
            {axis(top)}
          </span>
          <span
            className={styles.yTick}
            style={cssVars({ '--y': y(top / 2) / HEIGHT })}
            aria-hidden="true"
          >
            {axis(top / 2)}
          </span>
          <div
            role="slider"
            tabIndex={0}
            aria-label="Probability bin"
            aria-valuemin={1}
            aria-valuemax={bins.length}
            aria-valuenow={active === null ? 1 : active + 1}
            aria-valuetext={bin ? binDescription(bin, withheld) : 'no bin chosen'}
            className={styles.capture}
            // A tap fires focus before click: the pointer, not focus, names the bin.
            onPointerDown={(event) => setActive(binAt(event))}
            onPointerMove={(event) => setActive(binAt(event))}
            // Leaving with the mouse must not lose a keyboard user's place.
            onPointerLeave={(event) => {
              if (document.activeElement !== event.currentTarget) setActive(null);
            }}
            onClick={(event) => {
              const index = binAt(event);
              const picked = index === null ? undefined : bins[index];
              if (picked) onPickBand(picked.band);
            }}
            onKeyDown={onKeyDown}
            onFocus={() => setActive((current) => current ?? 0)}
            onBlur={() => setActive(null)}
          />
          <ChartTooltip
            visible={bin !== undefined}
            x={bin ? x((bin.low + bin.high) / 2) / WIDTH : 0}
            y={TOP / HEIGHT}
            flip={bin !== undefined && x((bin.low + bin.high) / 2) > WIDTH * 0.56}
            title={bin ? `p ${binRange(bin)} · ${bin.band}` : ''}
          >
            {bin && (
              <>
                <TipLine>
                  {int(bin.customers)} customers · {money(bin.monthly_charges)}/mo
                </TipLine>
                <TipLine>observed on validation {observedText(bin, withheld)}</TipLine>
              </>
            )}
          </ChartTooltip>
        </>
      }
      legend={
        <>
          <span className={styles.legendItem}>
            <span className={styles.ramp} aria-hidden="true" />
            {measure === 'customers' ? 'Customers' : 'Monthly charges'} per 0.05 of probability
          </span>
          {!withheld && (
            <>
              <span className={styles.legendItem}>
                <span className={styles.dot} aria-hidden="true" />
                observed churn in that bin, validation customers (right axis)
              </span>
              <span className={styles.legendItem}>
                <span className={styles.dashed} aria-hidden="true" />
                stated equals observed on validation
              </span>
            </>
          )}
        </>
      }
    >
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        {book.bands.map((band) => (
          <rect
            key={band.name}
            data-band={band.name}
            className={cx(styles.zone, only === band.name && styles.zoneOn)}
            x={x(band.min)}
            y={TOP}
            width={x(Math.min(band.max, 1)) - x(band.min)}
            height={BOTTOM - TOP}
          />
        ))}
        <line className={styles.grid} x1={LEFT} x2={RIGHT} y1={TOP} y2={TOP} />
        <line className={styles.grid} x1={LEFT} x2={RIGHT} y1={y(top / 2)} y2={y(top / 2)} />
        <line className={styles.axis} x1={LEFT} x2={RIGHT} y1={BOTTOM} y2={BOTTOM} />
        {book.bands.slice(1).map((band) => (
          <line
            key={band.name}
            className={styles.divider}
            x1={x(band.min)}
            x2={x(band.min)}
            y1={22}
            y2={BOTTOM}
          />
        ))}
        {book.bands.map((band) => (
          <text
            key={band.name}
            data-band={band.name}
            className={styles.bandName}
            x={x(band.min) + 6}
            y={16}
          >
            {band.name}
          </text>
        ))}
        <text className={styles.tick} x={LEFT - 6} y={BOTTOM + 4} textAnchor="end">
          0
        </text>
        <text className={styles.tick} x={RIGHT + 6} y={rateY(1) + 4}>
          100%
        </text>
        <text className={styles.tick} x={RIGHT + 6} y={rateY(0.5) + 4}>
          50%
        </text>
        {bins.map((b, i) => {
          const left = x(b.low) + GUTTER;
          const width = x(b.high) - x(b.low) - 2 * GUTTER;
          return (
            <rect
              key={i}
              data-band={b.band}
              className={cx(styles.bar, only !== null && only !== b.band && styles.faded)}
              x={left}
              y={y(valueOf(b))}
              width={width}
              height={BOTTOM - y(valueOf(b))}
            />
          );
        })}
        {!withheld && (
          <>
            <line className={styles.diagonal} x1={LEFT} y1={BOTTOM} x2={RIGHT} y2={TOP} />
            <path className={styles.observed} d={observedPath(bins)} />
            {bins.map((b, i) =>
              b.observed?.rate == null ? null : (
                <circle
                  key={i}
                  className={styles.observedDot}
                  cx={dotX(b)}
                  cy={rateY(b.observed.rate)}
                  r={3}
                />
              ),
            )}
          </>
        )}
        <line
          className={styles.threshold}
          x1={x(book.threshold)}
          x2={x(book.threshold)}
          y1={22}
          y2={202}
        />
        {/* Under the tick row, so it never runs into the next tick's label. */}
        <text className={styles.thresholdLabel} x={x(book.threshold) + 4} y={211}>
          flag ≥ {cut(book.threshold)}
        </text>
        {ticks.map((tick) => (
          <text key={tick} className={styles.tick} x={x(tick)} y={198} textAnchor="middle">
            {tick === 0 ? '0' : tick === 1 ? '1.0' : cut(tick)}
          </text>
        ))}
        {bin && (
          <rect
            className={styles.highlight}
            x={x(bin.low)}
            y={26}
            width={x(bin.high) - x(bin.low)}
            height={BOTTOM - 26}
          />
        )}
      </svg>
    </ChartFrame>
  );
}
