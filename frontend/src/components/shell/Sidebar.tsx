import { useEffect, useRef } from 'react';
import { Link, useLocation } from '../../app/router';
import { GROUPS, ROUTES } from '../../app/routes';
import { useHealth, useModelInfo, type ModelInfo } from '../../api/queries';
import { cut } from '../../lib/format';
import { modelName } from '../../lib/labels';
import { icons, Logo } from '../icons';
import styles from './Shell.module.css';

interface SidebarProps {
  /** Labels and the serving card are shown. */
  expanded: boolean;
  /** The narrow icon column of a wide screen. */
  collapsed: boolean;
  /** At 860 px and below the sidebar is an off-canvas drawer. */
  drawer: boolean;
  open: boolean;
  onClose: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled])';

export function Sidebar({ expanded, collapsed, drawer, open, onClose }: SidebarProps) {
  const { pathname } = useLocation();
  const aside = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  // While the drawer is open it is modal: focus starts on its close button,
  // Esc closes it, and Tab cycles within it.
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
  }, [drawer, open, onClose]);

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
      <nav aria-label="Sections" className={styles.nav}>
        {GROUPS.map((group) => (
          <NavGroup
            key={group}
            group={group}
            expanded={expanded}
            pathname={pathname}
            onFollow={drawer && open ? onClose : undefined}
          />
        ))}
      </nav>
      {expanded && <Serving />}
    </aside>
  );
}

function NavGroup({
  group,
  expanded,
  pathname,
  onFollow,
}: {
  group: string;
  expanded: boolean;
  pathname: string;
  /** In an open drawer: close it, even for the page already shown. */
  onFollow: (() => void) | undefined;
}) {
  return (
    <>
      {expanded && <span className={styles.group}>{group}</span>}
      {ROUTES.filter((route) => route.group === group).map((route) => {
        const Icon = icons[route.icon];
        return (
          <Link
            key={route.path}
            to={route.path}
            className={styles.navLink}
            aria-label={route.label}
            aria-current={route.path === pathname ? 'page' : undefined}
            onClick={onFollow}
          >
            <Icon />
            {expanded && <span className={styles.navLabel}>{route.label}</span>}
          </Link>
        );
      })}
    </>
  );
}

/** "isotonic-calibrated, clamped": how the served probabilities were made. */
function calibrationNote(model: ModelInfo): string {
  const calibration = model.calibrated
    ? `${model.calibration_method ?? 'calibrated'}-calibrated`
    : 'uncalibrated';
  return model.probability_epsilon ? `${calibration}, clamped` : calibration;
}

/** What is being served: the model, its operating point and its recorded test figures. */
function Serving() {
  const health = useHealth();
  const info = useModelInfo();
  const loaded = health.data?.model_loaded === true;
  const model = info.data;
  const test = model?.test_evaluation;
  const figure = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : value.toFixed(3);
  return (
    <div className={styles.foot}>
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
        </div>
      </div>
      <p className={styles.disclaimer}>
        Decision support, not a decision system. A probability is similarity to past churners, not a
        causal claim.
      </p>
    </div>
  );
}
