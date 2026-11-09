/**
 * How well the served model performs, as one table rather than a row of
 * tiles: ranking (threshold-free), the figures at the cut, the probabilities,
 * and accuracy last, by partition. The validation column is the served model
 * measured live; a test column appears only beside a recorded evaluation, so
 * no validation figure ever sits under a test label.
 */
import type { ReactNode } from 'react';
import type { Performance } from '../../api/queries';
import { Card, CardHeader } from '../../components/ui/Surface';
import { cut, int } from '../../lib/format';
import { cssVars } from '../../lib/style';
import {
  churnersFlagged,
  flaggedChurned,
  nobody,
  noSkill,
  SCORE_GROUPS,
  SCORE_NOTE,
  NOT_PUBLISHED,
  TEST_SCOPE,
  UNAVAILABLE,
  withheld,
} from './copy';
import {
  f1Of,
  precisionOf,
  recallOf,
  score,
  type Metrics,
  type RecordedMetrics,
} from './performance';
import { Coded } from './Provenance';
import styles from './Performance.module.css';

/** One partition's figures, as the scorecard reads them. */
interface Column {
  metrics: Metrics | RecordedMetrics;
  /** Null for the test release: its Brier, ROC-AUC and PR-AUC are not published. */
  scores: Pick<Metrics, 'average_precision' | 'roc_auc' | 'brier'> | null;
  churnRate: number | null;
  noSkillAccuracy: number;
  ece: number | null;
}

interface Row {
  label: string;
  meaning: string;
  value: (c: Column) => ReactNode;
}

function Value({ figure, sub, bar }: { figure: string; sub?: string; bar?: ReactNode }) {
  return (
    <>
      <span className={styles.scoreValue}>{figure}</span>
      {bar}
      {sub && <span className={styles.scoreSub}>{sub}</span>}
    </>
  );
}

/** PR-AUC against its no-skill line, both on one 0-to-1 track. */
function Track({ value, floor }: { value: number; floor: number | null }) {
  return (
    <span className={styles.track} aria-hidden="true">
      <span className={styles.trackFill} style={cssVars({ '--w': value })} />
      {floor !== null && <span className={styles.trackFloor} style={cssVars({ '--at': floor })} />}
    </span>
  );
}

/** A figure this partition does not publish: never filled in from another partition. */
function NotPublished() {
  return <Value figure="—" sub={NOT_PUBLISHED} />;
}

const GROUPS: { key: keyof typeof SCORE_GROUPS; rows: Row[] }[] = [
  {
    key: 'ranking',
    rows: [
      {
        label: 'PR-AUC (average precision)',
        meaning: 'primary · precision averaged over recall levels',
        value: (c) =>
          c.scores === null ? (
            <NotPublished />
          ) : (
            <Value
              figure={score(c.scores.average_precision)}
              bar={<Track value={c.scores.average_precision} floor={c.churnRate} />}
              {...(c.churnRate !== null && { sub: noSkill(c.churnRate) })}
            />
          ),
      },
      {
        label: 'ROC-AUC',
        meaning: 'chance a churner is scored above a stayer',
        value: (c) =>
          c.scores === null ? <NotPublished /> : <Value figure={score(c.scores.roc_auc)} />,
      },
    ],
  },
  {
    key: 'cut',
    rows: [
      {
        label: 'Recall',
        meaning: 'share of churners flagged',
        value: ({ metrics: m }) => (
          <Value
            figure={recallOf(m.positives, m.recall)}
            sub={churnersFlagged(m.tp, m.positives)}
          />
        ),
      },
      {
        label: 'Precision',
        meaning: 'share of the flagged who churned',
        value: ({ metrics: m }) => (
          <Value
            figure={precisionOf(m.tp + m.fp, m.precision)}
            sub={flaggedChurned(m.tp, m.tp + m.fp)}
          />
        ),
      },
      {
        label: 'F1',
        meaning: 'balance of precision and recall',
        value: ({ metrics: m }) => <Value figure={f1Of(m.tp + m.fp, m.positives, m.f1)} />,
      },
    ],
  },
  {
    key: 'probabilities',
    rows: [
      {
        label: 'Brier score',
        meaning: 'mean squared gap between probability and outcome; lower is better',
        value: (c) =>
          c.scores === null ? <NotPublished /> : <Value figure={score(c.scores.brier)} />,
      },
      {
        label: 'ECE',
        meaning: 'gap between stated and observed rates over ten bins; lower is better',
        value: (c) => (c.ece === null ? <NotPublished /> : <Value figure={score(c.ece)} />),
      },
    ],
  },
  {
    key: 'context',
    rows: [
      {
        label: 'Accuracy',
        meaning: 'share classified right',
        value: (c) => <Value figure={score(c.metrics.accuracy)} sub={nobody(c.noSkillAccuracy)} />,
      },
    ],
  },
];

export function Scorecard({ figures }: { figures: Performance }) {
  const measured = figures.validation_evaluation;
  const test = figures.test;
  const validation: Column | null = measured && {
    metrics: measured.metrics,
    scores: measured.metrics,
    churnRate: measured.churn_rate,
    noSkillAccuracy: measured.no_skill_accuracy,
    ece: measured.ece,
  };
  const recorded: Column | null = test && {
    metrics: test.metrics,
    scores: null,
    churnRate: test.churn_rate,
    noSkillAccuracy: test.no_skill_accuracy,
    ece: null,
  };
  const columns = [validation, ...(recorded ? [recorded] : [])];
  const cutLabel = (key: keyof typeof SCORE_GROUPS) => {
    if (key !== 'cut') return SCORE_GROUPS[key];
    return `${SCORE_GROUPS.cut} ${cut(validation?.metrics.threshold ?? figures.threshold)}`;
  };

  return (
    <Card titleId="scorecard-title">
      <CardHeader
        id="scorecard-title"
        title="How well it performs"
        subtitle="How churners are ranked, what the cut catches, and whether the probabilities hold up."
      />
      <div className={recorded ? styles.scoreOnly : styles.scoreLayout}>
        <table className={styles.scoreTable}>
          <caption className="sr">Metrics by partition</caption>
          <thead>
            <tr>
              <th scope="col">
                <span className="sr">Metric</span>
              </th>
              <th scope="col">
                <span className={styles.colTag}>Validation</span>
                <span className={styles.colSub}>
                  {validation
                    ? `${int(validation.metrics.n)} customers`
                    : figures.withheld_reason
                      ? withheld(figures.withheld_reason)
                      : UNAVAILABLE}
                </span>
              </th>
              {recorded && (
                <th scope="col">
                  <span className={styles.colTag}>Test · recorded</span>
                  <span className={styles.colSub}>{int(recorded.metrics.n)} customers</span>
                </th>
              )}
            </tr>
          </thead>
          {GROUPS.map((group) => (
            <tbody key={group.key} aria-label={cutLabel(group.key)}>
              <tr className={styles.groupRow}>
                <th scope="rowgroup" colSpan={columns.length + 1}>
                  {cutLabel(group.key)}
                </th>
              </tr>
              {group.rows.map((row) => (
                <tr key={row.label}>
                  <th scope="row" className={styles.metric}>
                    <span className={styles.metricName}>{row.label}</span>
                    <span className={styles.metricMeaning}>{row.meaning}</span>
                  </th>
                  {columns.map((column, i) => (
                    <td key={i}>{column ? row.value(column) : '—'}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
        </table>
        {!recorded && (
          <div role="note" aria-label="Test evaluation" className={styles.notRecorded}>
            <p className={styles.notRecordedTitle}>No test figures shown</p>
            {figures.test_reason && (
              <p>
                <Coded text={figures.test_reason} />
              </p>
            )}
            <p>
              The validation column is the only measured evidence here, and it is somewhat
              optimistic: validation set the cut.
            </p>
          </div>
        )}
      </div>
      <p className={styles.note}>{SCORE_NOTE}</p>
      {test && <p className={styles.note}>{TEST_SCOPE}</p>}
    </Card>
  );
}
