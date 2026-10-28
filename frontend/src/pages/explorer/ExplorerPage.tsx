/**
 * Customer explorer: every customer in the book, filtered, sorted and paged
 * by the API. The filters, the page and the open customer live in the
 * address, so a view can be linked and Back undoes a change. Every figure is
 * the API's; the page formats and places them.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { exportUrl } from '../../api/client';
import { customersQuery, useCustomers, useModelInfo, useSelection } from '../../api/queries';
import { useParams } from '../../app/params';
import { onNavigate, writeDrafts } from '../../app/router';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { Button, ButtonLink } from '../../components/ui/Button';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { exportLabel, lede } from './copy';
import { CustomerTable } from './CustomerTable';
import { Detail } from './Detail';
import {
  CUSTOMERS_ID,
  DETAIL_TITLE_ID,
  EXPLORER_PARAMS,
  explorerFilter,
  exportQuery,
  FILTER_DEFAULTS,
  idleReason,
  listingQuery,
  neighbour,
} from './explorer';
import { Filters } from './Filters';
import { Presets } from './Presets';
import { Summary } from './Summary';
import styles from './Explorer.module.css';

/** The whole book: the lede's count, and the "All customers" view's (one request). */
const WHOLE_BOOK = explorerFilter(FILTER_DEFAULTS);

export default function ExplorerPage() {
  const [state, set] = useParams(EXPLORER_PARAMS);
  const info = useModelInfo();
  const book = useSelection(WHOLE_BOOK);
  const idle = idleReason(state);
  const listing = useCustomers(listingQuery(state));
  // The selection's figures depend on the filters only, so a new page, order
  // or page size keeps them (and says nothing new to a screen reader).
  const selection = useSelection(explorerFilter(state));
  // A query with nothing to ask for still holds the last view's page as
  // placeholder data, so nothing reads the listing while the page is idle.
  const page = idle ? undefined : listing.data;
  const current = page && !listing.isPlaceholderData ? page : undefined;
  // Whether outcomes are withheld is the API's to say: the book's own answer,
  // else the model's provenance; until either answers, unknown.
  const withheld = book.data
    ? book.data.withheld_reason
    : info.data
      ? (info.data.provenance?.withheld_reason ?? null)
      : undefined;

  // The open customer's place in the whole list, when it is on the page shown.
  const row = current && state.id ? current.rows.findIndex((r) => r.id === state.id) : -1;
  const openIndex = current && row >= 0 ? (current.page - 1) * current.page_size + row : null;

  const client = useQueryClient();
  // Each step's number. Any change of address moves it on (leaving the page
  // is one), so a step the reader has moved on from never lands; the panel
  // stops waiting for it, and a failed step's message (about the customer
  // then open) goes.
  const steps = useRef(0);
  const [stepping, setStepping] = useState<number | null>(null);
  const [failed, setFailed] = useState<1 | -1 | null>(null);
  const [explained, setExplained] = useState<ReadonlySet<string>>(() => new Set());
  useEffect(
    () =>
      onNavigate(() => {
        steps.current += 1;
        setStepping(null);
        setFailed(null);
      }),
    [],
  );

  const open = (id: string) => {
    flushSync(() => set({ id }));
    document.getElementById(DETAIL_TITLE_ID)?.focus();
  };
  const close = () => {
    const id = state.id;
    flushSync(() => set({ id: '' }));
    (document.getElementById(`open-${id}`) ?? document.getElementById(CUSTOMERS_ID))?.focus();
  };

  /**
   * The customer before or after the open one. On another page, that page is
   * fetched first and the address then changes once (page and customer
   * together), so the list never shows a page without the open row. If it
   * cannot be fetched, the page stays where it is and the panel says so.
   * With nowhere to go (the customer is off the list, or alone in it), it
   * does nothing. Typed text not yet in the address is written first, at the
   * press and before the step lands: if it changed the list, the step is
   * the old list's and is dropped.
   */
  const step = async (direction: 1 | -1) => {
    const ticket = ++steps.current;
    writeDrafts();
    if (steps.current !== ticket || !current || openIndex === null) return;
    const target = neighbour(openIndex, direction, current.total, current.page_size);
    if (!target) return;
    if (target.page === current.page) {
      const next = current.rows[target.row];
      if (next) set({ id: next.id });
      return;
    }
    setFailed(null);
    setStepping(ticket);
    try {
      const next = await client.query(
        customersQuery(listingQuery({ ...state, page: target.page })),
      );
      writeDrafts();
      // The reader moved on while the page loaded: their choice stands.
      if (steps.current !== ticket) return;
      const found = next.rows[target.row];
      set(found ? { page: target.page, id: found.id } : { page: target.page });
    } catch {
      if (steps.current === ticket) setFailed(direction);
    } finally {
      setStepping((now) => (now === ticket ? null : now));
    }
  };

  const total = idle ? 0 : (selection.data?.total ?? current?.total);
  return (
    <>
      <Header section="Book" page="Customer explorer">
        {total === undefined || total === 0 ? (
          <Button size="lg" disabled>
            <icons.download size={15} />
            {exportLabel(total)}
          </Button>
        ) : (
          <ButtonLink href={exportUrl(exportQuery(state))} download size="lg">
            <icons.download size={15} />
            {exportLabel(total)}
          </ButtonLink>
        )}
      </Header>
      <Page density="dense">
        <PageHeader
          eyebrow="Customer explorer"
          title="Every customer, scored and filterable"
          size="md"
        >
          {lede(book.isError ? null : book.data?.total, withheld)}
        </PageHeader>
        <Presets state={state} set={set} />
        <Filters state={state} set={set} threshold={info.data?.threshold} />
        <Summary
          selection={idle ? undefined : selection.data}
          failed={!idle && selection.isError}
          onRetry={() => void selection.refetch()}
          idle={idle}
          info={info.data}
        />
        <div className={styles.workspace}>
          <CustomerTable
            state={state}
            set={set}
            listing={listing}
            page={page}
            idle={idle}
            info={info.data}
            withheld={withheld ?? null}
            openIndex={openIndex}
            onOpen={open}
            onClose={close}
          />
          {state.id && (
            <Detail
              id={state.id}
              canStep={openIndex !== null && (current?.total ?? 0) > 1}
              stepping={stepping !== null}
              failed={failed}
              offList={current !== undefined && openIndex === null}
              onStep={(direction) => void step(direction)}
              onClose={close}
              explained={explained.has(state.id)}
              onExplain={() => setExplained((ids) => new Set(ids).add(state.id))}
            />
          )}
        </div>
      </Page>
    </>
  );
}
