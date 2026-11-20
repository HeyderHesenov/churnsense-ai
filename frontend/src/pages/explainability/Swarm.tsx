/**
 * Every cached attribution of every attribute: one row per attribute in the
 * served ranking, one dot per explained customer, on one shared axis of
 * percentage points. A numeric attribute's dots are coloured by the third of
 * its observed range their value falls in; a category's are grey. A placed
 * customer is a ring on each row, from their own explanation.
 *
 * The picture is one SVG; the rows are buttons drawn over it, outside it in
 * the accessibility tree. The table says what the picture shows.
 */
import { scaleLinear } from 'd3-scale';
import { useId, useMemo } from 'react';
import type { Explanation, GlobalExplanation } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { Card, CardHeader } from '../../components/ui/Surface';
import { pct, signedPoints, tickLabels } from '../../lib/format';
import { valueText } from '../../lib/labels';
import { cx } from '../../lib/style';
import { CARDS, featureName, rowDescription, SCALE } from './copy';
import {
  contributionDomain,
  contributionOf,
  modelRanks,
  SWARM,
  swarmPaths,
  valueClasses,
  type ValueClass,
} from './explain';
import styles from './Explain.module.css';

/** The SVG's own width; it is stretched to its column, rows keep their height. */
const WIDTH = 400;
const INSET = 6;
const CLASSES: readonly ValueClass[] = ['low', 'mid', 'high', 'category'];

interface Props {
  global: GlobalExplanation;
  feature: string;
  /** A customer whose explanation is comparable with the cached figures. */
  ringed: Explanation | undefined;
  onPick: (feature: string) => void;
}

export function Swarm({ global, feature, ringed, onPick }: Props) {
  const [low, high] = contributionDomain(global, ringed);
  const x = useMemo(
    () =>
      scaleLinear()
        .domain([low, high])
        .range([INSET, WIDTH - INSET]),
    [low, high],
  );
  const rows = useMemo(
    () =>
      global.importance.map((row, i) => {
        const j = global.features.indexOf(row.feature);
        const column = j < 0 ? [] : global.values.map((values) => values[j] ?? 0);
        return {
          ...row,
          label: global.labels[row.feature] ?? featureName(row.feature),
          paths: swarmPaths(column, valueClasses(global, row.feature), x, i),
        };
      }),
    [global, x],
  );
  const ranks = modelRanks(global);
  const rowsId = useId();
  const ticks = x.ticks(5);
  const tickNames = tickLabels(ticks);
  const height = rows.length * SWARM.row;
  const association = global.association_rank;

  const columns = ['Attribute', 'Model rank', 'Mean |contribution| (pp)', 'Share'];
  if (ringed) columns.push('Customer value', 'Customer contribution (pp)');
  if (association) columns.push('Association rank');

  return (
    <Card titleId="swarm-title" className={styles.card}>
      <CardHeader id="swarm-title" title={CARDS.swarm.title} subtitle={CARDS.swarm.subtitle} />
      <div className={styles.swarmScroll}>
        <ChartFrame
          className={styles.swarmFrame}
          label={`Contributions of ${rows.length} attributes for ${global.n_explained} explained customers, in ${SCALE}`}
          table={{
            caption: 'Attributes by mean absolute contribution',
            columns,
            rows: rows.map((row) => {
              const cells = [
                row.label,
                String(ranks[row.feature] ?? ''),
                (row.mean_abs_shap * 100).toFixed(1),
                pct(row.share),
              ];
              const own = contributionOf(ringed, row.feature);
              if (ringed)
                cells.push(
                  own ? valueText(row.feature, own.value) : '—',
                  own ? signedPoints(own.shap) : '—',
                );
              if (association) cells.push(String(association[row.feature] ?? '—'));
              return cells;
            }),
          }}
          overlay={
            <div role="group" aria-label="Attributes" className={styles.swarmRows}>
              {rows.map((row) => {
                const on = row.feature === feature;
                return (
                  <button
                    key={row.feature}
                    type="button"
                    className={cx(styles.swarmRow, on && styles.swarmRowOn)}
                    aria-pressed={on}
                    aria-describedby={`${rowsId}-${row.feature}`}
                    onClick={() => onPick(row.feature)}
                  >
                    <span className={styles.rowLabel}>{row.label}</span>
                    <span />
                    <span className={styles.rowMean}>{(row.mean_abs_shap * 100).toFixed(1)}</span>
                    <span id={`${rowsId}-${row.feature}`} hidden>
                      {rowDescription(row.mean_abs_shap, ranks[row.feature] ?? 0)}
                    </span>
                  </button>
                );
              })}
            </div>
          }
          legend={
            <>
              <span className={styles.legendItem}>
                low value <span className={styles.gradientKey} aria-hidden="true" /> high value
              </span>
              <span className={styles.legendItem}>
                <span className={cx(styles.dotKey, styles.category)} aria-hidden="true" />
                category
              </span>
              {ringed && (
                <span className={styles.legendItem}>
                  <span className={styles.ringKey} aria-hidden="true" />
                  {ringed.customer_id}
                </span>
              )}
            </>
          }
        >
          <div className={styles.swarmHead}>
            <span>Attribute</span>
            {/* No viewBox: the labels keep their size, placed at a share of the width. */}
            <svg className={styles.axisLabels} aria-hidden="true">
              {ticks.map((t, i) => (
                <text key={t} x={share(x(t))} y={12} textAnchor="middle">
                  {tickNames[i]}
                </text>
              ))}
            </svg>
            <span className={styles.headMean}>mean |pp|</span>
          </div>
          <div className={styles.swarmPlot}>
            <span />
            <div className={styles.swarmArea}>
              {/* One 26 px line per attribute gives the area its height; the SVGs fill it. */}
              {rows.map((row) => (
                <span key={row.feature} className={styles.rowSpace} aria-hidden="true" />
              ))}
              <svg
                viewBox={`0 0 ${WIDTH} ${height}`}
                preserveAspectRatio="none"
                className={styles.swarmSvg}
                height={height}
              >
                {rows.map(
                  (row, i) =>
                    row.feature === feature && (
                      <rect
                        key="on"
                        className={styles.selectedBand}
                        x={0}
                        y={i * SWARM.row}
                        width={WIDTH}
                        height={SWARM.row}
                      />
                    ),
                )}
                {ticks.map((t) => (
                  <line
                    key={t}
                    className={t === 0 ? styles.zero : styles.grid}
                    x1={x(t)}
                    x2={x(t)}
                    y1={0}
                    y2={height}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
                {rows.map((row) => (
                  <g key={row.feature}>
                    {CLASSES.map((c) => (
                      <path key={c} className={styles[c]} d={row.paths[c] || 'M0 0'} />
                    ))}
                  </g>
                ))}
              </svg>
              {/* Rings in their own unscaled SVG, so they stay round however the dots stretch. */}
              {ringed && (
                <svg className={styles.ringLayer} height={height} aria-hidden="true">
                  {rows.map((row, i) => {
                    const own = contributionOf(ringed, row.feature);
                    return (
                      own && (
                        <circle
                          key={row.feature}
                          data-testid="ring"
                          className={styles.ring}
                          cx={share(x(own.shap))}
                          cy={i * SWARM.row + SWARM.row / 2}
                          r={5.5}
                        />
                      )
                    );
                  })}
                </svg>
              )}
            </div>
            <span />
          </div>
        </ChartFrame>
      </div>
      <p className={styles.note}>{CARDS.swarm.note}</p>
    </Card>
  );
}

/** A place on the stretched drawing as a share of its width, for an unscaled SVG. */
function share(at: number): string {
  return `${String((at / WIDTH) * 100)}%`;
}
