/**
 * jsdom does no layout, so it has no ``matchMedia``. This one answers
 * ``(max-width: Npx)`` queries for a width the test sets, and
 * ``(prefers-reduced-motion: reduce)`` for a preference the test sets, and
 * tells listeners when ``setViewport`` or ``setReducedMotion`` changes them,
 * as a resized window or a changed system setting would.
 */
type Listener = (event: MediaQueryListEvent) => void;

const DEFAULT_WIDTH = 1440;
let width = DEFAULT_WIDTH;
let reducedMotion = false;
const listeners = new Map<Listener, string>();

function matches(query: string): boolean {
  if (query === '(prefers-reduced-motion: reduce)') return reducedMotion;
  const maxWidth = /\(max-width:\s*(\d+)px\)/.exec(query)?.[1];
  return maxWidth !== undefined && width <= Number(maxWidth);
}

function mediaQueryList(query: string): MediaQueryList {
  const list = {
    media: query,
    get matches() {
      return matches(query);
    },
    onchange: null,
    addEventListener: (_type: 'change', listener: Listener) => listeners.set(listener, query),
    removeEventListener: (_type: 'change', listener: Listener) => listeners.delete(listener),
    addListener: (listener: Listener) => listeners.set(listener, query),
    removeListener: (listener: Listener) => listeners.delete(listener),
    dispatchEvent: () => false,
  };
  return list as unknown as MediaQueryList;
}

export function installMatchMedia(): void {
  window.matchMedia = mediaQueryList;
}

function notify(): void {
  for (const [listener, query] of listeners) {
    listener({ matches: matches(query), media: query } as MediaQueryListEvent);
  }
}

/** Resize the "window" to ``next`` px; wrap in ``act`` when a component is mounted. */
export function setViewport(next: number): void {
  width = next;
  notify();
}

/** Turn the reader's "reduce motion" setting on or off; wrap in ``act`` when mounted. */
export function setReducedMotion(on: boolean): void {
  reducedMotion = on;
  notify();
}

export function resetViewport(): void {
  width = DEFAULT_WIDTH;
  reducedMotion = false;
}
