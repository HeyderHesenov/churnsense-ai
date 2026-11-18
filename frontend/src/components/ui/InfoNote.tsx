/**
 * A note that qualifies what follows ("A re-used holdout, not an untouched
 * one."): a blue "i", the point in bold, then the detail. It is a note, not an
 * alert: it is read in its place, never announced.
 */
import type { ReactNode } from 'react';
import { icons } from '../icons';
import styles from './InfoNote.module.css';

interface InfoNoteProps {
  /** The first sentence, in bold: the point of the note. */
  title: string;
  /** The rest, as running text. */
  children?: ReactNode;
}

export function InfoNote({ title, children }: InfoNoteProps) {
  return (
    <div role="note" className={styles.note}>
      <icons.info size={16} className={styles.icon} />
      <p className={styles.text}>
        <strong className={styles.title}>{title}</strong>
        {children && <> {children}</>}
      </p>
    </div>
  );
}
