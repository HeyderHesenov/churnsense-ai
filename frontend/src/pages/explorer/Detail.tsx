/**
 * One customer beside the list: the served probability and band, ten of the
 * model's inputs, and, on request, what moved the score (the server's
 * attributions). Previous and next walk the filtered, sorted list.
 */
import { ApiError } from '../../api/client';
import {
  useCustomer,
  useExplanations,
  type CustomerDetail,
  type CustomerFeatures,
  type ExplainResponse,
} from '../../api/queries';
import { icons } from '../../components/icons';
import { BandLabel, OutcomePill } from '../../components/ui/Badges';
import { Button, IconButton } from '../../components/ui/Button';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cents, cut, probabilityPct, signedPoints } from '../../lib/format';
import { categoryValue, COMPACT, isFeature } from '../../lib/labels';
import { cssVars } from '../../lib/style';
import { explanationMeta, explanationNote } from './copy';
import { DETAIL_ID } from './explorer';
import styles from './Detail.module.css';

/** The inputs the panel lists, in the artboard's order. */
const ATTRIBUTES = [
  'tenure',
  'Contract',
  'MonthlyCharges',
  'TotalCharges',
  'InternetService',
  'PaymentMethod',
  'TechSupport',
  'OnlineSecurity',
  'PaperlessBilling',
  'SeniorCitizen',
] as const satisfies readonly (keyof CustomerFeatures)[];

function attribute(key: (typeof ATTRIBUTES)[number], features: CustomerFeatures): string {
  if (key === 'tenure') return `${String(features.tenure)} mo`;
  if (key === 'MonthlyCharges' || key === 'TotalCharges') return cents(features[key]);
  if (key === 'SeniorCitizen') return features.SeniorCitizen === '1' ? 'Yes' : 'No';
  return categoryValue(features[key]);
}

/** How many attributions the panel draws: the strongest, as the API orders them. */
const SHOWN = 7;

interface DetailProps {
  id: string;
  /** Whether previous and next can go anywhere from here. */
  canStep: boolean;
  /** A step to another page is fetching it. */
  stepping: boolean;
  /** The way the last step from this customer went, if it failed. */
  failed: 1 | -1 | null;
  /** The customer is not on the page the list shows. */
  offList: boolean;
  onStep: (direction: 1 | -1) => void;
  onClose: () => void;
  /** The reader has asked for this customer's attributions. */
  explained: boolean;
  onExplain: () => void;
}

/**
 * Previous and next are never disabled outright: a focused button that
 * became disabled would drop the focus. With nowhere to go they say so (and
 * ``onStep`` does nothing); while a step loads, another press makes a new step.
 */
export function Detail({
  id,
  canStep,
  stepping,
  failed,
  offList,
  onStep,
  onClose,
  explained,
  onExplain,
}: DetailProps) {
  const customer = useCustomer(id);
  const missing = customer.error instanceof ApiError && customer.error.status === 404;
  return (
    <aside
      id={DETAIL_ID}
      aria-label="Customer detail"
      aria-busy={stepping}
      className={styles.detail}
    >
      <div className={styles.head}>
        <h2 id="detail-title" tabIndex={-1} className={styles.title}>
          {id}
        </h2>
        <div className={styles.actions}>
          <IconButton
            label="Previous customer in the list"
            size="sm"
            aria-disabled={!canStep}
            onClick={() => onStep(-1)}
          >
            <icons.chevronLeft size={14} />
          </IconButton>
          <IconButton
            label="Next customer in the list"
            size="sm"
            aria-disabled={!canStep}
            onClick={() => onStep(1)}
          >
            <icons.chevronRight size={14} />
          </IconButton>
          <IconButton label="Close detail" size="sm" onClick={onClose}>
            <icons.close size={13} />
          </IconButton>
        </div>
      </div>
      {offList && (
        <p className={styles.note}>
          Previous and next walk the rows shown; this customer is not among them.
        </p>
      )}
      <p role="status" className={failed ? styles.note : 'sr'}>
        {failed && `The ${failed === 1 ? 'next' : 'previous'} customer could not be loaded.`}
      </p>
      {customer.isPending ? (
        <Skeleton label="Loading the customer" rows={4} />
      ) : missing ? (
        <p role="status" className={styles.note}>
          There is no customer {id} in the book.
        </p>
      ) : customer.isError ? (
        <ErrorState inline onRetry={() => void customer.refetch()}>
          This customer could not be loaded.
        </ErrorState>
      ) : (
        <Profile detail={customer.data} explained={explained} onExplain={onExplain} />
      )}
    </aside>
  );
}

function Profile({
  detail,
  explained,
  onExplain,
}: {
  detail: CustomerDetail;
  explained: boolean;
  onExplain: () => void;
}) {
  const response = useExplanations(explained ? [detail.id] : []);
  const explanation = response.data?.explanations.find((e) => e.customer_id === detail.id);
  return (
    <>
      <div className={styles.score}>
        <div>
          <p className={styles.label}>Churn probability</p>
          <p className={styles.probability}>{probabilityPct(detail.churn_probability)}</p>
        </div>
        <div className={styles.band}>
          <BandLabel band={detail.risk_band} inked />
          <span className={detail.flagged ? styles.flagged : styles.muted}>
            {detail.flagged ? `Flagged at ${cut(detail.threshold)}` : 'Not flagged'}
          </span>
        </div>
      </div>

      <dl className={styles.attributes}>
        {ATTRIBUTES.map((key) => (
          <div key={key} className={styles.attribute}>
            <dt>{COMPACT[key]}</dt>
            <dd>{attribute(key, detail.features)}</dd>
          </div>
        ))}
      </dl>

      <div className={styles.moved}>
        <h3 className={styles.movedTitle}>What moved this score</h3>
        <span className={styles.meta}>
          {explanation ? (
            explanationMeta(explanation)
          ) : (
            <OutcomePill outcome={detail.outcome} partition={detail.partition} />
          )}
        </span>
      </div>

      {explanation && response.data ? (
        <Attributions explanation={explanation} response={response.data} />
      ) : response.isFetching ? (
        <Skeleton label="Computing attributions" rows={3} rowHeight={18} />
      ) : (
        <>
          {response.isError && (
            <p role="status" className={styles.note}>
              Attributions are unavailable right now.
            </p>
          )}
          <Button
            variant="primary"
            size="md"
            onClick={() => (explained ? void response.refetch() : onExplain())}
          >
            Explain this customer
          </Button>
        </>
      )}
    </>
  );
}

function Attributions({
  explanation,
  response,
}: {
  explanation: ExplainResponse['explanations'][number];
  response: ExplainResponse;
}) {
  const shown = explanation.contributions.slice(0, SHOWN);
  // The bars run to the strongest attribution shown (the API orders them).
  const largest = Math.max(...shown.map((c) => Math.abs(c.shap_value)), Number.EPSILON);
  return (
    <>
      <ol aria-label="Attributions" className={styles.bars}>
        {shown.map((c) => {
          const value = signedPoints(c.shap_value);
          const direction = value.startsWith('+')
            ? 'raises'
            : value.startsWith('−')
              ? 'lowers'
              : 'none';
          return (
            <li key={c.feature} className={styles.bar}>
              <span className={styles.barLabel}>
                {isFeature(c.feature) ? COMPACT[c.feature] : c.label}
              </span>
              <span className={styles.track} aria-hidden="true">
                <span
                  className={styles.fill}
                  data-direction={direction}
                  style={cssVars({ '--w': Math.abs(c.shap_value) / largest })}
                />
              </span>
              <span className={styles.barValue} data-direction={direction}>
                {value}
              </span>
            </li>
          );
        })}
      </ol>
      <p className={styles.note}>{explanationNote(response)}</p>
    </>
  );
}
