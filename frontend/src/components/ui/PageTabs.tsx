/**
 * The tabs a long page is split into: a sticky strip of numbered tabs, each
 * with a one-line hint, over the chosen tab's panel.
 *
 * The chosen tab is part of the address (``?tab=``), read and written by the
 * page's own params (``app/params``): only a declared tab is ever read, an
 * unknown or empty one reads as the first, the first is left out of the
 * address, and every other key in the query (``?id=``, ``?q=``) is kept. Each
 * change is one history entry, so Back and Forward step through the tabs read.
 *
 * Only the chosen tab is in the tab order; the arrow keys move between tabs and
 * choose as they go, wrapping at the ends, and Home and End jump (as ``Tabs``).
 */
import { useCallback, useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { param, useParams, type Param } from '../../app/params';
import styles from './PageTabs.module.css';

export interface PageTab<T extends string> {
  value: T;
  label: string;
  /** One line under the label: what the tab answers. */
  hint: string;
}

export interface PageTabsDef<T extends string> {
  tabs: readonly PageTab<T>[];
  /** The page's params for its tab; spread into a page's own schema to share it. */
  schema: { tab: Param<T> };
}

/**
 * A page's tabs, declared once at module level (the schema must be stable).
 * The first tab is the default: the one an address without ``?tab=`` shows.
 */
export function pageTabs<const T extends string>(
  tabs: readonly [PageTab<T>, ...PageTab<T>[]],
): PageTabsDef<T> {
  const values = tabs.map((tab) => tab.value);
  return { tabs, schema: { tab: param.choice(values, tabs[0].value) } };
}

/** The tab the address names, and a way to choose another (one history entry; none for the same). */
export function usePageTab<T extends string>(def: PageTabsDef<T>): [T, (tab: T) => void] {
  const [values, set] = useParams(def.schema);
  const tab = values.tab;
  const choose = useCallback(
    (next: T) => {
      if (next !== tab) set({ tab: next });
    },
    [set, tab],
  );
  return [tab, choose];
}

/** Where ``strip`` must be scrolled to show all of ``tab``: as it is, if it already does. */
export function scrolledTo(strip: HTMLElement, tab: HTMLElement): number {
  const start = tab.offsetLeft;
  const end = start + tab.offsetWidth;
  if (start < strip.scrollLeft) return start;
  if (end > strip.scrollLeft + strip.clientWidth) return end - strip.clientWidth;
  return strip.scrollLeft;
}

/** A figure beside a tab's name, such as "2" pinned; ``noun`` is read after it, not shown. */
export interface TabCount {
  value: string;
  noun: string;
}

interface PageTabsProps<T extends string> {
  def: PageTabsDef<T>;
  /** The strip's accessible name, e.g. "Overview sections". */
  label: string;
  value: T;
  onChange: (value: T) => void;
  /** A hint that changes with the page, e.g. "2 pinned"; replaces the declared hint. */
  hints?: Partial<Record<T, string>>;
  counts?: Partial<Record<T, TabCount>>;
  /** The chosen tab's panel. */
  children: ReactNode;
}

export function PageTabs<T extends string>({
  def,
  label,
  value,
  onChange,
  hints,
  counts,
  children,
}: PageTabsProps<T>) {
  const id = useId();
  const buttons = useRef(new Map<T, HTMLButtonElement>());
  const list = useRef<HTMLDivElement>(null);
  const tabs = def.tabs;
  // Ids by position: a tab's value may hold characters an id cannot.
  const tabId = (index: number) => `${id}-tab-${index}`;
  const panelId = `${id}-panel`;
  const selectedIndex = Math.max(
    0,
    tabs.findIndex((tab) => tab.value === value),
  );

  // The strip scrolls sideways on a narrow screen: keep the chosen tab in it.
  // Only the strip moves, never the window (scrollIntoView would scroll both).
  useEffect(() => {
    const strip = list.current;
    const button = buttons.current.get(value);
    if (strip && button) strip.scrollLeft = scrolledTo(strip, button);
  }, [value]);

  const choose = (index: number) => {
    const tab = tabs[(index + tabs.length) % tabs.length];
    if (!tab) return;
    onChange(tab.value);
    buttons.current.get(tab.value)?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    // Alt, Ctrl and Cmd with an arrow are the browser's: Back, Forward, line ends.
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: tabs.length - 1,
    };
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    choose(next);
  };

  return (
    <>
      <div className={styles.strip}>
        <div ref={list} role="tablist" aria-label={label} className={styles.list}>
          {tabs.map((tab, index) => {
            const selected = index === selectedIndex;
            const count = counts?.[tab.value];
            return (
              <button
                key={tab.value}
                ref={(node) => {
                  if (node) buttons.current.set(tab.value, node);
                  else buttons.current.delete(tab.value);
                }}
                type="button"
                role="tab"
                id={tabId(index)}
                aria-selected={selected}
                aria-controls={panelId}
                tabIndex={selected ? 0 : -1}
                className={styles.tab}
                onClick={() => onChange(tab.value)}
                onKeyDown={(event) => onKeyDown(event, index)}
              >
                <span className={styles.number} aria-hidden="true">
                  {index + 1}
                </span>
                {/* The spaces render as nothing in a flex box; they keep the words apart in the name. */}
                <span className={styles.text}>
                  <span className={styles.label}>{tab.label}</span>{' '}
                  <span className={styles.hint}>{hints?.[tab.value] ?? tab.hint}</span>
                </span>
                {count && (
                  <>
                    {' '}
                    <span className={styles.count}>
                      {count.value} <span className="sr">{count.noun}</span>
                    </span>
                  </>
                )}
              </button>
            );
          })}
        </div>
      </div>
      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(selectedIndex)}
        // A stop after the tabs, as the ARIA tabs pattern has it: a panel may open
        // on text or a chart, with nothing to focus before the page goes on.
        // eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex
        tabIndex={0}
        className={styles.panel}
      >
        {children}
      </div>
    </>
  );
}
