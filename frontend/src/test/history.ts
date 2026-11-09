/**
 * Counting history entries. jsdom's ``history.length`` drops the entries
 * after the current one when a new one is added, so once a test has gone
 * Back it no longer counts additions; the calls that add them do.
 */
import { vi } from 'vitest';

/** How many history entries the app adds from now on. */
export function entriesAdded(): () => number {
  const push = vi.spyOn(window.history, 'pushState');
  return () => push.mock.calls.length;
}
