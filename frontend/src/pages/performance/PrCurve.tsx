/**
 * Precision against recall across the validation sweep's cuts, both axes
 * 0 to 1. A cut that flags nobody has no precision and is not drawn; the
 * no-skill line is the precision of flagging everyone (the sweep's cut at 0),
 * which is the churn rate. The F1 contours are geometry, not data.
 *
 * Hover reads a cut and a click loads it into the errors card; the slider
 * there is the keyboard control, and the table says what the picture shows.
 */
import { useState, type MouseEvent } from 'react';
import type { Performance } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { ChartTooltip, TipLine } from '../../components/charts/ChartTooltip';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut, int } from '../../lib/format';
import { PR_HINT, prLabel, UNAVAILABLE, withheld } from './copy';
import {
  contour,
  f1Of,
  indexOfLabel,
  nearestCut,
  PR,
  precisionOf,
  prX,
  prY,
  recallOf,
  score,
  sweepPath,
  tabledCuts,
} from './performance';
import styles from './Performance.module.css';

const TICKS = [0, 0.2, 0.4, 0.6, 0.8, 1];
const CONTOURS = [0.5, 0.6, 0.7];

interface Props {
  figures: Performance;
  /** The explored point, or -1. */
  index: number;
  onPick: (index: number) => void;
}

function fractions(event: MouseEvent<HTMLDivElement>): [number, number] {
  const box = event.currentTarget.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return [0, 0];
  return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height];
}

export function PrCurve({ figures, index, onPick }: Props) {
  const [hover, setHover] = useState<number>(-1);
  const curve = figures.validation;

  const header = (
    <CardHeader
      id="pr-title"
      title="Precision against recall"
      subtitle="What each extra churner reached costs in precision, at every validation cut."
      aside={<Tag>VALIDATION</Tag>}
    />
  );
  if (!curve) {
    return (
      <Card titleId="pr-title" className={styles.card}>
        {header}
        <p className={styles.unavailable}>
          {figures.withheld_reason ? withheld(figures.withheld_reason) : UNAVAILABLE}
        </p>
      </Card>
    );
  }

  const points = curve.points;
  const servedIndex = indexOfLabel(points, cut(figures.threshold));
  const served = points[servedIndex];
  const current = points[index];
  const shown = points[hover];
  const everyone = points[0];
  const noSkill =
    everyone && everyone.threshold === 0 && everyone.flagged === curve.customers
      ? everyone.precision
      : null;
  const mark = (i: number) =>
    [i === servedIndex && 'served', i === index && i !== servedIndex && 'explored']
      .filter(Boolean)
      .join(', ');
  const pick = (event: MouseEvent<HTMLDivElement>) => {
    const [fx, fy] = fractions(event);
    return nearestCut(points, fx, fy);
  };

  return (
    <Card titleId="pr-title" className={styles.card}>
      {header}
      <ChartFrame
        label={prLabel(served, current, curve.churners)}
        table={{
          caption: 'Validation precision and recall by cut',
          columns: ['Cut', 'Flagged', 'Precision', 'Recall', 'F1', 'Marked'],
          rows: tabledCuts(points, [servedIndex, index]).map((p) => {
            const i = points.indexOf(p);
            return [
              cut(p.threshold),
              int(p.flagged),
              precisionOf(p.flagged, p.precision),
              recallOf(curve.churners, p.recall),
              f1Of(p.flagged, curve.churners, p.f1),
              mark(i),
            ];
          }),
        }}
        overlay={
          <>
            <div
              aria-hidden="true"
              data-testid="pr-capture"
              className={styles.prCapture}
              onPointerMove={(event) => setHover(pick(event))}
              onPointerDown={(event) => setHover(pick(event))}
              onPointerLeave={() => setHover(-1)}
              onClick={(event) => {
                const i = pick(event);
                if (i >= 0) onPick(i);
              }}
            />
            <ChartTooltip
              visible={shown !== undefined}
              x={shown ? prX(shown.recall) / PR.width : 0}
              y={shown ? prY(shown.precision) / PR.height : 0}
              flip={shown !== undefined && prX(shown.recall) / PR.width > 0.6}
              title={shown ? `cut ${cut(shown.threshold)}` : ''}
            >
              {shown && (
                <>
                  <TipLine>
                    P {precisionOf(shown.flagged, shown.precision)} · R{' '}
                    {recallOf(curve.churners, shown.recall)} · F1{' '}
                    {f1Of(shown.flagged, curve.churners, shown.f1)}
                  </TipLine>
                  <TipLine>
                    {int(shown.flagged)} flagged · {int(shown.true_positives)} churners reached
                  </TipLine>
                </>
              )}
            </ChartTooltip>
          </>
        }
        legend={
          <>
            <span className={styles.legendItem}>
              <span className={styles.curveKey} aria-hidden="true" />
              validation cuts that flag someone
            </span>
            {served && (
              <span className={styles.legendItem}>
                <span className={styles.servedKey} aria-hidden="true" />
                served cut {cut(served.threshold)}
              </span>
            )}
            {current && current !== served && (
              <span className={styles.legendItem}>
                <span className={styles.currentKey} aria-hidden="true" />
                explored cut {cut(current.threshold)}
              </span>
            )}
            {noSkill !== null && (
              <span className={styles.legendItem}>
                <span className={styles.noSkillKey} aria-hidden="true" />
                no skill: the churn rate, {score(noSkill)}
              </span>
            )}
            <span className={styles.legendItem}>
              <span className={styles.contourKey} aria-hidden="true" />
              equal-F1 contours
            </span>
          </>
        }
      >
        <svg viewBox={`0 0 ${PR.width} ${PR.height}`}>
          {TICKS.map((t) => (
            <g key={t}>
              <line className={styles.grid} x1={prX(t)} x2={prX(t)} y1={PR.top} y2={PR.bottom} />
              <line className={styles.grid} x1={PR.left} x2={PR.right} y1={prY(t)} y2={prY(t)} />
              <text className={styles.tick} x={prX(t)} y={PR.bottom + 16} textAnchor="middle">
                {t === 0 ? '0' : t.toFixed(1)}
              </text>
              <text className={styles.tick} x={PR.left - 8} y={prY(t) + 4} textAnchor="end">
                {t === 0 ? '0' : t.toFixed(1)}
              </text>
            </g>
          ))}
          <line className={styles.axis} x1={PR.left} x2={PR.right} y1={PR.bottom} y2={PR.bottom} />
          <line className={styles.axis} x1={PR.left} x2={PR.left} y1={PR.top} y2={PR.bottom} />
          {CONTOURS.map((f) => (
            <g key={f}>
              <path className={styles.contour} d={contour(f)} />
              <text
                className={styles.contourLabel}
                x={prX(1) - 4}
                y={prY(f / (2 - f)) - 5}
                textAnchor="end"
              >
                F1 {f.toFixed(1)}
              </text>
            </g>
          ))}
          {noSkill !== null && (
            <line
              className={styles.noSkill}
              x1={PR.left}
              x2={PR.right}
              y1={prY(noSkill)}
              y2={prY(noSkill)}
            />
          )}
          <path className={styles.curve} d={sweepPath(points)} />
          {served && served.flagged > 0 && (
            <circle
              className={styles.servedMark}
              cx={prX(served.recall)}
              cy={prY(served.precision)}
              r={6.5}
            />
          )}
          {current && current.flagged > 0 && (
            <circle
              className={styles.currentMark}
              cx={prX(current.recall)}
              cy={prY(current.precision)}
              r={4.5}
            />
          )}
          {shown && (
            <circle
              className={styles.hoverMark}
              cx={prX(shown.recall)}
              cy={prY(shown.precision)}
              r={4}
            />
          )}
          <text
            className={styles.axisTitle}
            x={(PR.left + PR.right) / 2}
            y={PR.height - 4}
            textAnchor="middle"
          >
            Recall: share of churners flagged
          </text>
          <text
            className={styles.axisTitle}
            transform={`translate(12 ${(PR.top + PR.bottom) / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            Precision: flagged who churned
          </text>
        </svg>
      </ChartFrame>
      <p className={styles.note}>{PR_HINT}</p>
    </Card>
  );
}
