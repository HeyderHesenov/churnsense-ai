/**
 * Executive overview: where retention risk sits in the book. Every figure is
 * the API's; the page formats and places them. The retention queue's filters
 * live in the address, so a view can be linked and Back undoes a change.
 */
import { useOverview, useModelInfo } from '../../api/queries';
import { useParams } from '../../app/params';
import { MAIN_ID } from '../../components/shell/Shell';
import { Page } from '../../components/ui/Page';
import { isBandName } from '../../lib/bands';
import { int } from '../../lib/format';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { OverviewHeader } from './OverviewHeader';
import { Associations } from './Associations';
import { riskiestBands } from './copy';
import { CampaignCard } from './Campaign';
import { Distribution } from './Distribution';
import { QUEUE_PARAMS } from './queue';
import { QUEUE_TITLE_ID, RetentionQueue } from './RetentionQueue';
import { Summary } from './Summary';
import styles from './OverviewPage.module.css';

/**
 * Bring the queue into view after a key figure has set its filter, with
 * focus on its heading so the reader continues from there.
 */
function showQueue(): void {
  document.getElementById('queue')?.scrollIntoView({ block: 'start' });
  document.getElementById(QUEUE_TITLE_ID)?.focus({ preventScroll: true });
}

export default function OverviewPage() {
  const book = useOverview();
  const info = useModelInfo();
  const [queue, setQueue, queueHref] = useParams(QUEUE_PARAMS);
  const only = queue.bands?.length === 1 ? (queue.bands[0] ?? null) : null;

  return (
    <>
      <OverviewHeader
        info={info.data}
        query={queue.q}
        onSearch={(q) => setQueue({ q, page: 1 }, { replace: true })}
      />
      <Page>
        {book.isPending ? (
          <Skeleton label="Loading the overview" rows={4} rowHeight={120} />
        ) : book.isError ? (
          <ErrorState onRetry={() => void book.refetch()} focusTo={MAIN_ID}>
            The overview could not be loaded.
          </ErrorState>
        ) : (
          <>
            <Summary
              book={book.data}
              info={info.data}
              links={{
                // A key figure's count is the queue's with no other filter on, so none is.
                riskiest: queueHref({
                  bands: riskiestBands(book.data).filter(isBandName),
                  part: null,
                  q: '',
                  page: 1,
                }),
                flagged: queueHref({ bands: null, part: null, q: '', page: 1 }),
              }}
              onShowQueue={showQueue}
            />
            <section className={styles.rowA} aria-label="Risk and campaign">
              <Distribution
                book={book.data}
                info={info.data}
                only={only}
                onPickBand={(band) => {
                  if (isBandName(band)) setQueue({ bands: only === band ? null : [band], page: 1 });
                }}
              />
              <CampaignCard campaign={book.data.campaign} />
            </section>
            <Associations book={book.data} />
            <RetentionQueue book={book.data} state={queue} set={setQueue} />
            <footer className={styles.footer}>
              <span>
                IBM Telco Customer Churn · {int(book.data.customers)} rows · attributions are signed
                SHAP, pp · orange raises, blue lowers
              </span>
              <span>Every figure on this page was produced by running the code.</span>
            </footer>
          </>
        )}
      </Page>
    </>
  );
}
