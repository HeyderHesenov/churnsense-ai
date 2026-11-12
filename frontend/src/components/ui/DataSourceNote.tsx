/**
 * What the service says of the customers it lists, in its own words.
 *
 * A public demo lists generated customers and shows the dataset only as
 * aggregates. The API states that (`/model-info`, `data_source`) with the
 * sentences to print, and this prints them: nothing here decides what is
 * synthetic or words the label itself. With the dataset's own customers
 * listed there is nothing to say, and nothing is rendered.
 */
import { useModelInfo, type DataSource } from '../../api/queries';
import styles from './DataSourceNote.module.css';

/** The served data source, once model-info has answered; `null` until then or without a book. */
export function useDataSource(): DataSource | null {
  return useModelInfo().data?.data_source ?? null;
}

/** Whether the customers listed are generated ones. False until the API has said so. */
export function useSynthetic(): boolean {
  return useDataSource()?.customer_records === 'synthetic';
}

export type NotePart = 'customers' | 'figures' | 'evidence';

/** The service's sentence for each part, where it gave one. */
function lines(source: DataSource, parts: readonly NotePart[]): string[] {
  const text: Record<NotePart, string | null | undefined> = {
    customers: source.notice,
    figures: source.figures_note,
    evidence: source.evidence_note,
  };
  return parts.flatMap((part) => (text[part] ? [text[part]] : []));
}

interface DataSourceNoteProps {
  /** Which of the service's sentences this page needs, in order. */
  parts: readonly NotePart[];
}

export function DataSourceNote({ parts }: DataSourceNoteProps) {
  const source = useDataSource();
  if (source?.customer_records !== 'synthetic') return null;
  const shown = lines(source, parts);
  if (shown.length === 0) return null;
  return (
    <aside className={styles.note} role="note" aria-label={source.title} data-source="synthetic">
      <strong className={styles.title}>{source.title}</strong>
      <div className={styles.text}>
        {shown.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
    </aside>
  );
}

/** A short inline mark beside a heading whose figures are of generated customers. */
export function SyntheticTag() {
  const source = useDataSource();
  if (source?.customer_records !== 'synthetic') return null;
  return (
    <span className={styles.tag} data-source="synthetic" title={source.figures_note ?? undefined}>
      {source.title}
    </span>
  );
}
