/**
 * The V2 Preview's navigation, in the dashboard's own frame (``Shell``): the
 * same column, drawer and keyboard behaviour, with the preview's pages grouped
 * by where their data comes from, and a way back to the dashboard.
 */
import { useRef } from 'react';
import { Link, useLocation } from '../app/router';
import { icons, Logo } from '../components/icons';
import styles from '../components/shell/Shell.module.css';
import { useModalDrawer, type SidebarProps } from '../components/shell/Sidebar';
import { PREVIEW_NOTE } from './copy';
import { V2_GROUPS, V2_ROUTES, type V2Route } from './routes';
import own from './V2.module.css';

export function V2Sidebar({ expanded, collapsed, drawer, open, onClose }: SidebarProps) {
  const { pathname } = useLocation();
  const aside = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  useModalDrawer(aside, closeButton, { drawer, open, onClose });
  const onFollow = drawer && open ? onClose : undefined;
  const lit = V2_ROUTES.find((route) => route.path === pathname);
  const current = lit?.under ?? lit?.path;

  return (
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
            <span className={styles.brandTag}>V2 Preview · local</span>
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
        {V2_GROUPS.map((group) => (
          <div key={group} role="group" aria-label={group} className={styles.navGroup}>
            {expanded && (
              <span className={styles.group} aria-hidden="true">
                {group}
              </span>
            )}
            {V2_ROUTES.filter((route) => route.group === group && !route.under).map((route) => (
              <NavItem
                key={route.path}
                route={route}
                expanded={expanded}
                current={route.path === current}
                onFollow={onFollow}
              />
            ))}
          </div>
        ))}
      </nav>
      {expanded && (
        <div className={styles.foot}>
          <div className={own.previewCard}>
            <span className={own.previewBadge}>Local preview</span>
            <p className={own.previewText}>{PREVIEW_NOTE.body}</p>
          </div>
          <Link to="/" className={own.backLink} onClick={onFollow}>
            <icons.chevronLeft size={14} />
            Back to the dashboard
          </Link>
        </div>
      )}
    </aside>
  );
}

function NavItem({
  route,
  expanded,
  current,
  onFollow,
}: {
  route: V2Route;
  expanded: boolean;
  current: boolean;
  onFollow: (() => void) | undefined;
}) {
  const Icon = icons[route.icon];
  return (
    <Link
      to={route.path}
      className={styles.navLink}
      aria-label={`${route.label}: ${route.description}`}
      aria-current={current ? 'page' : undefined}
      onClick={onFollow}
    >
      <Icon />
      {expanded && (
        <span className={styles.navText}>
          <span className={styles.navLabel}>{route.label}</span>{' '}
          <span className={styles.navNote}>{route.description}</span>
        </span>
      )}
    </Link>
  );
}
