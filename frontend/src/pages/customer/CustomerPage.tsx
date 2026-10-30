/**
 * Single customer: what moved one customer's estimate. The probability, the
 * band, the flag and every attribution are the API's; the page arranges them.
 *
 * Who is shown lives in the address (``customer.ts``); what is typed for a
 * new customer stays in memory. Each kind of customer has its own requests,
 * so a figure of one is never drawn as another's while the next loads: a
 * customer in the book comes with its detail, the README example and the new
 * customer from ``POST /api/predict``. A typed record is scored once typing
 * settles, and explained only once its probability is in.
 */
import { useMemo, useState } from 'react';
import { flushSync } from 'react-dom';
import { ApiError } from '../../api/client';
import {
  useCustomer,
  useExamples,
  useExplanations,
  useModelInfo,
  usePrediction,
  useRecordExplanation,
  type CustomerFeatures,
  type Explanation,
  type ExplainResponse,
  type Prediction,
} from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button } from '../../components/ui/Button';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState } from '../../components/ui/States';
import { toCsv } from '../../lib/csv';
import { useDebouncedValue } from '../../lib/useDebouncedValue';
import { useDownload } from '../../lib/useDownload';
import { ApiPanel } from './ApiPanel';
import { LEDE, notFound, statusLine } from './copy';
import {
  amountProblems,
  choiceKey,
  CUSTOMER_PARAMS,
  draftOf,
  pickerChoices,
  recordOf,
  showChanges,
  shownBy,
  type Draft,
} from './customer';
import { Picker } from './Picker';
import { Prediction as PredictionCard, type Score, type ScoreState } from './Prediction';
import { LiveForm, Profile, TENURE_ID } from './Profile';
import { Sentences, Waterfall, type ExplainState } from './Waterfall';
import styles from './Customer.module.css';

/** How long typing settles before the API is asked, and again before it explains. */
const SETTLE_MS = 250;

/** The name the README example goes by, as the artboard's picker calls it. */
const README_NAME = 'API example';

interface Explained {
  state: ExplainState;
  explanation: Explanation | undefined;
  response: ExplainResponse | undefined;
  retry: () => void;
}

/**
 * Where one customer's attributions stand. ``never``: the server has said it
 * cannot explain anyone; ``blocked``: the inputs cannot be scored, so nothing
 * is asked for; ``waiting``: the probability is not in yet.
 */
function explained(
  query: {
    data?: ExplainResponse | undefined;
    error: Error | null;
    isPlaceholderData: boolean;
    refetch: () => unknown;
  },
  pick: (response: ExplainResponse) => Explanation | undefined,
  { never, blocked = false, waiting }: { never: boolean; blocked?: boolean; waiting: boolean },
): Explained {
  const response = query.data;
  const explanation = response ? pick(response) : undefined;
  const state: ExplainState = never
    ? 'unavailable'
    : blocked
      ? 'blocked'
      : query.error
        ? 'failed'
        : explanation && query.isPlaceholderData
          ? 'stale'
          : explanation
            ? 'ready'
            : waiting
              ? 'waiting'
              : 'pending';
  return { state, explanation, response, retry: () => void query.refetch() };
}

/** The server's answer when it has no verified training background: it can explain no one. */
function explainsNoOne(error: Error | null): boolean {
  return error instanceof ApiError && error.code === 'explanations_unavailable';
}

function scoreOf(served: {
  churn_probability: number;
  risk_band: string;
  flagged: boolean;
  threshold: number;
}): Score {
  return {
    probability: served.churn_probability,
    band: served.risk_band,
    flagged: served.flagged,
    threshold: served.threshold,
  };
}

export default function CustomerPage() {
  const [state, set, href] = useParams(CUSTOMER_PARAMS);
  const shown = shownBy(state);
  const info = useModelInfo();
  const bounds = info.data?.numeric_bounds ?? undefined;
  const examples = useExamples();
  const readme = examples.data?.readme_example ?? null;
  const exampleIds = useMemo(
    () => examples.data?.customers.map((c) => c.id) ?? [],
    [examples.data],
  );

  // --- a customer in the book ------------------------------------------------------
  const bookId = shown.kind === 'book' ? shown.id : null;
  const example = examples.data?.customers.find((c) => c.id === bookId);
  // An example's detail came with the examples; another customer's is asked for,
  // once the examples have said whether it is one of them.
  const fetched = useCustomer(bookId !== null && !example && !examples.isPending ? bookId : null);
  const detail = example ?? fetched.data;
  const missing = fetched.error instanceof ApiError && fetched.error.status === 404;
  // A customer a link opened is the picker's first card; once it has loaded, it
  // stays there while the page is open. One not in the book is not kept.
  const [remembered, setRemembered] = useState<string | null>(null);
  const linkedNow =
    bookId !== null && !examples.isPending && !exampleIds.includes(bookId) ? bookId : null;
  if (linkedNow !== null && fetched.data !== undefined && linkedNow !== remembered)
    setRemembered(linkedNow);
  const linked = linkedNow ?? remembered;
  // Without its verified background the server explains no one for as long as it
  // runs: once it has said so, nothing more is asked for.
  const [never, setNever] = useState(false);
  const bookPrediction = usePrediction(shown.kind === 'book' && detail ? detail.features : null);
  const bookExplanation = useExplanations(
    !never && shown.kind === 'book' && detail ? [detail.id] : [],
  );

  // --- the README example (its card shows its probability on every view) ------------
  const readmePrediction = usePrediction(readme);
  const readmeExplanation = useRecordExplanation(
    !never && shown.kind === 'readme' && readmePrediction.isSuccess ? readme : null,
  );

  // --- the new customer ----------------------------------------------------------
  const [typed, setTyped] = useState<Draft | null>(null);
  const readmeDraft = useMemo(() => (readme ? draftOf(readme) : null), [readme]);
  const form = typed ?? readmeDraft;
  const parsed = useMemo(() => (form ? recordOf(form) : null), [form]);
  // The bounds come in later than the form, so they are checked apart: their
  // arrival never makes a new record of the same inputs.
  const amountIssues = form ? amountProblems(form, bounds) : [];
  const record = amountIssues.length > 0 ? null : parsed;
  const scored = useDebouncedValue(record, SETTLE_MS);
  const asked = useDebouncedValue(scored, SETTLE_MS);
  const live = shown.kind === 'live';
  const livePrediction = usePrediction(live ? scored : null);
  // Explained only once the reader has paused on a record (it is still the one
  // typed, so the one scored) and the API has scored that very record.
  const explainable =
    asked !== null &&
    asked === record &&
    livePrediction.isSuccess &&
    !livePrediction.isPlaceholderData;
  const liveExplanation = useRecordExplanation(!never && live && explainable ? asked : null);
  const saysNever = [bookExplanation, readmeExplanation, liveExplanation].some((query) =>
    explainsNoOne(query.error),
  );
  // Set while rendering, so the screen never shows the answer before the latch.
  if (saysNever && !never) setNever(true);

  // --- what the page draws ---------------------------------------------------------
  let name = README_NAME;
  let id: string | null = null;
  /** The inputs the figures are for (the settled record, for a new customer). */
  let features: CustomerFeatures | null;
  let score: Score | undefined;
  let scoreState: ScoreState;
  let problems: readonly string[] = [];
  let retryScore: () => void;
  let panel: Prediction | undefined;
  /** Whether the API panel is waiting for an answer, rather than having none to show. */
  let answering: boolean;
  let explain: Explained;

  if (shown.kind === 'book') {
    name = shown.id;
    id = shown.id;
    features = detail?.features ?? null;
    score = detail && scoreOf(detail);
    scoreState = detail ? 'ready' : missing ? 'missing' : fetched.isError ? 'failed' : 'pending';
    retryScore = () => void fetched.refetch();
    panel = bookPrediction.isPlaceholderData ? undefined : bookPrediction.data;
    answering = !bookPrediction.isError;
    explain = explained(
      bookExplanation,
      (response) => response.explanations.find((e) => e.customer_id === shown.id),
      { never, waiting: !detail },
    );
  } else if (shown.kind === 'readme') {
    features = readme;
    const data = readmePrediction.data;
    score = data && scoreOf(data);
    scoreState = data ? 'ready' : readmePrediction.isError ? 'failed' : 'pending';
    retryScore = () => void readmePrediction.refetch();
    panel = data;
    answering = !readmePrediction.isError;
    explain = explained(readmeExplanation, (response) => response.explanations[0], {
      never,
      blocked: readmePrediction.isError,
      waiting: !data,
    });
  } else {
    name = 'New customer';
    features = scored;
    const data = livePrediction.isError ? undefined : livePrediction.data;
    score = data && scoreOf(data);
    const settling = record !== scored || livePrediction.isPlaceholderData;
    scoreState =
      amountIssues.length > 0
        ? 'invalid'
        : livePrediction.isError
          ? 'failed'
          : data && settling
            ? 'stale'
            : data
              ? 'ready'
              : 'pending';
    problems =
      livePrediction.error instanceof ApiError && livePrediction.error.status === 422
        ? livePrediction.error.problems
        : [];
    retryScore = () => void livePrediction.refetch();
    panel = scoreState === 'ready' ? data : undefined;
    answering = scoreState !== 'invalid' && scoreState !== 'failed';
    explain = explained(liveExplanation, (response) => response.explanations[0], {
      never,
      blocked: scoreState === 'invalid' || scoreState === 'failed',
      waiting: scoreState !== 'ready' && scoreState !== 'stale',
    });
  }
  const examplesFailed = examples.isError && shown.kind !== 'book';
  // What the CSV and the announcement describe: settled figures and the inputs they are for.
  const settled =
    scoreState === 'ready' && score && features ? { score, features, id, name } : null;

  // A refused save is about the figures shown then: it goes once another
  // customer is shown or the form is edited.
  const { unsaved, save, forget } = useDownload(choiceKey(shown));
  const edit = (next: Draft | null) => {
    setTyped(next);
    forget();
  };
  const download = () => {
    if (!settled) return;
    const columns = Object.keys(settled.features) as (keyof CustomerFeatures)[];
    const ids = settled.id ? [settled.id] : [];
    save(
      'churnsense_single_prediction.csv',
      toCsv(
        [
          ...(settled.id ? ['customerID'] : []),
          ...columns,
          'churn_probability',
          'risk_band',
          'flagged',
        ],
        [
          [
            ...ids,
            ...columns.map((key) => settled.features[key]),
            String(settled.score.probability),
            settled.score.band,
            settled.score.flagged,
          ],
        ],
      ),
    );
  };

  // The form mounts with the change of address: commit it, then type in it.
  const scoreNew = () => {
    if (!live) flushSync(() => set(showChanges({ kind: 'live' })));
    document.getElementById(TENURE_ID)?.focus();
  };

  return (
    <>
      <Header section="Model" page="Single customer">
        {/* Marked, not disabled, while there is nothing to save: Back or Forward must not drop the focus. */}
        <Button size="lg" aria-disabled={!settled} onClick={download}>
          <icons.download size={15} />
          Download prediction (CSV)
        </Button>
        <Button variant="primary" size="lg" onClick={scoreNew}>
          <icons.plus size={15} />
          Score a new customer
        </Button>
      </Header>
      <Page>
        <PageHeader eyebrow="Single customer · explainability" title="What moved this estimate">
          {LEDE}
        </PageHeader>
        <p role="status" className={unsaved ? styles.unsaved : 'sr'}>
          {unsaved ? 'The browser did not save the file.' : ''}
        </p>
        <Picker
          choices={pickerChoices(exampleIds, linked)}
          shown={shown}
          href={(choice) => href(showChanges(choice))}
          readmeName={README_NAME}
          readme={readmePrediction.data}
          readmeFailed={readmePrediction.isError || examples.isError}
          examples={examples.data?.customers ?? []}
          live={live && scoreState === 'ready' ? score : undefined}
        />
        {examples.isError && (
          <ErrorState inline onRetry={() => void examples.refetch()} focusTo={MAIN_ID}>
            The example customers could not be loaded.
          </ErrorState>
        )}
        <p role="status" className="sr">
          {settled
            ? statusLine(
                settled.name,
                settled.score.probability,
                settled.score.band,
                settled.score.flagged,
                settled.score.threshold,
              )
            : scoreState === 'missing'
              ? notFound(name)
              : ''}
        </p>
        {examplesFailed ? null : scoreState === 'missing' ? (
          <p className={styles.missing}>{notFound(name)}</p>
        ) : shown.kind === 'book' && scoreState === 'failed' ? (
          <ErrorState onRetry={retryScore} focusTo={MAIN_ID}>
            This customer could not be loaded.
          </ErrorState>
        ) : (
          <div className={styles.columns}>
            {live && form ? (
              <LiveForm form={form} bounds={bounds} onChange={edit} onReset={() => edit(null)} />
            ) : (
              <Profile
                features={features}
                strongest={explain.state === 'ready' ? explain.explanation : undefined}
              />
            )}
            <div className={styles.results}>
              <PredictionCard
                state={scoreState}
                score={score}
                problems={problems}
                bands={info.data?.risk_bands ?? undefined}
                onRetry={retryScore}
              />
              <Waterfall
                {...explain}
                band={settled?.score.band}
                threshold={score?.threshold ?? info.data?.threshold}
              />
              <div className={styles.lower}>
                <Sentences state={explain.state} explanation={explain.explanation} />
                <ApiPanel prediction={panel} answering={answering} />
              </div>
            </div>
          </div>
        )}
      </Page>
    </>
  );
}
