/**
 * Batch scoring: a file of customers, checked against the served contract and
 * scored by the API in one request. Every figure is the API's; the page
 * neither validates a value nor scores a row.
 *
 * A file and its results live in memory while the page is open, never in the
 * address or the browser's storage. A new choice drops the request still out
 * for the last one and its results at once, so a figure on the page always
 * belongs to the file named above it.
 */
import { useEffect, useReducer, useRef } from 'react';
import { scoreBatch, useModelInfo } from '../../api/queries';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { Button } from '../../components/ui/Button';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { toCsv } from '../../lib/csv';
import { useDownload } from '../../lib/useDownload';
import { canScore, INITIAL, reducer, type Stage } from './batch';
import { LEDE, steps } from './copy';
import { Results } from './Results';
import { Upload } from './Upload';
import { Validation } from './Validation';
import styles from './Batch.module.css';

const DEMO_NAME = 'demo_customers.csv';

export default function BatchPage() {
  const info = useModelInfo();
  const data = info.data;
  const contract = data?.upload_contract ?? undefined;
  const limits = data?.limits ?? undefined;
  const served =
    data && contract && limits
      ? { model: data.display_name, threshold: data.threshold, maxBytes: limits.max_upload_bytes }
      : undefined;

  const [{ id, stage }, dispatch] = useReducer(reducer, INITIAL);
  const choices = useRef(0);
  const request = useRef<AbortController | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  // A refused save is about the current choice: it goes with the next one.
  const { unsaved, save } = useDownload(id);

  // Leaving the page stops the request; the server may still finish its work.
  useEffect(() => {
    const out = request;
    return () => out.current?.abort();
  }, []);

  const waiting = stage.kind === 'rateLimited' && !stage.ready ? stage.wait : null;
  useEffect(() => {
    if (waiting === null) return;
    const timer = window.setTimeout(() => dispatch({ type: 'waited', id }), waiting * 1000);
    return () => window.clearTimeout(timer);
  }, [waiting, id]);

  /** A new choice: the last one's request and results are dropped. */
  const next = () => {
    request.current?.abort();
    request.current = null;
    choices.current += 1;
    return choices.current;
  };

  const choose = (file: File) => {
    if (!served) return;
    const choice = next();
    dispatch({ type: 'begin', id: choice, name: file.name, demo: false });
    if (file.size > served.maxBytes) {
      dispatch({ type: 'tooLarge', id: choice, size: file.size });
      return;
    }
    // A copy of the bytes, so a retry sends what was chosen even if the file changes on disk.
    file.arrayBuffer().then(
      (bytes) => {
        const copy = new File([bytes], file.name, { type: file.type || 'text/csv' });
        dispatch({ type: 'read', id: choice, file: copy });
      },
      () => dispatch({ type: 'unreadable', id: choice }),
    );
  };

  const chooseDemo = () => {
    const choice = next();
    dispatch({ type: 'begin', id: choice, name: DEMO_NAME, demo: true });
    import('../../../../tests/fixtures/demo_customers.csv?raw').then(
      ({ default: text }) => {
        const file = new File([text], DEMO_NAME, { type: 'text/csv' });
        dispatch({ type: 'read', id: choice, file });
      },
      () => dispatch({ type: 'unreadable', id: choice }),
    );
  };

  const drop = (files: FileList) => {
    const [only] = files;
    if (files.length > 1) dispatch({ type: 'several', id: next(), count: files.length });
    else if (only) choose(only);
  };

  const remove = () => {
    dispatch({ type: 'remove', id: next() });
    picker.current?.focus();
  };

  const score = () => {
    if (request.current || !canScore(stage)) return;
    const controller = new AbortController();
    request.current = controller;
    dispatch({ type: 'submit', id });
    void scoreBatch(stage.file, controller.signal)
      .then(
        (result) => dispatch({ type: 'scored', id, result, at: Date.now() }),
        (error: unknown) => dispatch({ type: 'failed', id, error }),
      )
      .finally(() => {
        if (request.current === controller) request.current = null;
      });
  };

  const template = () => {
    if (data && contract)
      save('churnsense_template.csv', toCsv([contract.id_column, ...data.feature_columns], []));
  };

  return (
    <>
      <Header section="Act" page="Batch scoring">
        {/* Marked, not disabled, until the contract is in: the focus is never dropped. */}
        <Button size="lg" aria-disabled={!contract} onClick={template}>
          <icons.file size={15} />
          Download template
        </Button>
      </Header>
      <Page density="dense">
        <PageHeader eyebrow="Batch scoring" title="Score a file of customers" size="md">
          {LEDE}
        </PageHeader>
        <p role="status" className={unsaved ? styles.unsaved : 'sr'}>
          {unsaved ? 'The browser did not save the file.' : ''}
        </p>
        <Steps stage={stage} />
        <div className={styles.panels}>
          <Upload info={info} picker={picker} onChoose={choose} onDrop={drop} onDemo={chooseDemo} />
          {served && (
            <Validation
              stage={stage}
              served={served}
              onScore={score}
              onChoose={() => picker.current?.click()}
              onRemove={remove}
            />
          )}
        </div>
        {stage.kind === 'accepted' && <Results key={id} stage={stage} onSave={save} />}
      </Page>
    </>
  );
}

function Steps({ stage }: { stage: Stage }) {
  return (
    <ol aria-label="Progress" className={styles.steps}>
      {steps(stage).map((step, index) => (
        <li
          key={step.label}
          className={styles.step}
          data-mark={step.mark}
          aria-current={step.current ? 'step' : undefined}
        >
          <span className={styles.mark} aria-hidden="true">
            {step.mark === 'done' ? '✓' : step.mark === 'refused' ? '!' : index + 1}
          </span>
          {step.label}
          {step.mark !== 'todo' && <span className="sr">, {step.mark}</span>}
        </li>
      ))}
    </ol>
  );
}
