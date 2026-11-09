/**
 * Where a file comes in: the drop zone (a label around the file input, which
 * covers it, so a click, a key or a drop reaches the input), the demo file,
 * and the served contract a file must meet. Without the contract nothing can
 * be chosen: the page would not know what it promises.
 */
import type { UseQueryResult } from '@tanstack/react-query';
import { useState, type RefObject } from 'react';
import type { ModelInfo } from '../../api/queries';
import { icons } from '../../components/icons';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button } from '../../components/ui/Button';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { columnRule, DEMO_NOTE, uploadLimits } from './copy';
import styles from './Batch.module.css';

const CONTRACT_ID = 'upload-contract';

interface UploadProps {
  info: UseQueryResult<ModelInfo>;
  picker: RefObject<HTMLInputElement | null>;
  onChoose: (file: File) => void;
  onDrop: (files: FileList) => void;
  onDemo: () => void;
}

export function Upload({ info, picker, onChoose, onDrop, onDemo }: UploadProps) {
  const [open, setOpen] = useState(false);
  const data = info.data;
  const contract = data?.upload_contract;
  const limits = data?.limits;
  return (
    <section aria-label="Upload" className={styles.panel}>
      {info.isPending ? (
        <Skeleton label="Loading the served contract…" rows={3} />
      ) : !data || !contract || !limits ? (
        <ErrorState onRetry={() => void info.refetch()} focusTo={MAIN_ID}>
          The served model’s contract could not be loaded. Nothing can be scored without it.
        </ErrorState>
      ) : (
        <>
          <label className={styles.drop}>
            <icons.upload size={28} className={styles.dropIcon} />
            <span className={styles.dropTitle}>Choose a CSV file</span>
            <span className={styles.note}>{uploadLimits(limits)}</span>
            {/* Over the whole box: a click or a drop anywhere on it reaches the input. */}
            <input
              ref={picker}
              type="file"
              accept=".csv,text/csv"
              aria-label="Choose a CSV file to score"
              className={styles.input}
              onChange={(event) => {
                const [file] = event.target.files ?? [];
                // Emptied, so choosing the same file again is a choice too.
                event.target.value = '';
                if (file) onChoose(file);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                onDrop(event.dataTransfer.files);
              }}
            />
          </label>
          <div className={styles.demo}>
            <Button variant="primary" onClick={onDemo}>
              Use the demo file
            </Button>
            <span className={styles.note}>{DEMO_NOTE}</span>
          </div>
          <button
            type="button"
            className={styles.disclosure}
            aria-expanded={open}
            aria-controls={open ? CONTRACT_ID : undefined}
            onClick={() => setOpen(!open)}
          >
            Required columns ({data.feature_columns.length})
            <icons.chevronDown size={14} className={styles.chevron} />
          </button>
          {open && (
            <div id={CONTRACT_ID} className={styles.contract}>
              <ul aria-label="Required columns" className={styles.columns}>
                {data.feature_columns.map((column) => (
                  <li key={column}>
                    <span className={styles.column}>{column}</span>{' '}
                    <span className={styles.rule}>
                      {columnRule(column, contract, data.numeric_bounds)}
                    </span>
                  </li>
                ))}
              </ul>
              <ul aria-label="Rules" className={styles.rules}>
                {contract.rules.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
