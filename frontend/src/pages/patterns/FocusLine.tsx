/**
 * The line above the table: the group pointed at or focused, else the pinned
 * one, else the partition as a whole. A pinned group can be opened in the
 * customer explorer, or the line says why it cannot. The line is for the eye: the
 * page announces pins and views, and each cell's own name says what the line says.
 */
import type { Cell, Segments } from '../../api/queries';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import { useSynthetic } from '../../components/ui/DataSourceNote';
import { explorerText, focusText, showing } from './copy';
import { explore, sameGroup, type View } from './patterns';
import styles from './Patterns.module.css';

interface Props {
  view: View;
  data: Segments;
  active: Cell | null;
  pinned: Cell | null;
  /** The view loading while this one stays on screen. */
  next: View | null;
}

export function FocusLine({ view, data, active, pinned, next }: Props) {
  const shown = active ?? pinned;
  // Pointing at the pinned group itself still reads as the pin: the line does
  // not change, so the explorer link below it stays where the pointer is going.
  const isPinned = pinned !== null && (active === null || sameGroup(active, pinned));
  // The explorer lists the group's own training customers; a service that lists
  // generated customers has none of them to open, so there is no link and no promise of one.
  const synthetic = useSynthetic();
  const opens = pinned === null || synthetic ? null : explore(view, pinned);
  return (
    <div className={styles.focus}>
      <p className={styles.focusLine} aria-hidden="true">
        {next !== null ? (
          showing(data, next)
        ) : (
          <>
            {isPinned && <b className={styles.pinMark}>Pinned · </b>}
            {focusText(view, shown, data)}
          </>
        )}
      </p>
      {pinned !== null && opens !== null && (
        <p className={styles.explore}>
          {opens.kind === 'link' ? (
            <Link to={opens.href} className={styles.exploreLink}>
              {explorerText(opens, pinned)} <icons.arrowRight size={13} />
            </Link>
          ) : (
            explorerText(opens, pinned)
          )}
        </p>
      )}
    </div>
  );
}
