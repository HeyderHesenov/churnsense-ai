/**
 * The headline figures as tiles, one partition at a time, each under its own
 * label. Validation shows the served model's ranking and probability figures
 * (PR-AUC, ROC-AUC, Brier, ECE), measured live, and no other figure. Test shows the recorded
 * release only: its two-by-two table's rates (recall, precision, F1,
 * accuracy). Neither is ever filled in from the other, and the test record's
 * churn rate and no-skill accuracy are not shown: the release names the table
 * and its rates, nothing more.
 */
import type { Performance } from '../../api/queries';
import { useSweepNote } from '../../components/ui/DataSourceNote';
import { StatTile, Tag } from '../../components/ui/Surface';
import { cut, int } from '../../lib/format';
import {
  churnersFlagged,
  evaluatedOn,
  flaggedOutcomes,
  NO_TEST,
  TEST_NOTE,
  TEST_SCOPE,
  unavailableText,
  VALIDATION_NOTE,
} from './copy';
import { f1Of, precisionOf, recallOf, score } from './performance';
import { Coded } from './Provenance';
import styles from './Performance.module.css';

/** PR-AUC on a 0-to-1 bar: the figure the tile states, drawn. */
function ApBar({ value }: { value: number }) {
  return (
    <svg
      className={styles.apBar}
      viewBox="0 0 100 12"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect className={styles.apTrack} x={0} y={3} width={100} height={6} rx={3} />
      <rect className={styles.apFill} x={0} y={3} width={value * 100} height={6} rx={3} />
    </svg>
  );
}

export function ValidationTiles({ figures }: { figures: Performance }) {
  const sweepNote = useSweepNote();
  const measured = figures.validation_evaluation;
  if (!measured) {
    return (
      <p className={styles.unavailable}>{unavailableText(figures.withheld_reason, sweepNote)}</p>
    );
  }
  const m = measured.metrics;
  return (
    <div role="group" aria-label="Validation figures" className={styles.partBlock}>
      <p className={styles.partHead}>
        <Tag>VALIDATION</Tag> <span>{int(m.n)} customers, measured live</span>
      </p>
      <div className={styles.tiles}>
        <div className={styles.wide}>
          <StatTile
            accent
            label="PR-AUC (average precision)"
            value={score(m.average_precision)}
            sub="primary · precision averaged over recall levels"
            visual={<ApBar value={m.average_precision} />}
          />
        </div>
        <StatTile
          label="ROC-AUC"
          value={score(m.roc_auc)}
          sub="chance a churner is scored above a stayer"
        />
        <StatTile
          label="Brier score"
          value={score(m.brier)}
          sub="mean squared gap between probability and outcome; lower is better"
        />
        <StatTile
          label="ECE"
          value={score(measured.ece)}
          sub="gap between stated and observed rates across the bins; lower is better"
        />
      </div>
      <p className={styles.note}>{VALIDATION_NOTE}</p>
    </div>
  );
}

export function TestTiles({ figures }: { figures: Performance }) {
  const test = figures.test;
  if (!test) {
    return (
      <div role="note" aria-label="Test evaluation" className={styles.notRecorded}>
        <p className={styles.notRecordedTitle}>{NO_TEST}</p>
        {figures.test_reason && (
          <p>
            <Coded text={figures.test_reason} />
          </p>
        )}
        <p>
          The validation figures are the only measured evidence here, and they are somewhat
          optimistic: validation set the cut.
        </p>
      </div>
    );
  }
  const m = test.metrics;
  const flagged = m.tp + m.fp;
  return (
    <div role="group" aria-label="Test figures" className={styles.partBlock}>
      <p className={styles.partHead}>
        <Tag>TEST</Tag>{' '}
        <span>
          {int(m.n)} customers, recorded {evaluatedOn(test.evaluated_at)} at the cut{' '}
          {cut(m.threshold)}
        </span>
      </p>
      <div className={styles.tiles}>
        <StatTile
          label="Recall"
          value={recallOf(m.positives, m.recall)}
          sub={churnersFlagged(m.tp, m.positives)}
        />
        <StatTile
          label="Precision"
          value={precisionOf(flagged, m.precision)}
          sub={flaggedOutcomes(m.tp, m.fp)}
        />
        <StatTile
          label="F1"
          value={f1Of(flagged, m.positives, m.f1)}
          sub="balance of precision and recall"
        />
        <div className={styles.dashed}>
          <StatTile label="Accuracy" value={score(m.accuracy)} sub="share classified right" />
        </div>
      </div>
      <p className={styles.note}>{TEST_NOTE}</p>
      <p className={styles.note}>{TEST_SCOPE}</p>
    </div>
  );
}
