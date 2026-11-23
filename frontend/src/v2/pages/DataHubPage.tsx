/**
 * Data Hub (M1): check an account file against the account contract.
 *
 * The file is chosen here and sent to ``POST /api/v2/imports/preview``; the
 * server maps, parses, validates and sums it, and nothing in the browser reads
 * the file's contents. The chosen ``File`` stays in this page's memory only, so
 * a changed mapping can be checked against the same file; leaving the page or
 * choosing another file lets it go. Nothing is stored anywhere.
 */
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { Button } from '../../components/ui/Button';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { previewImport, type ImportPreview, type Pairs } from '../api';
import { SECTION } from '../copy';
import { Refusal } from '../parts';
import styles from '../V2.module.css';
import { draftOf, Mapping, withheld, type Draft, type StatusRow } from './dataHub/Mapping';
import { Counts, Issues, MrrTotals, PreviewNotes, PreviewRows, Verdict } from './dataHub/Results';
import { Upload } from './dataHub/Upload';
import own from './dataHub/DataHub.module.css';

const RESULT_ID = 'data-hub-result';

/** The reader's mapping as the pairs the server reads: one per column given a field. */
function mappingPairs(preview: ImportPreview, draft: Draft): Pairs {
  const columns = [...preview.mapping.applied.map((m) => m.column), ...preview.columns.ignored];
  return columns
    .filter((column) => !withheld(column) && (draft[column.position] ?? '') !== '')
    .sort((a, b) => a.position - b.position)
    .map((column) => [column.name, draft[column.position] ?? ''] as const);
}

/** The translations the reader filled in; a row left blank (or only spaces) is not one. */
function statusPairs(rows: readonly StatusRow[]): Pairs {
  return rows
    .filter((row) => row.source.trim() !== '')
    .map((row) => [row.source, row.target] as const);
}

export default function DataHubPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [statusRows, setStatusRows] = useState<StatusRow[]>([]);
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef<AbortController | null>(null);

  // Leaving the page abandons a check still in flight.
  useEffect(() => () => inFlight.current?.abort(), []);

  const send = async (chosen: File, mapping?: Pairs, statusMapping?: Pairs) => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setBusy(true);
    setFailure(null);
    try {
      const result = await previewImport(
        { file: chosen, mapping, statusMapping },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setPreview(result);
      setDraft(draftOf(result));
    } catch (error) {
      if (controller.signal.aborted) return;
      setFailure(error);
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setBusy(false);
      }
    }
  };

  const choose = (chosen: File) => {
    setFile(chosen);
    setPreview(null);
    setDraft({});
    setStatusRows([]);
    void send(chosen);
  };

  const reset = () => {
    inFlight.current?.abort();
    inFlight.current = null;
    setBusy(false);
    setFile(null);
    setPreview(null);
    setDraft({});
    setStatusRows([]);
    setFailure(null);
  };

  const check = () => {
    if (!file || !preview) return;
    void send(file, mappingPairs(preview, draft), statusPairs(statusRows));
  };

  return (
    <>
      <Header section={SECTION} page="Data Hub" />
      <Page>
        <PageHeader eyebrow="Your file · checked, never saved" title="Data Hub">
          Check a CSV of account snapshots against the ChurnSense account contract before anything
          is imported. The server maps the columns, checks every row and reports what it would
          accept. This preview stores nothing and does not feed the portfolio demo.
        </PageHeader>

        {!file ? (
          <Upload onChoose={choose} busy={busy} />
        ) : (
          <section aria-labelledby={RESULT_ID} className={styles.stack}>
            <div className={own.fileRow}>
              <h2 id={RESULT_ID} className={own.fileName} tabIndex={-1}>
                <icons.file size={16} />
                {file.name}
              </h2>
              <Button size="sm" onClick={reset}>
                Choose another file
              </Button>
            </div>
            {busy && !preview && <Skeleton label="Checking the file…" rows={3} />}
            {!preview && failure !== null && (
              <FirstFailure error={failure} onRetry={() => void send(file)} />
            )}
            {preview && (
              <>
                <Verdict preview={preview} />
                <Counts preview={preview} />
                <Mapping
                  preview={preview}
                  draft={draft}
                  onDraft={setDraft}
                  statusRows={statusRows}
                  onStatusRows={setStatusRows}
                  onCheck={check}
                  busy={busy}
                  refused={failure}
                />
                <MrrTotals preview={preview} />
                <Issues
                  id="problems-title"
                  title="Rejected rows"
                  issues={preview.problems}
                  omitted={preview.problems_omitted}
                  empty="No row was rejected."
                />
                <Issues
                  id="warnings-title"
                  title="Warnings"
                  issues={preview.warning_details}
                  omitted={preview.warnings_omitted}
                  empty="No warnings."
                />
                <PreviewRows preview={preview} />
                <PreviewNotes preview={preview} />
              </>
            )}
          </section>
        )}
      </Page>
    </>
  );
}

/** The first check of a file failed: why, in the reader's terms, and a way on. */
function FirstFailure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 422) {
    return (
      <div role="alert" className={own.refused}>
        <p className={styles.strong}>The server could not read this file.</p>
        <Refusal error={error} fallback={null} />
        <p className={styles.muted}>Fix the file and choose it again.</p>
      </div>
    );
  }
  if (error instanceof ApiError && error.status === 413) {
    return (
      <div role="alert" className={own.refused}>
        <p className={styles.strong}>The file is larger than the local server accepts.</p>
        <p className={styles.muted}>Split it, or check a smaller sample first.</p>
      </div>
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <div role="alert" className={own.refused}>
        <p className={styles.strong}>The local server does not have the Data Hub preview on.</p>
        <p className={styles.muted}>
          Start the API with CHURNSENSE_V2_DATA_HUB_PREVIEW=1 in a local deployment, then try again.
        </p>
      </div>
    );
  }
  const busyServer = error instanceof ApiError && (error.status === 429 || error.status === 503);
  return (
    <ErrorState onRetry={onRetry} focusTo={RESULT_ID}>
      {busyServer
        ? 'The local server is busy with other checks. Try again in a moment.'
        : 'The file could not be checked. The local server may be unreachable.'}
    </ErrorState>
  );
}
