/**
 * Underlined tabs over one panel (the overview's segment card). Only the
 * chosen tab is in the tab order; the arrow keys move between tabs and
 * choose as they go, wrapping at the ends, and Home and End jump.
 */
import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import type { Option } from './Toggle';
import styles from './Tabs.module.css';

interface TabsProps<T extends string> {
  label: string;
  tabs: readonly Option<T>[];
  value: T;
  onChange: (value: T) => void;
  /** The chosen tab's panel. */
  children: ReactNode;
}

export function Tabs<T extends string>({ label, tabs, value, onChange, children }: TabsProps<T>) {
  const id = useId();
  const buttons = useRef(new Map<T, HTMLButtonElement>());
  // Ids by position: a tab's value may hold characters an id cannot.
  const tabId = (index: number) => `${id}-tab-${index}`;
  const panelId = `${id}-panel`;

  const choose = (index: number) => {
    const tab = tabs[(index + tabs.length) % tabs.length];
    if (!tab) return;
    onChange(tab.value);
    buttons.current.get(tab.value)?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
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
      <div role="tablist" aria-label={label} className={styles.list}>
        {tabs.map((tab, index) => {
          const selected = tab.value === value;
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
              {tab.label}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(tabs.findIndex((tab) => tab.value === value))}
        className={styles.panel}
      >
        {children}
      </div>
    </>
  );
}
