/**
 * Model performance: how well the served model ranks churners, which errors
 * a cut makes, whether its probabilities hold up, and where each figure
 * comes from. ``/api/performance`` measures the served model on validation
 * and serves the training records and the recorded test evaluation (or why
 * there is none); ``/api/model-info`` describes the artifact. The page shows
 * their figures and works none out.
 *
 * The two responses must describe one artifact: if they name different
 * ones, no figure is shown until both are asked again.
 */
import { useState } from 'react';
import { useModelInfo, usePerformance } from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button, ButtonLink } from '../../components/ui/Button';
import { DataSourceNote } from '../../components/ui/DataSourceNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut } from '../../lib/format';
import { Calibration } from './Calibration';
import { Confusion, type View } from './Confusion';
import { FAILED, FOOTER, lede, MISMATCH, shortSha, TITLE } from './copy';
import { explored, PERFORMANCE_PARAMS } from './performance';
import { PrCurve } from './PrCurve';
import { Provenance } from './Provenance';
import { Scorecard } from './Scorecard';
import { Segments } from './Segments';
import { Selection } from './Selection';
import styles from './Performance.module.css';

const REPO = 'https://github.com/HeyderHesenov/churnsense-ai/blob/main';

export default function PerformancePage() {
  const [state, set] = useParams(PERFORMANCE_PARAMS);
  const info = useModelInfo();
  const performance = usePerformance();
  const [view, setView] = useState<View>('validation');
  const reload = () => {
    void info.refetch();
    void performance.refetch();
  };

  let body;
  if (info.isPending || performance.isPending) {
    body = <Skeleton label="Loading the model’s evaluation" rows={6} />;
  } else if (info.isError || performance.isError) {
    body = (
      <ErrorState onRetry={reload} focusTo={MAIN_ID}>
        {FAILED}
      </ErrorState>
    );
  } else if (
    info.data.model_sha256 != null &&
    performance.data.model_sha256 != null &&
    info.data.model_sha256 !== performance.data.model_sha256
  ) {
    body = (
      <div role="alert" className={styles.mismatch}>
        <p>{MISMATCH}</p>
        <Button
          size="md"
          onClick={() => {
            reload();
            // The button goes with the alert: the focus waits on the page, not the body.
            document.getElementById(MAIN_ID)?.focus();
          }}
        >
          Reload figures
        </Button>
      </div>
    );
  } else {
    const model = info.data;
    const figures = performance.data;
    const points = figures.validation?.points ?? [];
    const index = explored(points, state.cut, figures.threshold);
    const served = cut(figures.threshold);
    const setCut = (i: number, replace: boolean) => {
      const point = points[i];
      if (!point) return;
      const label = cut(point.threshold);
      set({ cut: label === served ? '' : label }, { replace });
    };
    const sha = figures.model_sha256 ?? model.model_sha256 ?? null;
    body = (
      <>
        <PageHeader eyebrow="Model performance" title={TITLE}>
          {lede(
            figures.selection?.folds ?? null,
            model.n_train,
            model.n_validation,
            figures.test !== null,
          )}
        </PageHeader>
        <Provenance model={model} figures={figures} />
        <Scorecard figures={figures} />
        <div className={styles.pair}>
          <Confusion
            figures={figures}
            model={model}
            index={index}
            onCut={(i) => setCut(i, true)}
            view={view}
            onView={setView}
          />
          <PrCurve
            figures={figures}
            index={index}
            onPick={(i) => {
              setView('validation');
              setCut(i, false);
            }}
          />
        </div>
        <Segments figures={figures} />
        <div className={styles.pair}>
          <Calibration figures={figures} />
          <Selection figures={figures} />
        </div>
        <div className={styles.footer}>
          <p>{FOOTER}</p>
          {sha && <p className={styles.mono}>artifact {shortSha(sha)}</p>}
        </div>
      </>
    );
  }

  return (
    <>
      <Header section="Model" page="Model performance">
        <ButtonLink
          variant="ghost"
          size="lg"
          href={`${REPO}/docs/MODEL_CARD.md`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Model card <icons.arrowUpRight size={14} />
        </ButtonLink>
      </Header>
      <Page>
        <DataSourceNote parts={['evidence']} />
        {body}
      </Page>
    </>
  );
}
