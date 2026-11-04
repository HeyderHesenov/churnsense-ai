/**
 * Churn patterns: observed churn of the training partition, broken down by one
 * attribute or two crossed (``/api/segments``). The server counts every group
 * and hides the churn of any group too small to show, and of any group that
 * would give a small one's away; this page draws what it is sent. No model
 * output appears here.
 */
import { useState } from 'react';
import { ApiError } from '../../api/client';
import { useSegments, type Cell, type Segments } from '../../api/queries';
import { useParams } from '../../app/params';
import { MIN_CUSTOMERS } from '../../lib/cells';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { Associations } from './Associations';
import { Controls } from './Controls';
import {
  FAILED,
  focusText,
  lede,
  NOTE,
  population,
  showing,
  TITLE,
  viewName,
  WITHHELD,
} from './copy';
import { CrossTab } from './CrossTab';
import { FocusLine } from './FocusLine';
import {
  PATTERNS_PARAMS,
  pinnedCell,
  sameGroup,
  viewOf,
  type PatternsState,
  type View,
} from './patterns';
import { Ranked } from './Ranked';
import styles from './Patterns.module.css';

function withheld(error: Error | null): boolean {
  return error instanceof ApiError && error.code === 'outcomes_withheld';
}

export default function PatternsPage() {
  const [state, set] = useParams(PATTERNS_PARAMS);
  const view = viewOf(state);
  const query = useSegments(view.rows, view.cols);

  // A new view starts with nothing pinned, and is a place to go back to; the same
  // groups seen the other way round keep their pin, turned too.
  const choose = (next: View) => {
    const swapped = next.rows === view.cols && next.cols === view.rows;
    set({
      rows: next.rows,
      cols: next.cols ?? 'none',
      pr: swapped ? state.pc : '',
      pc: swapped ? state.pr : '',
    });
  };

  let body;
  if (query.isError && withheld(query.error)) {
    body = <EmptyState title={WITHHELD.title}>{WITHHELD.body}</EmptyState>;
  } else if (query.isError) {
    body = (
      <ErrorState onRetry={() => void query.refetch()} focusTo={MAIN_ID}>
        {FAILED}
      </ErrorState>
    );
  } else if (query.isPending) {
    body = <Skeleton label="Loading the breakdown" rows={8} />;
  } else {
    body = (
      <Breakdown
        data={query.data}
        state={state}
        next={query.isPlaceholderData ? view : null}
        onPin={(cell) => set({ pr: cell?.row ?? '', pc: cell?.column ?? '' }, { replace: true })}
        onView={choose}
      />
    );
  }

  return (
    <>
      <Header section="Book" page="Churn patterns" />
      <Page density="dense">
        <PageHeader eyebrow="Churn patterns" title={TITLE} size="md">
          {lede(query.data?.min_customers ?? MIN_CUSTOMERS)}
        </PageHeader>
        <Controls
          view={view}
          measure={state.show}
          onView={choose}
          onMeasure={(show) => set({ show }, { replace: true })}
        />
        {body}
        <p className={styles.footnote}>{NOTE}</p>
      </Page>
    </>
  );
}

interface BreakdownProps {
  /** The response on screen: the view asked for, or the last one while ``next`` loads. */
  data: Segments;
  state: PatternsState;
  next: View | null;
  onPin: (cell: Cell | null) => void;
  onView: (view: View) => void;
}

function Breakdown({ data, state, next, onPin, onView }: BreakdownProps) {
  // What the pointer or focus is on belongs to one response: a new one starts clear.
  // (Nothing remounts, so the focus stays where the reader left it.)
  const [pointed, setPointed] = useState<{ data: Segments; cell: Cell | null }>({
    data,
    cell: null,
  });
  const active = pointed.data === data ? pointed.cell : null;
  // The response's own view: its labels, whatever the address now asks for.
  const shown: View = { rows: data.rows, cols: data.columns };
  const pinned = next === null ? pinnedCell(data, state) : null;
  const toggle = (cell: Cell) => onPin(pinned && sameGroup(pinned, cell) ? null : cell);
  const unpin = pinned === null ? undefined : () => onPin(null);

  return (
    <>
      <p className={styles.population}>{population(data)}</p>
      {/* One live region, outside the busy one, for what changes under the reader. */}
      <p role="status" className="sr">
        {next !== null
          ? showing(data, next)
          : pinned !== null
            ? `Pinned: ${focusText(shown, pinned, data)}`
            : `Showing ${viewName(shown)}.`}
      </p>
      <div className={styles.workspace}>
        <div className={styles.main}>
          <section className={styles.main} aria-label="Breakdown" aria-busy={next !== null}>
            <FocusLine view={shown} data={data} active={active} pinned={pinned} next={next} />
            <CrossTab
              view={shown}
              data={data}
              measure={state.show}
              pinned={pinned}
              stale={next !== null}
              onActive={(cell) => setPointed({ data, cell })}
              onToggle={toggle}
              onUnpin={unpin}
            />
          </section>
          <Associations data={data} view={next ?? shown} onView={onView} />
        </div>
        <div className={styles.side}>
          <Ranked
            view={shown}
            data={data}
            pinned={pinned}
            stale={next !== null}
            onToggle={toggle}
            onUnpin={unpin}
          />
        </div>
      </div>
    </>
  );
}
