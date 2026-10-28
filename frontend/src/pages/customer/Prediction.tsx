/**
 * The served probability, its band and the flag, placed on the band scale
 * with the threshold. Every figure is the API's; the scale's bands are the
 * served ones and its dashed line the served threshold.
 */
import type { ModelInfo } from '../../api/queries';
import { icons } from '../../components/icons';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut, probability, probabilityPct } from '../../lib/format';
import { cssVars } from '../../lib/style';
import { flagText, gapNote } from './copy';
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
      <div className={styles.scaleBox} style={cssVars({ '--p': p, '--t': score.threshold })}>
        <div
          className={styles.scale}
          role="img"
          aria-label={`Probability ${probability(p)} on a scale from 0 to 1; threshold ${cut(score.threshold)}`}
        >
          <span className={styles.marker} data-lean={lean}>
            <span className={styles.markerLabel}>{probability(p)}</span>
            <span className={styles.markerStem} />
          </span>
          <span className={styles.bands}>
            {(bands ?? []).map((band) => (
              <span
                key={band.name}
                data-band={band.name}
                data-on={band.name === score.band}
                style={cssVars({ '--w': Math.min(band.max, 1) - band.min })}
              />
            ))}
          </span>
          <span className={styles.thresholdLine} />
        </div>
        <div className={styles.axis} aria-hidden="true">
          <span>0</span>
          <span className={styles.axisThreshold}>threshold {cut(score.threshold)}</span>
          <span>1.0</span>
        </div>
        <p className={styles.gap}>{gapNote(p, score.threshold, score.flagged)}</p>
      </div>
    </div>
  );
}
