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

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange);
  window.addEventListener(NAVIGATE, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(NAVIGATE, onChange);
  };
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
  if (canonical(url) === canonical(new URL(window.location.href))) return;
  const address = url.pathname + url.search + url.hash;
  if (options.replace) window.history.replaceState(null, '', address);
  else window.history.pushState(null, '', address);
  window.dispatchEvent(new Event(NAVIGATE));
}

export interface Location {
  pathname: string;
  search: URLSearchParams;
}

export function useLocation(): Location {
  const current = useSyncExternalStore(subscribe, snapshot);
  return useMemo(() => {
    const url = new URL(current, window.location.origin);
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
 * made, so two changes in one handler both land.
 */
export function useSearchParams(): [
  URLSearchParams,
  (changes: Record<string, ParamValue>, options?: { replace?: boolean }) => void,
] {
  const location = useLocation();
  const update = useCallback(
    (changes: Record<string, ParamValue>, options: { replace?: boolean } = {}) => {
      const now = {
        pathname: window.location.pathname,
        search: new URLSearchParams(window.location.search),
      };
      navigate(withParams(now, changes), options);
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
