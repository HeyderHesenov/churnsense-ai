/**
 * Executive overview: where retention risk sits in the book. Every figure is
 * the API's; the page formats and places them. The page is four tabs (summary,
 * risk distribution, associations, retention queue); the chosen tab and the
 * queue's filters live in the address, so a view can be linked and Back
 * undoes a change.
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { useOverview, useModelInfo } from '../../api/queries';
import { useParams } from '../../app/params';
import { MAIN_ID } from '../../components/shell/Shell';
import { DataSourceNote } from '../../components/ui/DataSourceNote';
import { Page } from '../../components/ui/Page';
import { PageTabs } from '../../components/ui/PageTabs';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { isBandName } from '../../lib/bands';
import { int } from '../../lib/format';
import { cx } from '../../lib/style';
import { Associations } from './Associations';
import { CampaignCard } from './Campaign';
import { footerSource, riskiestBands } from './copy';
import { Distribution } from './Distribution';
import { OverviewHeader } from './OverviewHeader';
import { QUEUE_TITLE_ID, RetentionQueue } from './RetentionQueue';
import { Intro, SummaryTab } from './Summary';
import { OVERVIEW_PARAMS, OVERVIEW_TABS, type OverviewTab } from './tabs';
import bento from './Bento.module.css';
import styles from './OverviewPage.module.css';

/** A click the browser handles itself (a new tab or window): the page does not follow it. */
function opensElsewhere(event: MouseEvent | undefined): boolean {
  return (
    event !== undefined &&
    (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
  );
}

/** Bring the queue into view, with focus on its heading so the reader continues from there. */
function showQueue(): void {
  document.getElementById('queue')?.scrollIntoView({ block: 'start' });
  document.getElementById(QUEUE_TITLE_ID)?.focus({ preventScroll: true });
}

export default function OverviewPage() {
  const book = useOverview();
  const info = useModelInfo();
  const [state, set, href] = useParams(OVERVIEW_PARAMS);
  const tab = state.tab;
  // A link or a bin that opens the queue tab sets this; once the tab shows, its heading takes focus.
  const focusQueue = useRef(false);
  const onQueue = (event?: MouseEvent) => {
    if (!opensElsewhere(event)) focusQueue.current = true;
  };
  useEffect(() => {
    if (tab !== 'queue') return;
    if (focusQueue.current && book.data) {
      focusQueue.current = false;
      showQueue();
    }
  }, [tab, book.data]);

  // A tab once shown stays mounted, hidden while another shows: its requests are
  // not cancelled and sent again as the reader moves between tabs (an explanation
  // costs a turn of the rate limit), and it comes back as it was left.
  const [visited, setVisited] = useState<ReadonlySet<OverviewTab>>(() => new Set([tab]));
  if (!visited.has(tab)) setVisited(new Set([...visited, tab]));
  const panel = (value: OverviewTab) => ({ hidden: tab !== value });

  const choose = (next: OverviewTab) => {
    if (next !== tab) set({ tab: next });
  };
  // Typing a search on another tab opens the queue it filters (one entry); then it settles in place.
  const onSearch = (q: string) => {
    if (q !== '' && tab !== 'queue') set({ q, page: 1, tab: 'queue' });
    else set({ q, page: 1 }, { replace: true });
  };

  return (
    <>
      <OverviewHeader info={info.data} query={state.q} onSearch={onSearch} />
      <Page>
        {book.isPending ? (
          <Skeleton label="Loading the overview" rows={4} rowHeight={120} />
        ) : book.isError ? (
          <ErrorState onRetry={() => void book.refetch()} focusTo={MAIN_ID}>
            The overview could not be loaded.
          </ErrorState>
        ) : (
          <>
            <DataSourceNote parts={['customers', 'figures', 'evidence']} />
            <Intro book={book.data} info={info.data} />
            <PageTabs
              def={OVERVIEW_TABS}
              label="Overview sections"
              value={tab}
              onChange={choose}
              counts={{
                queue: { value: int(book.data.flagged.in_queue), noun: 'in the queue' },
              }}
            >
              {visited.has('summary') && (
                <div {...panel('summary')}>
                  <SummaryTab
                    book={book.data}
                    links={{
                      // A key figure's count is the queue's with no other filter on, so none is.
                      riskiest: href({
                        tab: 'queue',
                        bands: riskiestBands(book.data).filter(isBandName),
                        part: null,
                        q: '',
                        page: 1,
                      }),
                      flagged: href({ tab: 'queue', bands: null, part: null, q: '', page: 1 }),
                      queue: href({ tab: 'queue', bands: null, part: null, q: '', page: 1 }),
                      distribution: href({ tab: 'distribution' }),
                    }}
                    onQueue={onQueue}
                  />
                </div>
              )}
              {visited.has('distribution') && (
                <div {...panel('distribution')}>
                  <div className={cx(bento.pair, bento.rise)}>
                    <Distribution
                      book={book.data}
                      info={info.data}
                      bandLink={(band) =>
                        href(
                          isBandName(band)
                            ? { tab: 'queue', bands: [band], page: 1 }
                            : { tab: 'queue' },
                        )
                      }
                      onPickBand={(band) => {
                        focusQueue.current = true;
                        set(
                          isBandName(band)
                            ? { tab: 'queue', bands: [band], page: 1 }
                            : { tab: 'queue' },
                        );
                      }}
                      onFollow={onQueue}
                    />
                    <CampaignCard campaign={book.data.campaign} />
                  </div>
                </div>
              )}
              {visited.has('associations') && (
                <div {...panel('associations')}>
                  <div className={bento.rise}>
                    <Associations book={book.data} />
                  </div>
                </div>
              )}
              {visited.has('queue') && (
                <div {...panel('queue')}>
                  <RetentionQueue book={book.data} state={state} set={set} />
                </div>
              )}
            </PageTabs>
            <footer className={styles.footer}>
              <span>
                {footerSource(info.data?.data_source, book.data.customers)} · attributions are
                signed SHAP, pp · orange raises, blue lowers
              </span>
              <span>Every figure on this page was produced by running the code.</span>
            </footer>
          </>
        )}
      </Page>
    </>
  );
}
