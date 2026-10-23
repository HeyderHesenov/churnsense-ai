/**
 * jsdom does no layout, so it has no ``matchMedia``. This one answers
 * ``(max-width: Npx)`` queries for a width the test sets, and tells listeners
 * when ``setViewport`` changes it, as a resized window would.
 */
type Listener = (event: MediaQueryListEvent) => void;

const DEFAULT_WIDTH = 1440;
let width = DEFAULT_WIDTH;
const listeners = new Map<Listener, string>();

function matches(query: string): boolean {
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

/** Resize the "window" to ``next`` px; wrap in ``act`` when a component is mounted. */
export function setViewport(next: number): void {
  width = next;
  for (const [listener, query] of listeners) {
    listener({ matches: matches(query), media: query } as MediaQueryListEvent);
  }
}

export function resetViewport(): void {
  width = DEFAULT_WIDTH;
}
