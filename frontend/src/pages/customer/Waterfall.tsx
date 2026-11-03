/**
 * What moved the estimate: the server's attributions from the average
 * customer to this one, strongest first, and the strongest four in words.
 * The bars are drawn on the probability scale; each row's words carry its
 * meaning, so the bars are for the eye only.
 */
import { useState } from 'react';
import type { Explanation, ExplainResponse } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { Skeleton } from '../../components/ui/States';
import { Segmented, type Option } from '../../components/ui/Toggle';
import { pct, probabilityPct, shownSign, signedPoints } from '../../lib/format';
import { contributionLabel, valueText } from '../../lib/labels';
import { cssVars } from '../../lib/style';
import {
  EXPLAIN_BLOCKED,
  EXPLAIN_FAILED,
  EXPLAIN_UNAVAILABLE,
  otherInputs,
  sentence,
  SENTENCES_NOTE,
  showAll,
  waterfallNote,
} from './copy';
import { bar, waterfall } from './customer';
import styles from './Customer.module.css';

/**
 * ``stale``: the attributions are for inputs typed a moment ago; ``blocked``:
 * nothing can be asked for until the inputs can be scored; ``waiting``: they
 * are asked for once the probability is in; ``unavailable``: the server can
 * never give them (no verified training background).
 */
export type ExplainState =
  'pending' | 'waiting' | 'blocked' | 'ready' | 'stale' | 'failed' | 'unavailable';

type Shown = '5' | '8' | 'all';

interface WaterfallProps {
  state: ExplainState;
  explanation: Explanation | undefined;
  response: ExplainResponse | undefined;
  retry: () => void;
  /** The served band, for the last bar, once the score and the attributions agree. */
  band: string | undefined;
  threshold: number | undefined;
}

export function Waterfall({
  state,
  explanation,
  response,
  retry,
  band,
  threshold,
}: WaterfallProps) {
  const [shown, setShown] = useState<Shown>('8');
  const drawn = (state === 'ready' || state === 'stale') && explanation && response;
  const count = explanation?.contributions.length;
  const options: Option<Shown>[] = [
    { value: '5', label: 'Top 5' },
    { value: '8', label: 'Top 8' },
    // Always there, so the reader's choice holds while the next customer's load.
    { value: 'all', label: count === undefined ? 'All' : showAll(count) },
  ];
  return (
    <section aria-labelledby="waterfall-title" className={styles.waterfallCard}>
      <div className={styles.waterfallHead}>
        <div>
          <h2 id="waterfall-title" tabIndex={-1} className={styles.cardTitle}>
            Contribution waterfall
          </h2>
          <p className={styles.subtitle}>
            Which attributes moved the estimate, and by how much? SHAP, in percentage points.
          </p>
        </div>
        <div className={styles.waterfallTools}>
          <span className={styles.legend} aria-hidden="true">
            <span data-direction="raises">raises</span>
            <span data-direction="lowers">lowers</span>
          </span>
          <Segmented
            label="Contributions shown"
            small
            value={shown}
            onChange={setShown}
            options={options}
          />
        </div>
      </div>
      {drawn ? (
        <>
          <Rows
            explanation={explanation}
            shown={shown === 'all' ? explanation.contributions.length : Number(shown)}
            band={state === 'ready' ? band : undefined}
            threshold={threshold}
            stale={state === 'stale'}
          />
          {threshold !== undefined && (
            <p className={styles.note}>{waterfallNote(response, threshold)}</p>
          )}
        </>
      ) : state === 'failed' ? (
        <div className={styles.explainState}>
          <p role="status">{EXPLAIN_FAILED}</p>
          <Button
            size="sm"
            onClick={() => {
              retry();
              // The button goes away while it asks: the reader stays on the card.
              document.getElementById('waterfall-title')?.focus();
            }}
          >
            Ask for the attributions again
          </Button>
        </div>
      ) : state === 'unavailable' ? (
        <p role="status" className={styles.explainState}>
          {EXPLAIN_UNAVAILABLE}
        </p>
      ) : state === 'blocked' ? (
        <p className={styles.explainState}>{EXPLAIN_BLOCKED}</p>
      ) : (
        <Skeleton label="Computing attributions" rows={4} rowHeight={24} />
      )}
    </section>
  );
}

/** "+30.8" raises, "−16.1" lowers; one that rounds to nothing is coloured as neither. */
function direction(fraction: number): 'raises' | 'lowers' | 'none' {
  const sign = shownSign(fraction);
  return sign > 0 ? 'raises' : sign < 0 ? 'lowers' : 'none';
}

function Rows({
  explanation,
  shown,
  band,
  threshold,
  stale,
}: {
  explanation: Explanation;
  shown: number;
  band: string | undefined;
  threshold: number | undefined;
  stale: boolean;
}) {
  const drawn = waterfall(explanation, shown);
  const line = threshold === undefined ? null : <span className={styles.wfThreshold} />;
  const track = (start: number, end: number, kind: string, band?: string, ruled = true) => {
    const { left, width } = bar(start, end);
    return (
      <span className={styles.wfTrack} aria-hidden="true">
        <span
          className={styles.wfBar}
          data-direction={kind}
          data-band={band}
          style={cssVars({ '--l': left, '--w': width })}
        />
        {ruled && line}
      </span>
    );
  };
  return (
    <ol
      aria-label="Contributions"
      aria-busy={stale}
      className={styles.waterfall}
      data-stale={stale}
      style={threshold === undefined ? undefined : cssVars({ '--t': threshold })}
    >
      <li className={styles.wfRow}>
        <span className={styles.wfLabel}>
          Average customer <span className={styles.wfTag}>base</span>
        </span>
        {track(0, drawn.base, 'base', undefined, false)}
        <span className={styles.wfValue}>{pct(drawn.base)}</span>
      </li>
      {drawn.steps.map((step) => (
        <li key={step.feature} className={styles.wfRow}>
          <span className={styles.wfLabel}>
            {contributionLabel(step.label)}{' '}
            <span className={styles.wfMuted}>· {valueText(step.feature, step.value)}</span>
          </span>
          {track(step.start, step.end, direction(step.shap))}
          <span className={styles.wfValue} data-direction={direction(step.shap)}>
            {signedPoints(step.shap)}
          </span>
        </li>
      ))}
      {drawn.rest && (
        <li className={styles.wfRow} data-rest="true">
          <span className={styles.wfLabel}>
            {otherInputs(drawn.rest.count)} <span className={styles.wfMuted}>· combined</span>
          </span>
          {track(drawn.rest.start, drawn.rest.end, direction(drawn.rest.shap))}
          <span className={styles.wfValue} data-direction={direction(drawn.rest.shap)}>
            {signedPoints(drawn.rest.shap)}
          </span>
        </li>
      )}
      <li className={styles.wfRow} data-total="true">
        <span className={styles.wfLabel}>This customer</span>
        {track(0, drawn.total, 'total', band)}
        <span className={styles.wfValue}>{probabilityPct(drawn.total)}</span>
      </li>
    </ol>
  );
}

/** The strongest four attributions as sentences, with what they do not establish. */
export function Sentences({
  state,
  explanation,
}: {
  state: ExplainState;
  explanation: Explanation | undefined;
}) {
  const shown = (state === 'ready' || state === 'stale') && explanation;
  return (
    <section aria-labelledby="sentences-title" className={styles.sentences}>
      <h2 id="sentences-title" className={styles.cardTitle}>
        In plain sentences
      </h2>
      {shown ? (
        <ul className={styles.sentenceList} data-stale={state === 'stale'}>
          {explanation.contributions.slice(0, 4).map((contribution) => {
            const words = sentence(contribution);
            return (
              <li key={contribution.feature}>
                <b>{words.label}</b> ({words.value}) {words.text}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className={styles.note}>The strongest attributions are written here once they are in.</p>
      )}
      <p className={styles.sentencesNote}>{SENTENCES_NOTE}</p>
    </section>
  );
}
