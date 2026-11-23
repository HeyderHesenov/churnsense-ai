import { useEffect, useRef, useState, type RefObject } from 'react';
import { Link, useLocation } from '../../app/router';
import { NAV_GROUPS, ROUTES, type Route } from '../../app/routes';
import { useHealth, useModelInfo, usePerformance, type ModelInfo } from '../../api/queries';
import { cut, int } from '../../lib/format';
import { modelName } from '../../lib/labels';
import { readPreference, writePreference } from '../../lib/preferences';
import { V2EntryLink } from '../../v2/EntryLink';
import { V2_PREVIEW } from '../../v2/flag';
import { icons, Logo } from '../icons';
import { useDataSource } from '../ui/DataSourceNote';
import styles from './Shell.module.css';

export interface SidebarProps {
  /** Labels, the guide and the serving card are shown. */
  expanded: boolean;
  /** The narrow icon column of a wide screen. */
  collapsed: boolean;
  /** At 860 px and below the sidebar is an off-canvas drawer. */
  drawer: boolean;
  open: boolean;
  onClose: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled])';

/** The getting-started guide: three pages, in the order a newcomer needs them. */
const GUIDE = [
  { path: '/', title: 'See where risk sits', note: 'Overview, 1 minute' },
  { path: '/performance', title: 'Check you can trust it', note: 'Performance and explanations' },
  { path: '/simulator', title: 'Plan a campaign', note: 'Pick a cut-off, compare scenarios' },
] as const;

/**
 * While the drawer is open it is modal: focus starts on its close button, Esc
 * closes it, and Tab cycles within ``aside``. Every sidebar's drawer behaves so.
 */
export function useModalDrawer(
  aside: RefObject<HTMLElement | null>,
  closeButton: RefObject<HTMLButtonElement | null>,
  { drawer, open, onClose }: Pick<SidebarProps, 'drawer' | 'open' | 'onClose'>,
): void {
  useEffect(() => {
    if (!drawer || !open) return;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !aside.current) return;
      const items = Array.from(aside.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [aside, closeButton, drawer, open, onClose]);
}

export function Sidebar({ expanded, collapsed, drawer, open, onClose }: SidebarProps) {
  const { pathname } = useLocation();
  const aside = useRef<HTMLElement>(null);
  const nav = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [guide, setGuide] = useState(() => readPreference('guide') !== 'hidden');
  // The explorer lists the book: its size, as the service states it, and nothing without it.
  const bookSize = useDataSource()?.customers;
  /** In an open drawer: close it, even for the page already shown. */
  const onFollow = drawer && open ? onClose : undefined;

  const hideGuide = () => {
    writePreference('guide', 'hidden');
    // The button leaves with the card; focus goes to the page's own link, not to the document.
    const links = nav.current;
    (
      links?.querySelector<HTMLElement>('[aria-current="page"]') ??
      links?.querySelector<HTMLElement>('a[href]')
    )?.focus();
    setGuide(false);
  };

  useModalDrawer(aside, closeButton, { drawer, open, onClose });

  return (
    // A closed drawer is off screen: inert, so focus and screen readers skip it.
    <aside
      ref={aside}
      className={styles.side}
      data-collapsed={collapsed}
      data-open={drawer && open}
      id="navigation"
      aria-label="Navigation"
      inert={drawer && !open}
      {...(drawer && open ? { role: 'dialog', 'aria-modal': true } : {})}
    >
      <div className={styles.brand}>
        <Logo />
        {expanded && (
          <div className={styles.brandText}>
            <span className={styles.brandName}>ChurnSense AI</span>
            <span className={styles.brandTag}>Retention intelligence</span>
          </div>
        )}
        <button
          ref={closeButton}
          type="button"
          className={`${styles.closeButton} ${styles.showSm}`}
          onClick={onClose}
          aria-label="Close navigation menu"
        >
          <icons.close size={14} />
        </button>
      </div>
      <nav ref={nav} aria-label="Sections" className={styles.nav}>
        {NAV_GROUPS.map((group) => (
          // Named whether or not its label is shown: a collapsed rail still has groups.
          <div key={group} role="group" aria-label={group} className={styles.navGroup}>
            {expanded && (
              <span className={styles.group} aria-hidden="true">
                {group}
              </span>
            )}
            {ROUTES.filter((route) => route.navGroup === group).map((route) => (
              <NavItem
                key={route.path}
                route={route}
                expanded={expanded}
                current={route.path === pathname}
                count={route.path === '/explorer' ? bookSize : undefined}
                onFollow={onFollow}
              />
            ))}
          </div>
        ))}
        {/* Only in a build made with the preview on: otherwise left out of the bundle. */}
        {V2_PREVIEW && <V2EntryLink expanded={expanded} onFollow={onFollow} />}
      </nav>
      {expanded && (
        <div className={styles.foot}>
          {guide && <Guide pathname={pathname} onHide={hideGuide} onFollow={onFollow} />}
          <Serving />
          <p className={styles.disclaimer}>
            Decision support, not a decision system. A probability is similarity to past churners,
            not a causal claim.
          </p>
        </div>
      )}
    </aside>
  );
}

function NavItem({
  route,
  expanded,
  current,
  count,
  onFollow,
}: {
  route: Route;
  expanded: boolean;
  current: boolean;
  /** The customers the page lists, shown as a badge. */
  count: number | undefined;
  onFollow: (() => void) | undefined;
}) {
  const Icon = icons[route.icon];
  const badge = expanded && count !== undefined ? int(count) : null;
  // The name holds every word the link shows, in order (WCAG 2.5.3), and the
  // same words on a collapsed rail, where only the icon shows.
  const name = `${route.label}: ${route.description}${badge === null ? '' : `, ${badge} customers`}`;
  return (
    <Link
      to={route.path}
      className={styles.navLink}
      aria-label={name}
      aria-current={current ? 'page' : undefined}
      onClick={onFollow}
    >
      <Icon />
      {expanded && (
        <>
          {/* The spaces render as nothing in a flex box; they keep the words apart in the text. */}
          <span className={styles.navText}>
            <span className={styles.navLabel}>{route.label}</span>{' '}
            <span className={styles.navNote}>{route.description}</span>
          </span>
          {badge !== null && (
            <>
              {' '}
              <span className={styles.badge}>{badge}</span>
            </>
          )}
        </>
      )}
    </Link>
  );
}

/** Three steps for a first visit; dismissed once, remembered as a UI preference. */
function Guide({
  pathname,
  onHide,
  onFollow,
}: {
  pathname: string;
  onHide: () => void;
  onFollow: (() => void) | undefined;
}) {
  return (
    <section className={styles.guide} aria-label="Getting started">
      <div className={styles.guideHead}>
        <span className={styles.guideTitle}>New here? Start with three steps</span>
        <button
          type="button"
          className={styles.guideClose}
          onClick={onHide}
          aria-label="Hide the getting started guide"
        >
          <icons.close size={12} strokeWidth={2.4} />
        </button>
      </div>
      <ol className={styles.steps}>
        {GUIDE.map((step, i) => {
          const here = step.path === pathname;
          return (
            <li key={step.path}>
              <Link
                to={step.path}
                className={styles.step}
                aria-current={here ? 'step' : undefined}
                onClick={onFollow}
              >
                <span className={styles.stepNumber} aria-hidden="true">
                  {i + 1}
                </span>
                <span className={styles.stepText}>
                  <span className={styles.stepTitle}>{step.title}</span>
                  <span className={here ? styles.stepHere : styles.stepNote}>
                    {here ? 'you are here' : step.note}
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** "isotonic-calibrated, clamped": how the served probabilities were made. */
function calibrationNote(model: ModelInfo): string {
  const calibration = model.calibrated
    ? `${model.calibration_method ?? 'calibrated'}-calibrated`
    : 'uncalibrated';
  return model.probability_epsilon ? `${calibration}, clamped` : calibration;
}

/**
 * What is being served: the model, its operating point, the test figures the
 * release ledger records for it, and its calibration error on validation.
 */
function Serving() {
  const health = useHealth();
  const info = useModelInfo();
  // The performance page's own query: one request, shared, kept for the session.
  const performance = usePerformance();
  const loaded = health.data?.model_loaded === true;
  const model = info.data;
  const test = model?.test_evaluation;
  const figure = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : value.toFixed(3);
  return (
    <div className={styles.serving}>
      <div className={styles.servingHead}>
        <span className={styles.label}>Serving</span>
        <span className={styles.status} data-state={loaded ? 'up' : 'down'}>
          <span className={styles.dot} />
          {health.isPending
            ? 'Checking'
            : health.isError
              ? 'Unreachable'
              : loaded
                ? 'Loaded'
                : 'No model'}
        </span>
      </div>
      <div className={styles.modelName}>{model ? modelName(model.display_name) : '—'}</div>
      <div className={styles.modelNote}>{model ? calibrationNote(model) : '—'}</div>
      <div className={styles.figures}>
        <div>
          <div className={styles.figureName}>Threshold</div>
          <div className={styles.figureValue}>{model ? cut(model.threshold) : '—'}</div>
        </div>
        <div>
          <div className={styles.figureName}>Test precision</div>
          <div className={styles.figureValue}>{figure(test?.precision)}</div>
        </div>
        <div>
          <div className={styles.figureName}>Test recall</div>
          <div className={styles.figureValue}>{figure(test?.recall)}</div>
        </div>
        <div>
          <div className={styles.figureName}>Validation ECE</div>
          <div className={styles.figureValue}>
            {figure(performance.data?.validation_evaluation?.ece)}
          </div>
        </div>
      </div>
    </div>
  );
}
