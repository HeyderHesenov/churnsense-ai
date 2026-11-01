/**
 * Where each figure comes from: the three partitions and what each decided,
 * whether a test evaluation is recorded, and the artifact the figures
 * describe. A reason the server gives is shown as it gives it, with its
 * backticked commands set as code.
 */
import type { ModelInfo, Performance } from '../../api/queries';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { int } from '../../lib/format';
import { evaluatedOn, ROLES, shortSha, withheld } from './copy';
import styles from './Performance.module.css';

/** ``text`` with each `backticked` run as code. */
export function Coded({ text }: { text: string }) {
  return (
    <>{text.split('`').map((part, i) => (i % 2 === 1 ? <code key={i}>{part}</code> : part))}</>
  );
}

interface Props {
  model: ModelInfo;
  figures: Performance;
}

export function Provenance({ model, figures }: Props) {
  const test = figures.test;
  const verified = model.provenance?.verified;
  const sha = figures.model_sha256 ?? model.model_sha256 ?? null;
  return (
    <Card titleId="evidence-title" variant="dense">
      <CardHeader id="evidence-title" title="Where each figure comes from" />
      <dl className={styles.evidence}>
        <div className={styles.partition}>
          <dt>
            <Tag>TRAIN · CV</Tag>
          </dt>
          <dd>
            <span className={styles.partitionSize}>{int(model.n_train)} customers</span>
            <span className={styles.partitionRole}>{ROLES.train}</span>
          </dd>
        </div>
        <div className={styles.partition}>
          <dt>
            <Tag>VALIDATION</Tag>
          </dt>
          <dd>
            <span className={styles.partitionSize}>{int(model.n_validation)} customers</span>
            <span className={styles.partitionRole}>{ROLES.validation}</span>
          </dd>
        </div>
        <div className={styles.partition}>
          <dt>
            <Tag>TEST</Tag>
          </dt>
          <dd>
            {test ? (
              <>
                <span className={styles.partitionSize}>
                  Recorded {evaluatedOn(test.evaluated_at)}
                </span>
                <span className={styles.partitionRole}>{ROLES.recorded}</span>
              </>
            ) : (
              <>
                <span className={styles.partitionSize}>Not recorded</span>
                <span className={styles.partitionRole}>
                  {figures.test_reason && <Coded text={figures.test_reason} />}
                </span>
              </>
            )}
          </dd>
        </div>
        <div className={styles.partition}>
          <dt>
            <Tag>ARTIFACT</Tag>
          </dt>
          <dd>
            <span className={styles.mono}>{sha ? shortSha(sha) : 'unknown'}</span>
            <span className={styles.partitionRole}>
              {verified === undefined
                ? 'Data provenance not served.'
                : verified
                  ? 'The data and the split match the ones it was trained on.'
                  : 'The data or the split differ from the ones it was trained on.'}
            </span>
          </dd>
        </div>
      </dl>
      {figures.withheld_reason && (
        <p role="note" className={styles.withheld}>
          {withheld(figures.withheld_reason)}
        </p>
      )}
    </Card>
  );
}
