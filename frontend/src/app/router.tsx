/**
 * A deliberately small router: eight flat pages, state in the query string.
 *
 * The browser's history is the only store. ``navigate`` pushes or replaces an
 * entry, ``Link`` renders a real ``<a href>`` (so a middle click, a modified
 * click or "copy link" behave as links do) and intercepts a plain left click.
 */
import {
  useCallback,
  useMemo,
  useSyncExternalStore,
  type AnchorHTMLAttributes,
  type MouseEvent,
} from 'react';

const NAVIGATE = 'churnsense:navigate';

/** Calls ``onChange`` on every change of address: a link, a setter, Back or Forward. */
export function onNavigate(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange);
  window.addEventListener(NAVIGATE, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(NAVIGATE, onChange);
  };
}

/**
 * Typed text not yet in the address (``useSettledDraft``). A draft is written
 * once it has been still for a moment, on its own; and before any other
 * change is made, so it lands in the entry it was typed in and the change
 * applies on top of it.
 */
const drafts = new Set<() => void>();
/** A draft is being written: its own change writes no other draft. */
let writing = false;

/**
 * Hold ``write`` until it has been still for ``delay`` ms or the address is
 * about to change, whichever comes first; the returned function lets it go
 * unwritten.
 */
export function holdDraft(write: () => void, delay: number): () => void {
  const settle = () => {
    release();
    const outer = writing;
    writing = true;
    try {
      write();
    } finally {
      writing = outer;
    }
  };
  const timer = window.setTimeout(settle, delay);
  drafts.add(settle);
  function release() {
    window.clearTimeout(timer);
    drafts.delete(settle);
  }
  return release;
}

/** Write every held draft now (none while one is being written). */
export function writeDrafts(): void {
  if (writing) return;
  // Each lets itself go first; none writes another (``writing``).
  for (const settle of [...drafts]) settle();
}

function snapshot(): string {
  return window.location.pathname + window.location.search;
}

/**
 * ``to`` resolved against this page, which it must not leave. Every caller
 * passes an address of its own making, so another origin is a bug: thrown,
 * never followed.
 */
function appUrl(to: string): URL {
  const url = new URL(to, window.location.href);
  if (url.origin !== window.location.origin)
    throw new Error(`"${to}" is not an address in this app`);
  return url;
}

/** Path, query and fragment, the query's keys in order: two spellings of one address compare equal. */
function canonical(url: URL): string {
  const query = new URLSearchParams(url.search);
  query.sort();
  return `${url.pathname}?${query.toString()}${url.hash}`;
}

/** Go to ``to`` (a path in this app, with an optional query and fragment). */
export function navigate(to: string, options: { replace?: boolean } = {}): void {
  const url = appUrl(to);
  writeDrafts();
  if (canonical(url) === canonical(new URL(window.location.href))) return;
  // Whole, for the same reason: a bare path beginning "//" would not be this origin.
  if (options.replace) window.history.replaceState(null, '', url.href);
  else window.history.pushState(null, '', url.href);
  window.dispatchEvent(new Event(NAVIGATE));
}

export interface Location {
  pathname: string;
  search: URLSearchParams;
}

export function useLocation(): Location {
  const current = useSyncExternalStore(onNavigate, snapshot);
  return useMemo(() => {
    // ``current`` is this page's own path, but a path can begin "//": parsed on
    // its own that names another host. The whole address keeps it a path.
    const url = new URL(window.location.origin + current);
    return { pathname: url.pathname, search: url.searchParams };
  }, [current]);
}

export type ParamValue = string | number | boolean | readonly string[] | null | undefined;

/** The current path with ``changes`` applied to its query; ``null``/``undefined``/``[]`` removes a key. */
export function withParams(location: Location, changes: Record<string, ParamValue>): string {
  const params = new URLSearchParams(location.search);
  for (const [key, value] of Object.entries(changes)) {
    params.delete(key);
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) value.forEach((item: string) => params.append(key, item));
    else params.set(key, String(value));
  }
  const query = params.toString();
  return location.pathname + (query ? `?${query}` : '');
}

/**
 * Read the query, and change it: a new history entry by default, in place
 * with ``replace``. Each change applies to the address as it is when it is
 * made (typed text held back included), so two changes in one handler both land.
 */
export function useSearchParams(): [
  URLSearchParams,
  (changes: Record<string, ParamValue>, options?: { replace?: boolean }) => void,
] {
  const location = useLocation();
  const update = useCallback(
    (changes: Record<string, ParamValue>, options: { replace?: boolean } = {}) => {
      writeDrafts();
      const now = {
        pathname: window.location.pathname,
        search: new URLSearchParams(window.location.search),
      };
      // Whole, as in ``navigate``: a bare path beginning "//" would name another host.
      navigate(window.location.origin + withParams(now, changes), options);
    },
    [],
  );
  return [location.search, update];
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { to: string };

/** A link that changes page without a reload, and is still a link. */
export function Link({ to, onClick, children, ...props }: LinkProps) {
  appUrl(to);
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      props.target === '_blank'
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handle} {...props}>
      {children}
    </a>
  );
}
