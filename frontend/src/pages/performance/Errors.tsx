/**
 * Errors and trade-offs, one partition at a time.
 *
 * Validation: the sweep's cuts, explored with the fader (the keyboard
 * control; the precision–recall chart beside it is a pointer's shortcut),
 * each cut read as the sweep serves it: how many it flags, and its
 * precision, recall and F1. No two-by-two table is drawn for validation.
 *
 * Test: the recorded release's two-by-two table at its one cut, as the
 * record holds it, with the rates the record publishes. No other cut, curve
 * or ranking score of the test partition exists to show.
 */
import type { ModelInfo, Performance } from '../../api/queries';
import { Link } from '../../app/router';
import { useSweepNote } from '../../components/ui/DataSourceNote';
import { Fader } from '../../components/ui/Fader';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut, int } from '../../lib/format';
import {
  NOT_OPTIMAL,
  OUTCOME_NAMES,
  offerCost,
  ONE_CUT,
  readoutAt,
  recordAt,
  TEST_SCOPE,
  unavailableText,
} from './copy';
import { PartitionSwitch } from './Partition';
import { f1Of, indexOfLabel, precisionOf, recallOf, score, type Recorded } from './performance';
import { PrCurve } from './PrCurve';
import { TestTiles } from './Tiles';
import type { Partition } from './tabs';
import styles from './Performance.module.css';

interface Props {
  figures: Performance;
  model: ModelInfo;
  /** The explored point of ``figures.validation``, or -1. */
  index: number;
  /** The fader moved: the address is replaced, not added to. */
  onCut: (index: number) => void;
  /** A cut picked on the chart: one history entry. */
  onPick: (index: number) => void;
  part: Partition;
  onPart: (part: Partition) => void;
}

function Matrix({ record, offer }: { record: Recorded; offer: string | null }) {
  const m = record.metrics;
  const cell = (key: 'tn' | 'fp' | 'fn' | 'tp', note?: string | null) => (
    <td className={styles[key]}>
      <span className={styles.cellCount}>{int(m[key])}</span>{' '}
      <span className={styles.cellShort}>{OUTCOME_NAMES[key].short}</span>{' '}
      <span className={styles.cellName}>{OUTCOME_NAMES[key].long}</span>
      {note && (
        <>
          {' '}
          <span className={styles.cellNote}>{note}</span>
        </>
      )}
    </td>
  );
  return (
    <>
      <table className={styles.matrix}>
        <caption className={styles.matrixCaption}>
          Errors at {cut(m.threshold)}
          <span className="sr">, test record</span>
        </caption>
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
            {cell('tn')}
            {cell('fp', offer)}
          </tr>
          <tr>
            <th scope="row">Churned</th>
            {cell('fn')}
            {cell('tp')}
          </tr>
        </tbody>
      </table>
      <dl className={styles.strip}>
        <div>
          <dt>Precision</dt>
          <dd>{precisionOf(m.tp + m.fp, m.precision)}</dd>
        </div>
        <div>
          <dt>Recall</dt>
          <dd>{recallOf(m.positives, m.recall)}</dd>
        </div>
        <div>
          <dt>F1</dt>
          <dd>{f1Of(m.tp + m.fp, m.positives, m.f1)}</dd>
        </div>
        <div>
          <dt>Accuracy</dt>
          <dd>{score(m.accuracy)}</dd>
        </div>
      </dl>
      <p className={styles.summary}>{recordAt(m.threshold, m.tp, m.fn, m.fp)}</p>
    </>
  );
}

function Explore({
  figures,
  index,
  onCut,
}: {
  figures: Performance;
  index: number;
  onCut: (index: number) => void;
}) {
  const sweepNote = useSweepNote();
  const curve = figures.validation;
  if (!curve) {
    return (
      <p className={styles.unavailable}>{unavailableText(figures.withheld_reason, sweepNote)}</p>
    );
  }
  const point = curve.points[index];
  if (!point) {
    return (
      <p className={styles.unavailable}>
        The served cut is not one of the validation sweep’s cuts.
      </p>
    );
  }
  const served = indexOfLabel(curve.points, cut(figures.threshold));
  const precision = precisionOf(point.flagged, point.precision);
  const recall = recallOf(curve.churners, point.recall);
  const f1 = f1Of(point.flagged, curve.churners, point.f1);
  return (
    <>
      <Fader
        label="Explored cut"
        caption="Customers at or above the cut are flagged."
        values={curve.points.map((p) => p.threshold)}
        index={index}
        onChange={onCut}
        format={cut}
        defaultIndex={served >= 0 ? served : undefined}
        marks={served >= 0 ? [served] : []}
        tone="cyan"
      />
      <dl className={styles.strip}>
        <div>
          <dt>Flagged</dt>
          <dd>{int(point.flagged)}</dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd>{precision}</dd>
        </div>
        <div>
          <dt>Recall</dt>
          <dd>{recall}</dd>
        </div>
        <div>
          <dt>F1</dt>
          <dd>{f1}</dd>
        </div>
      </dl>
      <p className={styles.summary} aria-live="polite">
        {readoutAt(point.threshold, point.flagged, precision, recall, f1)}
      </p>
    </>
  );
}

export function ErrorsTab({ figures, model, index, onCut, onPick, part, onPart }: Props) {
  const test = figures.test;
  const defaults = model.business_defaults;
  const offer = defaults ? offerCost(defaults.retention_offer_cost, defaults.currency) : null;
  const how = (
    <p className={styles.note}>
      How the served cut {cut(figures.threshold)} was set: “{model.threshold_source}”. {NOT_OPTIMAL}{' '}
      <Link to="/simulator">Try other assumptions in the simulator</Link>.
    </p>
  );

  if (part === 'test') {
    return (
      <div className={styles.pair}>
        <Card titleId="errors-title" className={styles.card}>
          <CardHeader
            id="errors-title"
            title="Errors at the recorded cut"
            subtitle="Which mistakes the cut made on the test customers, and how many of each."
            aside={<PartitionSwitch value={part} onChange={onPart} />}
          />
          {test ? (
            <div className={styles.panel}>
              <p className={styles.partHead}>
                <Tag>TEST</Tag> <span>{int(test.metrics.n)} customers, recorded</span>
              </p>
              <Matrix record={test} offer={offer} />
            </div>
          ) : (
            <TestTiles figures={figures} />
          )}
          {how}
        </Card>
        <Card titleId="record-scope-title" className={styles.card}>
          <CardHeader id="record-scope-title" title="What the test record holds" />
          <p className={styles.sentence}>{ONE_CUT}</p>
          <p className={styles.note}>{TEST_SCOPE}</p>
        </Card>
      </div>
    );
  }

  return (
    <div className={styles.pair}>
      <Card titleId="errors-title" className={styles.card}>
        <CardHeader
          id="errors-title"
          title="Explore a cut"
          subtitle="What each validation cut flags, and its precision and recall. Customers at or above it are flagged."
          aside={<PartitionSwitch value={part} onChange={onPart} />}
        />
        <div className={styles.panel}>
          <p className={styles.partHead}>
            <Tag>VALIDATION</Tag>{' '}
            <span>
              {figures.validation
                ? `${int(figures.validation.customers)} customers, the sweep’s cuts`
                : 'the sweep’s cuts'}
            </span>
          </p>
          <Explore figures={figures} index={index} onCut={onCut} />
        </div>
        {how}
      </Card>
      <PrCurve figures={figures} index={index} onPick={onPick} />
    </div>
  );
}
