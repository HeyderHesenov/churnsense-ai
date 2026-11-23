/**
 * Where a file comes in: a drop zone around the file input (a click, a key or
 * a drop reaches it), what happens to the file, and the contract it is checked
 * against.
 */
import { icons } from '../../../components/icons';
import { Disclosure } from '../../../components/ui/Disclosure';
import { InfoNote } from '../../../components/ui/InfoNote';
import { NOT_SAVED } from '../../copy';
import { CANONICAL_FIELDS, FIELD_LABELS, isRequired } from '../../fields';
import styles from '../../V2.module.css';
import own from './DataHub.module.css';

export function Upload({ onChoose, busy }: { onChoose: (file: File) => void; busy: boolean }) {
  return (
    <section aria-label="Upload" className={own.panel}>
      <label className={own.drop} data-busy={busy}>
        <icons.upload size={28} className={own.dropIcon} />
        <span className={own.dropTitle}>Choose a CSV file of account snapshots</span>
        <span className={styles.muted}>
          One header row, UTF-8, comma-separated. The local server’s size and row limits apply.
        </span>
        <input
          type="file"
          accept=".csv,text/csv"
          aria-label="Choose a CSV file to check"
          className={own.input}
          disabled={busy}
          onChange={(event) => {
            const [file] = event.target.files ?? [];
            // Emptied, so choosing the same file again is a choice too.
            event.target.value = '';
            if (file) onChoose(file);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const [file] = event.dataTransfer.files;
            if (file && !busy) onChoose(file);
          }}
        />
      </label>
      <InfoNote title={NOT_SAVED.title}>{NOT_SAVED.body}</InfoNote>
      <Disclosure summary={`The account contract (${String(CANONICAL_FIELDS.length)} fields)`}>
        <ul aria-label="Contract fields" className={own.fields}>
          {CANONICAL_FIELDS.map((field) => (
            <li key={field}>
              <span className={styles.mono}>{field}</span>{' '}
              <span className={styles.muted}>
                {FIELD_LABELS[field]}
                {isRequired(field) ? ' · required' : ''}
              </span>
            </li>
          ))}
        </ul>
      </Disclosure>
    </section>
  );
}
