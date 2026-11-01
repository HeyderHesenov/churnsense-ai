/**
 * Errors at a cut: the four outcomes a cut gives, from served counts. The
 * validation sweep can be explored with a slider (the keyboard control; the
 * precision–recall chart is a pointer's shortcut to it), starting at the
 * served cut; a recorded test evaluation has its own tab at the record's cut.
 */
import type { ModelInfo, Performance } from '../../api/queries';
import { Link } from '../../app/router';
import { Card, CardHeader } from '../../components/ui/Surface';
import { Tabs } from '../../components/ui/Tabs';
import { cut, int } from '../../lib/format';
import { CELLS, errorsAt, NOT_OPTIMAL, offerCost, UNAVAILABLE, withheld } from './copy';
import { f1Of, precisionOf, recallOf } from './performance';
import styles from './Performance.module.css';

interface Counts {
  threshold: number;
  tn: number;
  fp: number;
  fn: number;
  tp: number;
  churners: number;
  precision: number;
  recall: number;
  f1: number;
}

export type View = 'validation' | 'test';

interface Props {
  figures: Performance;
  model: ModelInfo;
  /** The explored point of ``figures.validation``, or -1. */
  index: number;
  onCut: (index: number) => void;
  /** Which partition the card shows; the page owns it, so a cut picked on the chart shows. */
  view: View;
  onView: (view: View) => void;
}

function Matrix({ counts, offer }: { counts: Counts; offer: string | null }) {
  const flagged = counts.tp + counts.fp;
  return (
    <>
      <table className={styles.matrix}>
        <caption className={styles.matrixCaption}>Errors at {cut(counts.threshold)}</caption>
        <thead>
          <tr>
            <td />
            <th scope="col">Predicted stay</th>
            <th scope="col">Predicted churn</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Stayed</th>
            <td className={styles.tn}>
              <span className={styles.cellCount}>{int(counts.tn)}</span>
              <span className={styles.cellName}>{CELLS.tn}</span>
            </td>
            <td className={styles.fp}>
              <span className={styles.cellCount}>{int(counts.fp)}</span>
              <span className={styles.cellName}>{CELLS.fp}</span>
              {offer && <span className={styles.cellNote}>{offer}</span>}
            </td>
          </tr>
          <tr>
            <th scope="row">Churned</th>
            <td className={styles.fn}>
              <span className={styles.cellCount}>{int(counts.fn)}</span>
              <span className={styles.cellName}>{CELLS.fn}</span>
            </td>
            <td className={styles.tp}>
              <span className={styles.cellCount}>{int(counts.tp)}</span>
              <span className={styles.cellName}>{CELLS.tp}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <dl className={styles.strip}>
        <div>
          <dt>Flagged</dt>
          <dd>{int(flagged)}</dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd>{precisionOf(flagged, counts.precision)}</dd>
        </div>
        <div>
          <dt>Recall</dt>
          <dd>{recallOf(counts.churners, counts.recall)}</dd>
        </div>
        <div>
          <dt>F1</dt>
          <dd>{f1Of(flagged, counts.churners, counts.f1)}</dd>
        </div>
      </dl>
      <p className={styles.summary} aria-live="polite">
        {errorsAt(counts.threshold, flagged, counts.fp, counts.fn)}
      </p>
    </>
  );
}

export function Confusion({ figures, model, index, onCut, view, onView }: Props) {
  const test = figures.test;
  const curve = figures.validation;
  const point = curve?.points[index];
  const defaults = model.business_defaults;
  const offer = defaults ? offerCost(defaults.retention_offer_cost, defaults.currency) : null;
  const shown: View = test ? view : 'validation';

  let panel;
  if (shown === 'test' && test) {
    const m = test.metrics;
    panel = (
      <Matrix
        counts={{
          threshold: m.threshold,
          tn: m.tn,
          fp: m.fp,
          fn: m.fn,
          tp: m.tp,
          churners: m.positives,
          precision: m.precision,
          recall: m.recall,
          f1: m.f1,
        }}
        offer={offer}
      />
    );
  } else if (!curve) {
    panel = (
      <p className={styles.unavailable}>
        {figures.withheld_reason ? withheld(figures.withheld_reason) : UNAVAILABLE}
      </p>
    );
  } else if (!point) {
    panel = (
      <p className={styles.unavailable}>
        The served cut is not one of the validation sweep’s cuts.
      </p>
    );
  } else {
    panel = (
      <>
        <div className={styles.field}>
          <div className={styles.fieldTop}>
            <label htmlFor="explored-cut" className={styles.label}>
              Explored cut
            </label>
            <span className={styles.cutValue} aria-hidden="true">
              {cut(point.threshold)}
            </span>
          </div>
          <input
            id="explored-cut"
            type="range"
            className={styles.slider}
            min={0}
            max={curve.points.length - 1}
            step={1}
            value={index}
            aria-valuetext={`threshold ${cut(point.threshold)}`}
            onChange={(event) => onCut(Number(event.target.value))}
          />
        </div>
        <Matrix
          counts={{
            threshold: point.threshold,
            tn: point.true_negatives,
            fp: point.false_positives,
            fn: point.false_negatives,
            tp: point.true_positives,
            churners: curve.churners,
            precision: point.precision,
            recall: point.recall,
            f1: point.f1,
          }}
          offer={offer}
        />
      </>
    );
  }

  return (
    <Card titleId="errors-title" className={styles.card}>
      <CardHeader
        id="errors-title"
        title="Errors at a cut"
        subtitle="Which mistakes does a cut make, and how many of each? Customers at or above it are flagged."
      />
      {test ? (
        <Tabs
          label="Partition for the errors"
          tabs={[
            { value: 'validation', label: 'Validation · explore' },
            { value: 'test', label: `Test · recorded at ${cut(test.metrics.threshold)}` },
          ]}
          value={shown}
          onChange={onView}
        >
          <div className={styles.panel}>{panel}</div>
        </Tabs>
      ) : (
        <div className={styles.panel}>{panel}</div>
      )}
      <p className={styles.note}>
        How the served cut {cut(figures.threshold)} was set: “{model.threshold_source}”.{' '}
        {NOT_OPTIMAL} <Link to="/simulator">Try other assumptions in the simulator</Link>.
      </p>
    </Card>
  );
}
