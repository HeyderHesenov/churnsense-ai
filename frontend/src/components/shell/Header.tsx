import type { ReactNode } from 'react';
import { icons, Logo } from '../icons';
import { useShell } from './Shell';
import styles from './Shell.module.css';

interface HeaderProps {
  section: string;
  page: string;
  /** The page's own controls, right-aligned. */
  children?: ReactNode;
  /** A search box between the breadcrumb and the actions (the overview's). */
  search?: ReactNode;
  /** The overview names the product in its header on small screens. */
  brandOnSmall?: boolean;
}

export function Header({ section, page, children, search, brandOnSmall = false }: HeaderProps) {
  const { expanded, toggleSidebar, drawerOpen, openDrawer, menuButton } = useShell();
  return (
    <header className={styles.header}>
      <button
        ref={menuButton}
        type="button"
        className={`${styles.menuButton} ${styles.showSm}`}
        onClick={openDrawer}
        aria-label="Open navigation menu"
        aria-expanded={drawerOpen}
        aria-controls="navigation"
      >
        <icons.menu size={18} />
      </button>
      {brandOnSmall && (
        <span className={`${styles.headerBrand} ${styles.showSm}`}>
          <Logo size={22} />
          ChurnSense AI
        </span>
      )}
      <button
        type="button"
        className={`${styles.iconButton} ${styles.hideSm}`}
        onClick={toggleSidebar}
        aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        aria-expanded={expanded}
      >
        <icons.sidebar />
      </button>
      <nav
        aria-label="Breadcrumb"
        className={`${styles.crumbs} ${brandOnSmall ? styles.hideSm : ''}`}
      >
        <span>{section}</span>
        <span aria-hidden="true">/</span>
        <span className={styles.crumbCurrent} aria-current="page">
          {page}
        </span>
      </nav>
      {search && <div className={styles.search}>{search}</div>}
      <div className={styles.actions}>{children}</div>
    </header>
  );
}
