/**
 * Model performance: how well the served model ranks churners, which errors
 * a cut makes, whether its probabilities hold up, and where each figure
 * comes from. ``/api/performance`` measures the served model on validation
 * and serves the training records and the recorded test evaluation (or why
 * there is none); ``/api/model-info`` describes the artifact. The page shows
 * their figures and works none out.
 *
 * Four tabs (summary, model choice, errors and trade-offs, calibration and
 * blind spots); the tab, the partition and the explored cut live in the
 * address, so a view can be linked and Back undoes a change. Every tab reads
 * the one shared response: changing tab or partition asks the server nothing.
 *
 * The two responses must describe one artifact: if they name different
 * ones, no figure is shown until both are asked again.
 */
import { useModelInfo, usePerformance } from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button, ButtonLink } from '../../components/ui/Button';
import { DataSourceNote } from '../../components/ui/DataSourceNote';
import { InfoNote } from '../../components/ui/InfoNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageTabs } from '../../components/ui/PageTabs';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { Card, CardHeader } from '../../components/ui/Surface';
import { cut } from '../../lib/format';
import { Calibration } from './Calibration';
import { FAILED, FOOTER, lede, MISMATCH, REUSED_HOLDOUT, ROLES, shortSha, TITLE } from './copy';
import { ErrorsTab } from './Errors';
import { PartitionSwitch } from './Partition';
import { explored } from './performance';
import { PlainCards } from './Plain';
import { Provenance } from './Provenance';
import { Segments } from './Segments';
import { Selection } from './Selection';
import { PERFORMANCE_PARAMS, PERFORMANCE_TABS, type Partition, type PerformanceTab } from './tabs';
import { TestTiles, ValidationTiles } from './Tiles';
import { Waffle } from './Waffle';
import styles from './Performance.module.css';

const REPO = 'https://github.com/HeyderHesenov/churnsense-ai/blob/main';

export default function PerformancePage() {
  const [state, set] = useParams(PERFORMANCE_PARAMS);
  const info = useModelInfo();
  const performance = usePerformance();
  const reload = () => {
    void info.refetch();
    void performance.refetch();
  };
  const choose = (tab: PerformanceTab) => {
    if (tab !== state.tab) set({ tab });
  };
  const choosePart = (part: Partition) => {
    if (part !== state.part) set({ part });
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
    const setCut = (i: number, replace: boolean, extra: { part?: Partition } = {}) => {
      const point = points[i];
      if (!point) return;
      const label = cut(point.threshold);
      set({ cut: label === served ? '' : label, ...extra }, { replace });
    };
    const sha = figures.model_sha256 ?? model.model_sha256 ?? null;
    const tab = state.tab;
    const part = state.part;

    let panel;
    if (tab === 'summary') {
      panel = (
        <div className={styles.tab}>
          <Provenance model={model} figures={figures} />
          <Card titleId="figures-title">
            <CardHeader
              id="figures-title"
              title="How well it performs"
              subtitle={
                figures.test
                  ? 'How churners are ranked and whether the probabilities hold up (validation), and what the recorded cut caught (test).'
                  : 'How churners are ranked and whether the probabilities hold up (validation).'
              }
              aside={<PartitionSwitch value={part} onChange={choosePart} />}
            />
            {part === 'test' ? (
              <TestTiles figures={figures} />
            ) : (
              <ValidationTiles figures={figures} />
            )}
          </Card>
          {part === 'test' && figures.test && (
            <div className={styles.wafflePair}>
              <Waffle record={figures.test} />
              <PlainCards record={figures.test} />
            </div>
          )}
        </div>
      );
    } else if (tab === 'choice') {
      panel = (
        <div className={styles.tab}>
          <Selection figures={figures} />
        </div>
      );
    } else if (tab === 'errors') {
      panel = (
        <div className={styles.tab}>
          <ErrorsTab
            figures={figures}
            model={model}
            index={index}
            onCut={(i) => setCut(i, true)}
            onPick={(i) => setCut(i, false, { part: 'validation' })}
            part={part}
            onPart={choosePart}
          />
        </div>
      );
    } else {
      panel = (
        <div className={styles.tab}>
          <Calibration figures={figures} />
          <Segments figures={figures} />
        </div>
      );
    }

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
        {figures.test && <InfoNote title={REUSED_HOLDOUT}>{ROLES.recorded}</InfoNote>}
        <PageTabs def={PERFORMANCE_TABS} label="Performance sections" value={tab} onChange={choose}>
          {panel}
        </PageTabs>
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
