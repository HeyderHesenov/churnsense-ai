/**
 * Pieces every V2 page shares: the synthetic-data banner, a rule level, the
 * API's notes, and the problems a refusal lists. Each shows what the API
 * returned and computes nothing.
 */
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';
import type { DatasetInfo, Level } from './api';
import { LEVEL_LABEL, SYNTHETIC } from './copy';
import { count, longDate } from './format';
import styles from './V2.module.css';

/** What the Workbench's pages show: synthetic, as of the dataset's fixed snapshot date. */
export function DatasetBanner({ dataset }: { dataset: DatasetInfo }) {
  return (
    <div role="note" aria-label="About this data" className={styles.banner}>
      <span className={styles.bannerTag}>Synthetic demo data</span>
      <span className={styles.bannerFact}>
        Snapshot <strong>{longDate(dataset.as_of_date)}</strong>
      </span>
      <span className={styles.bannerFact}>
        <strong>{count(dataset.accounts)}</strong> generated accounts
      </span>
      <span className={styles.bannerFact}>Not your uploaded file, not real customers</span>
    </div>
  );
}

/** A rule level, or "Not assessed" where the API gives none. */
export function LevelBadge({ level }: { level: Level | null }) {
  return (
    <span className={styles.level} data-level={level ?? 'not_assessed'}>
      {LEVEL_LABEL[level ?? 'not_assessed']}
    </span>
  );
}

/** The API's notes about a response, as it wrote them. */
export function Notes({ notes, label = 'Notes' }: { notes: readonly string[]; label?: string }) {
  if (notes.length === 0) return null;
  return (
    <ul aria-label={label} className={styles.list}>
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

/** The synthetic data's own caveat, beside its banner. */
export function SyntheticNote() {
  return (
    <p className={styles.muted}>
      <strong>{SYNTHETIC.title}</strong> {SYNTHETIC.body}
    </p>
  );
}

/**
 * The reasons a 422 lists, which describe the reader's own input (a file, a
 * mapping, a filter): the one server text a page shows. Anything else is told
 * in the page's own words (``fallback``).
 */
export function Refusal({ error, fallback }: { error: unknown; fallback: ReactNode }) {
  if (error instanceof ApiError && error.status === 422 && error.problems.length > 0) {
    return (
      <div className={styles.stack}>
        <ul aria-label="Reasons" className={styles.list}>
          {error.problems.map((problem, i) => (
            <li key={`${String(i)}-${problem}`}>{problem}</li>
          ))}
        </ul>
        {error.problemsOmitted > 0 && (
          <p className={styles.muted}>…and {count(error.problemsOmitted)} more.</p>
        )}
      </div>
    );
  }
  return <>{fallback}</>;
}
