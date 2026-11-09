/**
 * One book customer, placed on the cached figures. The examples are links
 * (a choice is an address and a history entry); a typed ID is placed only
 * when submitted. Each example shows its ID and served probability, never
 * its outcome. What the customer's own explanation says is listed here; the
 * full waterfall is on the single-customer page.
 */
import { useState } from 'react';
import { useExamples, type Explanation, type GlobalExplanation } from '../../api/queries';
import { Link } from '../../app/router';
import { Button } from '../../components/ui/Button';
import { Skeleton } from '../../components/ui/States';
import { Card, CardHeader } from '../../components/ui/Surface';
import { cleanCustomerId, MAX_CUSTOMER_ID } from '../../lib/customerId';
import { pct, probabilityPct, signedPoints } from '../../lib/format';
import { valueText } from '../../lib/labels';
import {
  CARDS,
  EXPLAIN_FAILED,
  EXPLAIN_UNAVAILABLE,
  featureName,
  incomparable,
  notFound,
  PROVENANCE_UNVERIFIED,
  ringNote,
  ROUNDING_NOTE,
} from './copy';
import { strongest, type Comparability } from './explain';
import styles from './Explain.module.css';

export const CUSTOMER_TITLE_ID = 'customer-title';

/** Where the customer named in the address stands. */
export type Asked =
  | { kind: 'none' }
  | { kind: 'unverified' }
  | { kind: 'unavailable' }
  | { kind: 'pending' }
  | { kind: 'notFound' }
  | { kind: 'failed'; retry: () => void }
  | { kind: 'ready'; explanation: Explanation; comparable: Comparability };

interface Props {
  id: string;
  asked: Asked;
  global: GlobalExplanation;
  /** No customer can be explained: the reason is in ``asked``. */
  blocked: boolean;
  href: (id: string) => string;
  onPlace: (id: string) => void;
  onClear: () => void;
  /** Ask for the model and every attribution again: a customer was explained by another one. */
  onReload: () => void;
}

export function CustomerCard({
  id,
  asked,
  global,
  blocked,
  href,
  onPlace,
  onClear,
  onReload,
}: Props) {
  const examples = useExamples();
  const [draft, setDraft] = useState('');

  return (
    <Card titleId={CUSTOMER_TITLE_ID} variant="dense">
      <CardHeader
        id={CUSTOMER_TITLE_ID}
        title={CARDS.customer.title}
        subtitle={CARDS.customer.subtitle}
        focusable
      />
      <div className={styles.placer}>
        {examples.data && examples.data.customers.length > 0 && (
          <nav aria-label="Example customers">
            <ul className={styles.picks}>
              {examples.data.customers.map((c) => (
                <li key={c.id}>
                  {blocked ? (
                    <span className={styles.pick}>
                      <span className={styles.mono}>{c.id}</span>
                      <span className={styles.pickP}>{probabilityPct(c.churn_probability)}</span>
                    </span>
                  ) : (
                    <Link
                      to={href(c.id)}
                      className={styles.pick}
                      aria-current={c.id === id ? 'page' : undefined}
                    >
                      <span className={styles.mono}>{c.id}</span>
                      <span className={styles.pickP}>{probabilityPct(c.churn_probability)}</span>
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </nav>
        )}
        <form
          className={styles.idForm}
          onSubmit={(event) => {
            event.preventDefault();
            // Placed only when submitted, as a history entry; what no ID holds is dropped.
            const cleaned = cleanCustomerId(draft);
            if (cleaned !== '') onPlace(cleaned);
          }}
        >
          <label htmlFor="explain-id" className={styles.idLabel}>
            Customer ID
          </label>
          <input
            id="explain-id"
            className={styles.idInput}
            value={draft}
            maxLength={MAX_CUSTOMER_ID * 2}
            autoComplete="off"
            spellCheck={false}
            disabled={blocked}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button size="md" type="submit" disabled={blocked}>
            Place customer
          </Button>
          {id !== '' && (
            <Button size="md" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          )}
        </form>
      </div>
      <Status id={id} asked={asked} global={global} onReload={onReload} />
    </Card>
  );
}

function Status({
  id,
  asked,
  global,
  onReload,
}: {
  id: string;
  asked: Asked;
  global: GlobalExplanation;
  onReload: () => void;
}) {
  switch (asked.kind) {
    case 'none':
      return (
        <p role="status" className={styles.note}>
          {CARDS.customer.prompt}
        </p>
      );
    case 'unverified':
      return (
        <p role="status" className={styles.note}>
          {PROVENANCE_UNVERIFIED}
        </p>
      );
    case 'unavailable':
      return (
        <p role="status" className={styles.note}>
          {EXPLAIN_UNAVAILABLE}
        </p>
      );
    case 'pending':
      return <Skeleton label={`Loading the attributions of ${id}`} rows={2} />;
    case 'notFound':
      return (
        <p role="status" className={styles.note}>
          {notFound(id)}
        </p>
      );
    case 'failed':
      return (
        <div role="alert" className={styles.failed}>
          <p>{EXPLAIN_FAILED}</p>
          <Button
            size="xs"
            onClick={() => {
              asked.retry();
              // The button goes with the alert while the request runs: the focus waits on the card.
              document.getElementById(CUSTOMER_TITLE_ID)?.focus();
            }}
          >
            Ask for the attributions again
          </Button>
        </div>
      );
    case 'ready':
      // Explained by another model than the figures describe: its numbers are not shown
      // beside them, and the page can be described again.
      if (asked.comparable === 'artifact')
        return (
          <div role="alert" className={styles.failed}>
            <p>{incomparable('artifact')}</p>
            <Button size="xs" onClick={onReload}>
              Reload figures
            </Button>
          </div>
        );
      return (
        <Summary
          id={id}
          explanation={asked.explanation}
          comparable={asked.comparable}
          global={global}
        />
      );
  }
}

function Summary({
  id,
  explanation,
  comparable,
  global,
}: {
  id: string;
  explanation: Explanation;
  comparable: Comparability;
  global: GlobalExplanation;
}) {
  const { raises, lowers } = strongest(explanation, 3);
  const named = (list: Explanation['contributions']) =>
    list.length === 0
      ? 'none'
      : list
          .map(
            (c) =>
              `${global.labels[c.feature] ?? featureName(c.feature)} ${signedPoints(c.shap_value)}`,
          )
          .join(', ');
  return (
    <div className={styles.summary}>
      <dl className={styles.figures}>
        <div>
          <dt>Base value</dt>
          <dd>{pct(explanation.base_value)}</dd>
        </div>
        <div>
          <dt>Served probability</dt>
          <dd>{probabilityPct(explanation.churn_probability)}</dd>
        </div>
        <div>
          <dt>Raises the estimate most (pp)</dt>
          <dd>{named(raises)}</dd>
        </div>
        <div>
          <dt>Lowers it most (pp)</dt>
          <dd>{named(lowers)}</dd>
        </div>
      </dl>
      <p role="status" className={styles.note}>
        {comparable === 'ok' ? ringNote(id, global) : incomparable(comparable)}
      </p>
      <div className={styles.contributions}>
        <table className={styles.table}>
          <caption className="sr">{`${id}: every contribution`}</caption>
          <thead>
            <tr>
              <th scope="col">Attribute</th>
              <th scope="col">Value</th>
              <th scope="col">Contribution (pp)</th>
            </tr>
          </thead>
          <tbody>
            {explanation.contributions.map((c) => (
              <tr key={c.feature}>
                <th scope="row">{global.labels[c.feature] ?? featureName(c.feature)}</th>
                <td>{valueText(c.feature, c.value)}</td>
                <td className={styles.num}>{signedPoints(c.shap_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.note}>
        {ROUNDING_NOTE}{' '}
        <Link to={`/customer?id=${encodeURIComponent(id)}`} className={styles.link}>
          See the full waterfall for {id}
        </Link>
      </p>
    </div>
  );
}
