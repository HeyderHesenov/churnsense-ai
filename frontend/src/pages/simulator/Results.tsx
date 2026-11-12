/**
 * The cut, and the figures at it. Every figure is a field of the response:
 * the counts measured on validation customers apart from the money simulated
 * from them, the response's own assumptions named first, and, when those are
 * not the assumptions set, a line saying so before any figure.
 */
import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import type { Sweep } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { Card, CardHeader, SimulatedBadge } from '../../components/ui/Surface';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut, int, money, signedMoney } from '../../lib/format';
import { cx } from '../../lib/style';
import { netParts, precisionText, provenance, recallText, statusNote } from './copy';
import { ForAssumptions } from './ForAssumptions';
import type { Selection, Status } from './simulator';
import styles from './Simulator.module.css';

export const RESULTS_ID = 'simulator-results';

interface CutCardProps {
  sweep: Sweep;
  selected: Selection;
  onCut: (index: number) => void;
  onFollow: () => void;
  /** The figures are for other assumptions than those set. */
  stale: boolean;
}

/** The one keyboard control for the cut; the chart is a pointer's shortcut to it. */
export function CutCard({ sweep, selected, onCut, onFollow, stale }: CutCardProps) {
  const point = sweep.points[selected.index];
  return (
    <Card titleId="cut-title" className={styles.panel}>
      <CardHeader
        id="cut-title"
        title="Where to cut"
        subtitle="Customers the model scores at or above the cut would get an offer."
      />
      <ForAssumptions echo={sweep.assumptions} stale={stale} />
      <div className={styles.field}>
        <div className={styles.fieldTop}>
          <label htmlFor="cut" className={styles.label}>
            Decision threshold
          </label>
          <span className={styles.cutValue} aria-hidden="true">
            {point ? cut(point.threshold) : '—'}
          </span>
        </div>
        <input
          id="cut"
          type="range"
          className={styles.slider}
          min={0}
          max={sweep.points.length - 1}
          step={1}
          value={selected.index}
          aria-valuetext={point ? cut(point.threshold) : 'no cut'}
          onChange={(event) => onCut(Number(event.target.value))}
        />
      </div>
      <div className={styles.follow}>
        <span className={styles.followNote}>
          {selected.following ? 'Following the recommendation' : 'Set by hand'}
        </span>
        <Button
          variant="simGhost"
          size="sm"
          aria-disabled={selected.following}
          onClick={() => {
            if (!selected.following) onFollow();
          }}
        >
          Use recommended
        </Button>
      </div>
      <p className={styles.tip}>A click on the net-benefit chart sets the cut there.</p>
    </Card>
  );
}

interface ResultsProps {
  status: Status;
  sweep: Sweep | undefined;
  selected: Selection | null;
  error: Error | null;
  /** The service's reason when it publishes no sweep at all. */
  sweepNote?: string | null;
  onRetry: () => void;
}

/** A response whose recommendation is none of its points: no cut can be drawn from it. */
const MALFORMED =
  'The sweep the API sent has no point at its own recommended cut, so no cut can be shown.';

const WITHHELD =
  'The simulator works on the observed outcomes of validation customers, and validation ' +
  'outcomes are withheld while the partitions cannot be verified against the served model.';

export function Results({ status, sweep, selected, error, sweepNote, onRetry }: ResultsProps) {
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
        <h2 id={RESULTS_ID} tabIndex={-1} className={styles.resultsTitle}>
          {point ? `At ${cut(point.threshold)}` : 'At the cut'}
        </h2>
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
        <div className={styles.groups}>
          <div className={styles.group}>
            <h3 className={styles.groupTitle}>Measured on validation</h3>
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
            </dl>
          </div>
          <div className={cx(styles.group, styles.simGroup)}>
            <h3 className={styles.groupTitle}>Simulated money</h3>
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
        </div>
        <p className={styles.recommended}>
          {`Recommended under these assumptions: ${cut(sweep.recommended.threshold)} · ${signedMoney(sweep.recommended.net_benefit, currency)}`}
        </p>
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
