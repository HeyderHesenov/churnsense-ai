/**
 * The served probability, its band and the flag, placed on the band scale
 * with the threshold. Every figure is the API's; the scale's bands are the
 * served ones and its dashed line the served threshold.
 */
import type { ModelInfo } from '../../api/queries';
import { icons } from '../../components/icons';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut, probability, probabilityPct } from '../../lib/format';
import { Tag } from '../../components/ui/Surface';
import { flagText, gapNote, SOURCES } from './copy';
import styles from './Customer.module.css';

export interface Score {
  probability: number;
  band: string;
  flagged: boolean;
  threshold: number;
}

/**
 * ``stale``: the figures are for inputs typed a moment ago, and the next are
 * on their way; ``invalid``: what is typed cannot be scored; ``missing``: no
 * such customer in the book.
 */
export type ScoreState = 'pending' | 'ready' | 'stale' | 'invalid' | 'failed' | 'missing';

interface PredictionProps {
  state: ScoreState;
  score: Score | undefined;
  /** What the API found wrong with typed inputs (a 422's problems), as text. */
  problems: readonly string[];
  bands: ModelInfo['risk_bands'] | undefined;
  onRetry: () => void;
}

/** The card itself: a retry takes its error away, and the reader stays on the card. */
const PREDICTION_ID = 'prediction';

export function Prediction({ state, score, problems, bands, onRetry }: PredictionProps) {
  return (
    <section
      id={PREDICTION_ID}
      tabIndex={-1}
      aria-label="Prediction"
      aria-busy={state === 'stale'}
      className={styles.prediction}
    >
      <p className={styles.source}>
        <Tag>{SOURCES.output.tag}</Tag>
        <span>{SOURCES.output.text}</span>
      </p>
      {state === 'failed' ? (
        problems.length > 0 ? (
          <div className={styles.refused}>
            <p className={styles.refusedTitle}>The API did not score these inputs:</p>
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </div>
        ) : (
          <ErrorState inline onRetry={onRetry} focusTo={PREDICTION_ID}>
            The prediction could not be loaded.
          </ErrorState>
        )
      ) : state === 'invalid' || !score ? (
        state === 'pending' ? (
          <Skeleton label="Scoring the customer" rows={2} rowHeight={40} />
        ) : (
          <div className={styles.score}>
            <span className={styles.scoreLabel}>Estimated churn probability</span>
            <span className={styles.scoreValue}>—</span>
            <span className={styles.scoreNote}>Scored once every amount can be sent.</span>
          </div>
        )
      ) : (
        <Scored score={score} bands={bands} stale={state === 'stale'} />
      )}
    </section>
  );
}

function Scored({
  score,
  bands,
  stale,
}: {
  score: Score;
  bands: ModelInfo['risk_bands'] | undefined;
  stale: boolean;
}) {
  const p = score.probability;
  // The marker's label leans inward at the ends, so it never leaves the scale.
  const lean = p < 0.06 ? 'start' : p > 0.94 ? 'end' : 'middle';
  return (
    <div className={styles.scored} data-stale={stale}>
      <div className={styles.score}>
        <span className={styles.scoreLabel}>Estimated churn probability</span>
        <span className={styles.scoreValue}>{probabilityPct(p)}</span>
        <span className={styles.chips}>
          <span className={styles.bandChip} data-band={score.band}>
            <span className={styles.swatch} aria-hidden="true" />
            {score.band} band
          </span>
          <span className={styles.flagChip} data-flagged={score.flagged}>
            <icons.flag size={13} />
            {flagText(score.flagged, score.threshold)}
          </span>
        </span>
      </div>
      <div className={styles.scaleBox}>
        <Scale p={p} threshold={score.threshold} band={score.band} bands={bands} lean={lean} />
        <p className={styles.gap}>{gapNote(p, score.threshold, score.flagged)}</p>
      </div>
    </div>
  );
}

/** One character of the marker's mono label, in px (11px type), and the pill's padding. */
const CHAR = 6.7;
const PAD = 6;

/** A place on the scale as a share of its width. */
function at(value: number): string {
  return `${String(Math.min(1, Math.max(0, value)) * 100)}%`;
}

/**
 * The served bands from 0 to 1, the served threshold dashed across them, and
 * the probability's marker above: one SVG with no viewBox, so places are
 * shares of its width and the marker's label keeps its size. The marker and
 * the threshold's label are nested SVGs placed at their share, drawn in px
 * from there; the label leans inward at the ends.
 */
function Scale({
  p,
  threshold,
  band,
  bands,
  lean,
}: {
  p: number;
  threshold: number;
  band: string;
  bands: ModelInfo['risk_bands'] | undefined;
  lean: 'start' | 'middle' | 'end';
}) {
  const label = probability(p);
  const width = label.length * CHAR + 2 * PAD;
  const left = lean === 'start' ? -1 : lean === 'end' ? 1 - width : -width / 2;
  return (
    <svg
      className={styles.scale}
      role="img"
      aria-label={`Probability ${probability(p)} on a scale from 0 to 1; threshold ${cut(threshold)}`}
    >
      {(bands ?? [])
        .filter((b) => Math.min(b.max, 1) > b.min)
        .map((b) => (
          <rect
            key={b.name}
            className={styles.bandRect}
            data-band={b.name}
            data-on={b.name === band}
            x={at(b.min)}
            y="24"
            width={at(Math.min(b.max, 1) - b.min)}
            height="10"
          />
        ))}
      <line
        className={styles.thresholdLine}
        x1={at(threshold)}
        x2={at(threshold)}
        y1="20"
        y2="42"
      />
      <svg x={at(p)} y="0" overflow="visible" data-lean={lean}>
        <rect className={styles.markerPill} x={left} y="0" width={width} height="18" rx="5" />
        <text className={styles.markerLabel} x={left + width / 2} y="13" textAnchor="middle">
          {label}
        </text>
        <line className={styles.markerStem} x1="0" x2="0" y1="18" y2="34" />
      </svg>
      <text className={styles.axisText} x="0" y="58" textAnchor="start">
        0
      </text>
      <text className={styles.axisText} x={at(threshold)} y="58" textAnchor="middle">
        threshold {cut(threshold)}
      </text>
      <text className={styles.axisText} x="100%" y="58" textAnchor="end">
        1.0
      </text>
    </svg>
  );
}
