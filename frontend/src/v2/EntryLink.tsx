/**
 * The dashboard's one way into the V2 Preview: a sidebar group of its own,
 * shown only in a build made with the preview on (``flag``).
 */
import { Link } from '../app/router';
import { icons } from '../components/icons';
import styles from '../components/shell/Shell.module.css';
import { V2_ROOT } from './flag';

const LABEL = 'V2 Preview';
const NOTE = 'Local workspace preview';

export function V2EntryLink({
  expanded,
  onFollow,
}: {
  expanded: boolean;
  onFollow: (() => void) | undefined;
}) {
  return (
    <div role="group" aria-label="Preview" className={styles.navGroup}>
      {expanded && (
        <span className={styles.group} aria-hidden="true">
          Preview
        </span>
      )}
      <Link
        to={V2_ROOT}
        className={styles.navLink}
        aria-label={`${LABEL}: ${NOTE}`}
        onClick={onFollow}
      >
        <icons.flag />
        {expanded && (
          <span className={styles.navText}>
            <span className={styles.navLabel}>{LABEL}</span>{' '}
            <span className={styles.navNote}>{NOTE}</span>
          </span>
        )}
      </Link>
    </div>
  );
}
