/**
 * What ``POST /api/predict`` answered for the inputs shown, field by field
 * in the order the API sent them: the same number the page draws, from the
 * one prediction path.
 */
import { Fragment } from 'react';
import type { Prediction } from '../../api/queries';
import { Skeleton } from '../../components/ui/States';
import { panelCaption } from './copy';
import styles from './Customer.module.css';

/**
 * ``prediction`` is the answer for exactly the inputs shown, or nothing while
 * it is not; ``answering`` says whether one is on its way or none will come
 * (the inputs cannot be scored, or the request failed).
 */
export function ApiPanel({
  prediction,
  answering,
}: {
  prediction: Prediction | undefined;
  answering: boolean;
}) {
  const entries = prediction ? Object.entries(prediction) : [];
  return (
    <section aria-labelledby="api-title" className={styles.api}>
      <div className={styles.apiHead}>
        <h2 id="api-title" className={styles.cardTitle}>
          API response
        </h2>
        {prediction && <span className={styles.ok}>200 OK</span>}
      </div>
      {prediction ? (
        <pre className={styles.json}>
          {'{\n'}
          {entries.map(([key, value], index) => (
            <Fragment key={key}>
              {'  '}
              <span className={styles.jsonKey}>{JSON.stringify(key)}</span>
              {`: ${JSON.stringify(value)}${index < entries.length - 1 ? ',' : ''}\n`}
            </Fragment>
          ))}
          {'}'}
        </pre>
      ) : answering ? (
        <Skeleton label="Waiting for the API" rows={4} rowHeight={16} />
      ) : (
        <p className={styles.none}>No response for these inputs.</p>
      )}
      <span className={styles.caption}>{panelCaption(window.location.host)}</span>
    </section>
  );
}
