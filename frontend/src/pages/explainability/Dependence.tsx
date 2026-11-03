/**
 * One attribute's contribution against its value, for the explained
 * customers: a dot each, coloured by the sign shown, and the server's group
 * means (a line through the numeric groups that has a gap wherever a group is
 * too small or empty, or a bar per category). A placed customer is a ring.
 *
 * The groups are stepped through with the slider under the chart; its value
 * text says what the tooltip shows. There is no per-dot hover, and no
 * customer probability is reconstructed from rounded contributions.
 */
import { scaleLinear } from 'd3-scale';
import { useState } from 'react';
import type { Explanation, GlobalExplanation } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { ChartTooltip } from '../../components/charts/ChartTooltip';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { int, pct, shownSign, signedPoints } from '../../lib/format';
import { cx } from '../../lib/style';
import {
  CARDS,
  categoryTick,
  depLabel,
  depNote,
  featureName,
  groupName,
  groupText,
  question,
  totalChargesNote,
} from './copy';
import {
  categoryCenter,
  columnDomain,
  contributionOf,
  dependenceOf,
  groupsOf,
  jitter,
  meanSegments,
  modelRanks,
  valueDomain,
  type Group,
} from './explain';
import styles from './Explain.module.css';

const D = { width: 520, height: 330, left: 50, right: 506, top: 14, bottom: 276 };
const SIGN_CLASS = { 1: 'raises', [-1]: 'lowers', 0: 'none' } as const;

function dot(cx: number, cy: number, r = 2.6): string {
  return `M${(cx - r).toFixed(1)} ${cy.toFixed(1)}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
}

/** A numeric axis bound: months whole, money in whole dollars. */
function tick(feature: string, value: number): string {
  return feature === 'tenure'
    ? `${Math.round(value)} mo`
    : `$${Math.round(value).toLocaleString('en-US')}`;
}

interface Props {
  global: GlobalExplanation;
  feature: string;
  ringed: Explanation | undefined;
}

export function Dependence({ global, feature, ringed }: Props) {
  const [index, setIndex] = useState(0);
  const [active, setActive] = useState(false);
  const summary = dependenceOf(global, feature);
  const label = global.labels[feature] ?? featureName(feature);
  const rank = modelRanks(global)[feature];
  const share = global.importance.find((r) => r.feature === feature)?.share;
  const own = contributionOf(ringed, feature);
  const j = global.features.indexOf(feature);
  const column = global.values.map((row) => row[j] ?? 0);
  const y = scaleLinear()
    .domain(columnDomain(global, feature, own?.shap))
    .range([D.bottom, D.top]);

  const header = (
    <CardHeader
      id="dependence-title"
      title={`${label}: value against contribution`}
      subtitle={question(feature)}
      aside={
        rank !== undefined && share !== undefined ? (
          <Tag>
            rank {rank} · {pct(share)} of total
          </Tag>
        ) : undefined
      }
    />
  );
  if (!summary || j < 0) {
    return (
      <Card titleId="dependence-title" className={styles.card}>
        {header}
        <p className={styles.note}>{CARDS.dependence.noSummary}</p>
      </Card>
    );
  }

  const groups = groupsOf(summary);
  const at = Math.min(index, Math.max(0, groups.length - 1));
  const group = groups[at];

  // --- where everything goes --------------------------------------------------
  const dots: Record<'raises' | 'lowers' | 'none', string> = { raises: '', lowers: '', none: '' };
  let means = '';
  let markers = '';
  let ring: [number, number] | null = null;
  let xTicks: { at: number; text: string; sub?: string }[];
  let groupAt: (g: Group, k: number) => number;

  if (summary.kind === 'numeric') {
    const ownNumber = typeof own?.value === 'number' ? own.value : undefined;
    const x = scaleLinear()
      .domain(valueDomain(global, feature, ownNumber))
      .range([D.left, D.right]);
    global.x.forEach((row, i) => {
      const value = row[feature];
      if (typeof value !== 'number') return;
      const c = column[i] ?? 0;
      dots[SIGN_CLASS[shownSign(c)]] += dot(x(value), y(c));
    });
    for (const run of meanSegments(summary.bins)) {
      if (run.length > 1)
        means += run
          .map(([v, m], k) => `${k === 0 ? 'M' : 'L'}${x(v).toFixed(1)} ${y(m).toFixed(1)}`)
          .join('');
      for (const [v, m] of run) markers += dot(x(v), y(m), 3);
    }
    if (own && ownNumber !== undefined) ring = [x(ownNumber), y(own.shap)];
    const [lo, hi] = x.domain() as [number, number];
    xTicks = [lo, (lo + hi) / 2, hi].map((v) => ({ at: x(v), text: tick(feature, v) }));
    groupAt = (g) => (g.kind === 'bin' ? x((g.low + g.high) / 2) : D.left);
  } else {
    const levels = summary.levels;
    const count = levels.length;
    const width = (D.right - D.left) / count;
    const spread = Math.min(70, width * 0.6);
    const half = Math.min(40, width * 0.35);
    const centre = (k: number) => categoryCenter(k, count, D.left, D.right);
    const indexOf = new Map(levels.map((l, k) => [l.category, k]));
    global.x.forEach((row, i) => {
      const k = indexOf.get(String(row[feature]));
      if (k === undefined) return;
      const c = column[i] ?? 0;
      dots[SIGN_CLASS[shownSign(c)]] += dot(centre(k) + jitter(i, spread), y(c));
    });
    levels.forEach((l, k) => {
      if (l.mean !== null)
        means += `M${(centre(k) - half).toFixed(1)} ${y(l.mean).toFixed(1)}H${(centre(k) + half).toFixed(1)}`;
    });
    const ownK = own ? indexOf.get(String(own.value)) : undefined;
    if (own && ownK !== undefined) ring = [centre(ownK), y(own.shap)];
    xTicks = levels.map((l, k) => ({
      at: centre(k),
      text: categoryTick(feature, l.category),
      sub: `n ${int(l.customers)}`,
    }));
    groupAt = (_, k) => centre(k);
  }

  const tipX = group ? groupAt(group, at) : 0;
  // A group without a mean is pointed at from the top, never at a mean of zero.
  const tipY = group?.mean != null ? y(group.mean) : D.top;
  const yTicks = y.ticks(4);
  const tableRows = groups.map((g) => [
    groupName(feature, g),
    int(g.customers),
    g.mean !== null
      ? signedPoints(g.mean)
      : g.customers === 0
        ? 'none in the sample'
        : 'too few for a mean',
  ]);
  const chargesNote = feature === 'TotalCharges' ? totalChargesNote(global.total_charges_r) : null;

  return (
    <Card titleId="dependence-title" className={styles.card}>
      {header}
      <ChartFrame
        label={depLabel(label, global.n_explained)}
        table={{
          caption: `${label}: groups`,
          columns: ['Group', 'Customers', 'Mean contribution (pp)'],
          rows: tableRows,
        }}
        overlay={
          <ChartTooltip
            visible={active && group !== undefined}
            x={tipX / D.width}
            y={tipY / D.height}
            flip={tipX / D.width > 0.6}
            title={group ? groupText(feature, group) : ''}
          />
        }
        legend={
          <>
            <span className={styles.legendItem}>
              <span className={cx(styles.dotKey, styles.raisesKey)} aria-hidden="true" />
              raises the estimate
            </span>
            <span className={styles.legendItem}>
              <span className={cx(styles.dotKey, styles.lowersKey)} aria-hidden="true" />
              lowers it
            </span>
            <span className={styles.legendItem}>
              <span className={cx(styles.dotKey, styles.noneKey)} aria-hidden="true" />
              rounds to 0.0
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
        <svg viewBox={`0 0 ${D.width} ${D.height}`}>
          {yTicks.map((t) => (
            <g key={t}>
              <line
                className={t === 0 ? styles.zero : styles.grid}
                x1={D.left}
                x2={D.right}
                y1={y(t)}
                y2={y(t)}
              />
              <text className={styles.tick} x={D.left - 8} y={y(t) + 4} textAnchor="end">
                {t === 0 ? '0' : signedPoints(t, 0)}
              </text>
            </g>
          ))}
          <text
            className={styles.tick}
            x={12}
            y={(D.top + D.bottom) / 2}
            textAnchor="middle"
            transform={`rotate(-90 12 ${(D.top + D.bottom) / 2})`}
          >
            pp of churn probability
          </text>
          {xTicks.map((t, k) => (
            <g key={k}>
              <text className={styles.tick} x={t.at} y={D.bottom + 18} textAnchor="middle">
                {t.text}
              </text>
              {t.sub && (
                <text className={styles.tickSub} x={t.at} y={D.bottom + 32} textAnchor="middle">
                  {t.sub}
                </text>
              )}
            </g>
          ))}
          <path className={styles.raises} d={dots.raises || 'M0 0'} />
          <path className={styles.lowers} d={dots.lowers || 'M0 0'} />
          <path className={styles.none} d={dots.none || 'M0 0'} />
          <path className={styles.mean} d={means || 'M0 0'} />
          <path className={styles.meanMarker} d={markers || 'M0 0'} />
          {active && group?.mean != null && (
            <circle
              data-testid="group-mark"
              className={styles.groupMark}
              cx={tipX}
              cy={tipY}
              r={6}
            />
          )}
          {ring && (
            <circle data-testid="ring" className={styles.ring} cx={ring[0]} cy={ring[1]} r={6} />
          )}
        </svg>
      </ChartFrame>
      {groups.length > 0 && group && (
        <div className={styles.groupField}>
          <label htmlFor="dependence-group" className={styles.groupLabel}>
            Group
          </label>
          <input
            id="dependence-group"
            type="range"
            className={styles.slider}
            min={0}
            max={groups.length - 1}
            step={1}
            value={at}
            aria-valuetext={groupText(feature, group)}
            onChange={(event) => setIndex(Number(event.target.value))}
            onFocus={() => setActive(true)}
            onBlur={() => setActive(false)}
            onPointerEnter={() => setActive(true)}
            onPointerLeave={() => setActive(false)}
          />
        </div>
      )}
      <p className={styles.note}>{depNote(summary.kind, global.dependence_min_customers)}</p>
      {chargesNote && <p className={styles.note}>{chargesNote}</p>}
    </Card>
  );
}
