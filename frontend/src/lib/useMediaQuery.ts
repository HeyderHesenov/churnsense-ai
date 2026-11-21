import { useCallback, useSyncExternalStore } from 'react';

/** Whether ``query`` matches now, following the viewport as it changes. */
export function useMediaQuery(query: string): boolean {
  // One subscription per query, not one per render.
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener('change', onChange);
      return () => list.removeEventListener('change', onChange);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches);
}

/** The width at which the sidebar becomes a drawer, as the design sets it. */
export const SMALL_SCREEN = '(max-width: 860px)';

/** The reader asked for less motion: animations end where they would have ended, at once. */
export const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';
