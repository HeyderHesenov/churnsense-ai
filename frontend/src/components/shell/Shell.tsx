/**
 * The frame every page sits in: the sidebar (a collapsible column on wide
 * screens, a drawer at 860 px and below) and the main column. Pages render
 * their own header through ``Header``, which reads the controls from here.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { flushSync } from 'react-dom';
import { useLocation } from '../../app/router';
import { readPreference, writePreference } from '../../lib/preferences';
import { SMALL_SCREEN, useMediaQuery } from '../../lib/useMediaQuery';
import { Sidebar } from './Sidebar';
import styles from './Shell.module.css';

/** The page's content: the skip link's target, and where a page-wide retry leaves the reader. */
export const MAIN_ID = 'main';

interface ShellState {
  expanded: boolean;
  toggleSidebar: () => void;
  drawerOpen: boolean;
  openDrawer: () => void;
  menuButton: RefObject<HTMLButtonElement | null>;
}

const ShellContext = createContext<ShellState | null>(null);

export function useShell(): ShellState {
  const state = useContext(ShellContext);
  if (!state) throw new Error('useShell must be used inside <Shell>');
  return state;
}

export function Shell({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(() => readPreference('sidebar') !== 'collapsed');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const small = useMediaQuery(SMALL_SCREEN);
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  const toggleSidebar = useCallback(() => {
    writePreference('sidebar', expanded ? 'collapsed' : 'expanded');
    setExpanded(!expanded);
  }, [expanded]);
  const openDrawer = useCallback(() => setDrawerOpen(true), []);
  // Committed at once, so the page is no longer inert when focus returns to it.
  const closeDrawer = useCallback(() => {
    flushSync(() => setDrawerOpen(false));
    menuButton.current?.focus();
  }, []);

  // The drawer closes on a new page, and when the window widens past it (it
  // is no longer a drawer). Adjusted while rendering, not in an effect, so no
  // frame ever shows it open.
  const [seen, setSeen] = useState({ pathname, small });
  if (seen.pathname !== pathname || seen.small !== small) {
    if (seen.pathname !== pathname || !small) setDrawerOpen(false);
    setSeen({ pathname, small });
  }

  // A new page starts at the top, with focus on it so a screen reader
  // announces it. Not on the first load: that page has focus already.
  const shownPath = useRef(pathname);
  useEffect(() => {
    if (shownPath.current === pathname) return;
    shownPath.current = pathname;
    window.scrollTo(0, 0);
    main.current?.focus();
  }, [pathname]);

  const state = useMemo(
    () => ({ expanded, toggleSidebar, drawerOpen, openDrawer, menuButton }),
    [expanded, toggleSidebar, drawerOpen, openDrawer],
  );

  return (
    <ShellContext.Provider value={state}>
      <div className={styles.root}>
        {/* The first stop for a keyboard: past the navigation, straight to the page. */}
        <a
          href={`#${MAIN_ID}`}
          className={styles.skip}
          onClick={(event) => {
            event.preventDefault();
            main.current?.focus();
          }}
        >
          Skip to content
        </a>
        <Sidebar
          expanded={expanded || small}
          collapsed={!expanded && !small}
          drawer={small}
          open={drawerOpen}
          onClose={closeDrawer}
        />
        {small && drawerOpen && (
          <button
            type="button"
            className={styles.backdrop}
            onClick={closeDrawer}
            aria-label="Close navigation menu"
            tabIndex={-1}
          />
        )}
        <main
          ref={main}
          className={styles.main}
          tabIndex={-1}
          id={MAIN_ID}
          inert={small && drawerOpen}
        >
          {children}
        </main>
      </div>
    </ShellContext.Provider>
  );
}
