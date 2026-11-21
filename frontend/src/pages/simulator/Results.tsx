/**
 * Results & impact: the figures at the cut. Every figure is a field of the
 * response: the money simulated from the assumptions first (the impact, under
 * its SIMULATED tag), then the counts measured on validation customers under
 * theirs, the response's own assumptions named before any figure, and, when
 * those are not the assumptions set, a line saying so.
 */
import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import type { Sweep } from '../../api/queries';
import { SimulatedBadge, Tag } from '../../components/ui/Surface';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut, int, money, signedMoney } from '../../lib/format';
import { cx } from '../../lib/style';
import { f1Text, netParts, precisionText, provenance, recallText, statusNote } from './copy';
import type { Selection, Status } from './simulator';
import styles from './Simulator.module.css';

export const RESULTS_ID = 'simulator-results';

interface ResultsProps {
  status: Status;
  sweep: Sweep | undefined;
  selected: Selection | null;
  error: Error | null;
  /** The service's reason when it publishes no sweep at all. */
  sweepNote?: string | null;
  onRetry: () => void;
  /** The pin control, shown under the figures. */
  pin?: ReactNode;
}

/** A response whose recommendation is none of its points: no cut can be drawn from it. */
const MALFORMED =
  'The sweep the API sent has no point at its own recommended cut, so no cut can be shown.';

const WITHHELD =
  'The simulator works on the observed outcomes of validation customers, and validation ' +
  'outcomes are withheld while the partitions cannot be verified against the served model.';

export function Results({ status, sweep, selected, error, sweepNote, onRetry, pin }: ResultsProps) {
  const point = sweep && selected ? sweep.points[selected.index] : undefined;
  const shown = point !== undefined && status !== 'failed' && status !== 'withheld';
  const note = statusNote(status, shown);
  return (
    <section
      aria-labelledby={RESULTS_ID}
      aria-busy={status === 'updating' || status === 'loading'}
      className={cx(styles.results, shown && status !== 'current' && styles.stale)}
    >
      <div className={styles.resultsTop}>
        <div>
          <p className={styles.resultsEyebrow}>Results &amp; impact</p>
          <h2 id={RESULTS_ID} tabIndex={-1} className={styles.resultsTitle}>
            {point ? `At ${cut(point.threshold)}` : 'At the cut'}
          </h2>
        </div>
        <SimulatedBadge />
      </div>
      {note && (
        <p className={styles.statusNote} data-status={status}>
          {note}
        </p>
      )}
      {/* Always there, so its first words are announced as a change. */}
      <div role="status" className="sr">
        {sweep && shown ? [note, provenance(sweep)].filter(Boolean).join(' ') : (note ?? '')}
      </div>
      {body()}
    </section>
  );

  function body(): ReactNode {
    if (status === 'withheld') return <p className={styles.withheld}>{sweepNote ?? WITHHELD}</p>;
    if (status === 'failed') return <Failure error={error} onRetry={onRetry} />;
    if (!sweep) {
      return status === 'unsent' ? null : <Skeleton label="Loading the sweep" rows={4} />;
    }
    if (!point) return <p className={styles.withheld}>{MALFORMED}</p>;
    const { amount, word } = netParts(point.net_benefit, sweep.assumptions.currency);
    const currency = sweep.assumptions.currency;
    return (
      <>
        <p className={styles.provenance}>{provenance(sweep)}</p>
        <div className={cx(styles.group, styles.simGroup)}>
          <div className={styles.groupHead}>
            <h3 className={styles.groupTitle}>Simulated money</h3>
            <SimulatedBadge />
          </div>
          <p className={styles.groupNote}>Your assumptions applied to the measured counts.</p>
          <dl className={styles.figures}>
            <Figure term="Net benefit" wide>
              <span className={styles.net} data-sign={word}>
                {amount}
              </span>{' '}
              <span className={styles.netWord}>{word}</span>
            </Figure>
            <Figure term="Retained value">{money(point.retained_value, currency)}</Figure>
            <Figure term="Campaign cost">{money(point.intervention_cost, currency)}</Figure>
          </dl>
        </div>
        <div className={styles.group}>
          <div className={styles.groupHead}>
            <h3 className={styles.groupTitle}>Measured on validation</h3>
            <Tag>VALIDATION</Tag>
          </div>
          <p className={styles.groupNote}>
            The model’s flags at this cut against what these customers did.
          </p>
          <dl className={styles.figures}>
            <Figure term="Flagged">
              {int(point.flagged)}
              <span className={styles.of}> of {int(sweep.customers)}</span>
            </Figure>
            <Figure term="Churners flagged">
              {int(point.true_positives)}
              <span className={styles.of}> of {int(sweep.churners)}</span>
            </Figure>
            <Figure term="Churners missed">{int(point.false_negatives)}</Figure>
            <Figure term="Flagged customers who stayed">{int(point.false_positives)}</Figure>
            <Figure term="Precision">{precisionText(point)}</Figure>
            <Figure term="Recall">{recallText(point, sweep.churners)}</Figure>
            <Figure term="F1">{f1Text(point, sweep.churners)}</Figure>
          </dl>
        </div>
        <p className={styles.recommended}>
          {`Recommended under these assumptions: ${cut(sweep.recommended.threshold)} · ${signedMoney(sweep.recommended.net_benefit, currency)}`}
        </p>
        {pin}
        <p className={styles.disclaimer}>{sweep.disclaimer}</p>
      </>
    );
  }
}

function Figure({
  term,
  wide = false,
  children,
}: {
  term: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cx(styles.figure, wide && styles.wide)}>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** A refusal lists what the API refused; any other failure can be tried again. */
function Failure({ error, onRetry }: { error: Error | null; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 422) {
    return (
      <div role="alert" className={styles.refused}>
        {error.problems.length === 0 ? (
          <p>The API refused these assumptions.</p>
        ) : (
          <>
            <p>The API refused these assumptions:</p>
            <ul>
              {error.problems.map((problem, i) => (
                // By place: the API may list one problem twice, and the list never reorders.
                <li key={i}>{problem}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    );
  }
  return (
    <ErrorState inline onRetry={onRetry} focusTo={RESULTS_ID}>
      The sweep for these assumptions could not be loaded.
    </ErrorState>
  );
}
