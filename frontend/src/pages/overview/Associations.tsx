/**
 * "What the risk is associated with": churn by segment on the training
 * partition, and what moves the score - in the model (mean |SHAP|) and on its
 * own (univariate association). Observed associations in one snapshot;
 * nothing here identifies a cause. (Churn by tenure is on the summary.)
 *
 * Bars are scaled to the largest figure in their card, rounded up to the
 * next tenth, so the cards never clip and the tick for the average stays
 * comparable across tabs.
 */
import { useState } from 'react';
import type { Schemas } from '../../api/client';
import type { Overview } from '../../api/queries';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { Segmented } from '../../components/ui/Toggle';
import { Tabs } from '../../components/ui/Tabs';
import { hiddenText, MIN_CUSTOMERS } from '../../lib/cells';
import { int, pct } from '../../lib/format';
import { isFeature, overviewLevel, RANKING } from '../../lib/labels';
import { importanceNote, segmentNote } from './copy';
import { Meter } from './Meter';
import { scaleTop } from './scale';
import styles from './Associations.module.css';

type Cell = Schemas['ReportedCell'] | Schemas['SuppressedCell'];

export function Associations({ book }: { book: Overview }) {
  return (
    <section aria-labelledby="associations" className={styles.section}>
      <div className={styles.heading}>
        <h2 id="associations" className={styles.title}>
          What the risk is associated with
        </h2>
        <span className={styles.caveat}>
          Observed associations in one historical snapshot. Nothing here identifies a cause.
        </span>
      </div>
      <div className={styles.row}>
        <Segments tabs={book.segments} withheld={book.withheld_reason} />
        <Importance book={book} />
      </div>
    </section>
  );
}

const TAB_LABELS: Record<string, string> = {
  Contract: 'Contract',
  PaymentMethod: 'Payment',
  InternetService: 'Internet',
  monthly: 'Price',
};

function Segments({ tabs, withheld }: { tabs: Overview['segments']; withheld: string | null }) {
  const [key, setKey] = useState(tabs[0]?.key ?? '');
  const tab = tabs.find((t) => t.key === key) ?? tabs[0];
  const top = scaleTop(
    tabs.flatMap((t) => [...t.levels.map((c) => (c.suppressed ? null : c.rate)), t.base_rate]),
  );
  return (
    <Card titleId="segments" className={styles.segments}>
      <CardHeader
        id="segments"
        level={3}
        title="Churn by segment"
        subtitle="Which groups lost customers?"
        aside={tab && <Tag>TRAIN · {int(tab.partition_customers)}</Tag>}
      />
      {!tab ? (
        <p className={styles.note}>Observed outcomes are withheld: {withheld}</p>
      ) : (
        <>
          <Tabs
            label="Segment"
            tabs={tabs.map((t) => ({ value: t.key, label: TAB_LABELS[t.key] ?? t.key }))}
            value={tab.key}
            onChange={setKey}
          >
            <div className={styles.levels}>
              {tab.levels.map((cell) => (
                <LevelBar
                  key={cell.row}
                  dimension={tab.key}
                  cell={cell}
                  top={top}
                  base={tab.base_rate}
                />
              ))}
            </div>
          </Tabs>
          <p className={styles.note}>
            {segmentNote(tab)}{' '}
            {tab.base_rate !== null && (
              <span className={styles.nowrap}>Tick = {pct(tab.base_rate)} average.</span>
            )}
          </p>
        </>
      )}
    </Card>
  );
}

function LevelBar({
  dimension,
  cell,
  top,
  base,
}: {
  dimension: string;
  cell: Cell;
  top: number;
  base: number | null;
}) {
  return (
    <div className={styles.level}>
      <div className={styles.levelHead}>
        <span className={styles.levelName}>{overviewLevel(dimension, cell.row)}</span>
        {cell.suppressed ? (
          <span className={styles.detail}>{hiddenText(cell, MIN_CUSTOMERS)}</span>
        ) : (
          <span className={styles.nowrap}>
            <span className={styles.detail}>
              {int(cell.churned)} of {int(cell.n)}
            </span>
            <span className={styles.rate}>{pct(cell.rate)}</span>
          </span>
        )}
      </div>
      <Meter
        value={cell.suppressed ? null : cell.rate / top}
        tick={base === null ? null : base / top}
      />
    </div>
  );
}

const MEASURE: Record<string, string> = { "Cramer's V": 'V', 'point-biserial |r|': '|r|' };

function Importance({ book }: { book: Overview }) {
  const [mode, setMode] = useState<'model' | 'alone'>('model');
  const importance = book.importance ?? [];
  const association = book.association ?? [];
  const lead = association[0]?.feature;
  const name = (feature: string) => (isFeature(feature) ? RANKING[feature] : feature);
  const rows =
    mode === 'model'
      ? importance.slice(0, 5).map((r) => ({
          feature: r.feature,
          value: r.mean_abs_shap,
          text: `${(r.mean_abs_shap * 100).toFixed(1)} pp`,
        }))
      : association.slice(0, 5).map((r) => ({
          feature: r.feature,
          value: r.strength,
          text: `${MEASURE[r.measure] ?? r.measure} ${r.strength.toFixed(2)}`,
        }));
  const largest = Math.max(0, ...rows.map((r) => r.value));
  const basis = book.importance_basis;
  const question =
    mode === 'model'
      ? basis
        ? `Mean |SHAP| on ${int(basis.customers)} ${basis.partition} customers, in percentage points.`
        : 'No attributions are cached for the served model.'
      : 'Univariate association with churn on the training partition.';
  const note =
    mode === 'model'
      ? importanceNote(importance, association)
      : 'Cramér’s V for categories, point-biserial |r| for numbers. Both rankings are published; neither is a cause.';
  return (
    <Card titleId="importance" className={styles.importance}>
      <CardHeader id="importance" level={3} title="What moves the score" subtitle={question} />
      <Segmented
        label="Ranking"
        small
        value={mode}
        onChange={setMode}
        options={[
          { value: 'model', label: 'In the model' },
          { value: 'alone', label: 'On its own' },
        ]}
      />
      <div className={styles.ranking}>
        {rows.map((row) => (
          <div key={row.feature} className={styles.level} data-lead={row.feature === lead}>
            <div className={styles.levelHead}>
              <span className={styles.featureName}>{name(row.feature)}</span>
              <span className={styles.featureValue}>{row.text}</span>
            </div>
            <Meter
              value={largest > 0 ? row.value / largest : 0}
              tone={row.feature === lead ? 'sim' : 'accent'}
              thin
            />
          </div>
        ))}
      </div>
      {note && <p className={styles.note}>{note}</p>}
    </Card>
  );
}
