/**
 * Explainability: how the shipped model uses each attribute. The global
 * figures are ``make explain``'s cached attributions for a validation sample
 * (``/api/explain/global``); one book customer can be placed on them from
 * their own explanation (``/api/explain``), which is asked for only once the
 * cached figures are in, the model's description agrees with them, and the
 * server can explain anyone at all. The page shows served figures; it
 * averages, sums or ranks none.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiError } from '../../api/client';
import {
  useExplanations,
  useGlobalExplanation,
  useModelInfo,
  type GlobalExplanation,
  type ModelInfo,
} from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button, ButtonLink } from '../../components/ui/Button';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { isFeature } from '../../lib/labels';
import { FAILED, LEDE, MASKING_CAVEAT, MISMATCH, NOT_CACHED, TITLE } from './copy';
import { CustomerCard, type Asked } from './CustomerCard';
import { Dependence } from './Dependence';
import {
  comparability,
  EXPLAIN_PARAMS,
  featureChange,
  pickExplanation,
  selectedFeature,
} from './explain';
import { MethodStrip } from './MethodStrip';
import { Rankings } from './Rankings';
import { Swarm } from './Swarm';
import styles from './Explain.module.css';

function notCached(error: Error | null): boolean {
  return error instanceof ApiError && error.code === 'explanations_not_cached';
}

export default function ExplainabilityPage() {
  const client = useQueryClient();
  const info = useModelInfo();
  const global = useGlobalExplanation();
  const reload = () => {
    void info.refetch();
    void global.refetch();
  };
  /** The model changed under the page: describe it again, and every attribution goes. */
  const reloadFigures = () => {
    void info.refetch();
    void client.invalidateQueries({ queryKey: ['explain'] });
    // The control goes with its alert: the focus waits on the page, not the body.
    document.getElementById(MAIN_ID)?.focus();
  };

  let body;
  if (info.isPending || global.isPending) {
    body = <Skeleton label="Loading the cached attributions" rows={6} />;
  } else if (global.isError && notCached(global.error)) {
    body = (
      <EmptyState
        title={NOT_CACHED.title}
        action={{
          label: 'Check again',
          onClick: () => {
            void global.refetch();
            // The button goes once the figures come: the focus waits on the page.
            document.getElementById(MAIN_ID)?.focus();
          },
        }}
      >
        {NOT_CACHED.body}
      </EmptyState>
    );
  } else if (info.isError || global.isError) {
    body = (
      <ErrorState onRetry={reload} focusTo={MAIN_ID}>
        {FAILED}
      </ErrorState>
    );
  } else if (
    info.data.model_sha256 != null &&
    info.data.model_sha256 !== global.data.model_sha256
  ) {
    body = (
      <div role="alert" className={styles.mismatch}>
        <p>{MISMATCH}</p>
        <Button size="md" onClick={reloadFigures}>
          Reload figures
        </Button>
      </div>
    );
  } else {
    body = <Explained global={global.data} info={info.data} onReload={reloadFigures} />;
  }

  return (
    <>
      <Header section="Model" page="Explainability">
        <ButtonLink variant="ghost" size="lg" to="/customer">
          Explain one customer <icons.arrowRight size={14} />
        </ButtonLink>
      </Header>
      <Page density="dense">{body}</Page>
    </>
  );
}

/** The figures, once they are in and describe the served artifact. */
function Explained({
  global,
  info,
  onReload,
}: {
  global: GlobalExplanation;
  info: ModelInfo;
  onReload: () => void;
}) {
  const [state, set, href] = useParams(EXPLAIN_PARAMS);
  const feature = selectedFeature(global, state.f);
  // Studying another attribute is a view of the same figures: no history entry.
  const pick = (next: string) => {
    if (isFeature(next)) set({ f: featureChange(global, next) }, { replace: true });
  };

  const id = state.id;
  const unverified = info.provenance?.verified === false;
  // Without its verified background the server explains no one for as long as it
  // runs: once it has said so, nothing more is asked for.
  const [never, setNever] = useState(false);
  const blocked = unverified || never;
  const query = useExplanations(id !== '' && !blocked ? [id] : []);
  const error = query.error;
  if (!never && error instanceof ApiError && error.code === 'explanations_unavailable')
    setNever(true);

  const explanation = pickExplanation(query.data, id);
  const comparable =
    explanation && query.data ? comparability(global, query.data, explanation) : null;
  const ringed = comparable === 'ok' ? explanation : undefined;

  let asked: Asked;
  if (unverified) asked = { kind: 'unverified' };
  else if (never) asked = { kind: 'unavailable' };
  else if (id === '') asked = { kind: 'none' };
  else if (error instanceof ApiError && error.status === 404) asked = { kind: 'notFound' };
  else if (query.isError) asked = { kind: 'failed', retry: () => void query.refetch() };
  else if (query.isPending) asked = { kind: 'pending' };
  else if (explanation && comparable) asked = { kind: 'ready', explanation, comparable };
  else asked = { kind: 'failed', retry: () => void query.refetch() };

  return (
    <>
      <PageHeader eyebrow="Explainability" title={TITLE} size="md">
        {LEDE}
      </PageHeader>
      <MethodStrip global={global} />
      <CustomerCard
        id={id}
        asked={asked}
        global={global}
        blocked={blocked}
        href={(next) => href({ id: next })}
        onPlace={(next) => set({ id: next })}
        onClear={() => set({ id: '' })}
        onReload={onReload}
      />
      {feature !== null && (
        <div className={styles.pair}>
          <Swarm global={global} feature={feature} ringed={ringed} onPick={pick} />
          <Dependence key={feature} global={global} feature={feature} ringed={ringed} />
        </div>
      )}
      <Rankings global={global} feature={feature} onPick={pick} />
      <div className={styles.footer}>
        <p>{MASKING_CAVEAT}</p>
        <p>{global.caveat}</p>
      </div>
    </>
  );
}
