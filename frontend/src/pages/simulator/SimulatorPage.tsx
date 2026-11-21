/**
 * Retention simulator: where the cut-off could sit, and what a campaign at it
 * would be worth, under assumptions the reader sets. The API sweeps the
 * validation partition for them (``GET /api/threshold/sweep``); the page shows
 * its figures and works none out.
 *
 * The assumptions and the cut live in the address (``simulator.ts``), checked
 * against the bounds and defaults ``/model-info`` serves. A request goes once
 * all four can be sent, and a response is shown for other assumptions only
 * under a line that says so. Pinned scenarios stay in memory, held here so a
 * tab change keeps them.
 *
 * Three tabs share that one address (``tabs.ts``): Simulate (the controls,
 * the overview with its charts, and the results), Compare (three cuts of the
 * response side by side, and the pins) and Method. Only the open tab is drawn;
 * every one reads the same two queries, so moving between them asks nothing.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { useModelInfo, useSweep } from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button } from '../../components/ui/Button';
import { DataSourceNote, useSweepNote } from '../../components/ui/DataSourceNote';
import { Page } from '../../components/ui/Page';
import { PageTabs } from '../../components/ui/PageTabs';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut } from '../../lib/format';
import { ThreeCuts } from './Compare';
import { AdvancedControls, PrimaryControls, type CutControl } from './Controls';
import { cutUnavailable, LEDE, SIMULATED_NOTE } from './copy';
import { HowMade } from './Method';
import { Overview } from './Overview';
import { Results, RESULTS_ID } from './Results';
import { PinButton, pinning, Scenarios, usePins } from './Scenarios';
import { Sensitivity, WhyNotPeak } from './Sensitivity';
import {
  atDefaults,
  cutAfter,
  pointed,
  pointIndex,
  scenarioOf,
  selection,
  statusOf,
  textsOf,
  type FieldKey,
  type Pin,
} from './simulator';
import { SweepCharts } from './SweepCharts';
import { SIMULATOR_PAGE_PARAMS, SIMULATOR_TABS, type SimulatorTab } from './tabs';
import styles from './Simulator.module.css';

const RESET = { cost: null, success: null, horizon: null, margin: null, t: '' } as const;

export default function SimulatorPage() {
  const [state, set] = useParams(SIMULATOR_PAGE_PARAMS);
  const pins = usePins();
  const info = useModelInfo();
  const sweepNote = useSweepNote();
  const bounds = info.data?.assumption_bounds ?? null;
  const defaults = info.data?.business_defaults ?? null;
  const scenario = bounds && defaults ? scenarioOf(state, bounds, defaults) : null;
  // Unverified partitions withhold every validation outcome for as long as the
  // server runs: then nothing is asked for, and once refused, nothing more.
  const unverified = info.data?.provenance?.verified === false;
  const [never, setNever] = useState(false);
  const sweep = useSweep(never || unverified ? null : (scenario?.assumptions ?? null));
  const refused =
    sweep.error instanceof ApiError &&
    (sweep.error.code === 'outcomes_withheld' || sweep.error.code === 'sweep_withheld');
  // Set while rendering, so the screen never shows the refusal before the latch.
  if (refused && !never) setNever(true);
  const status = statusOf({
    withheld: unverified || never || refused,
    assumptions: scenario?.assumptions ?? null,
    sweep: sweep.data,
    placeholder: sweep.isPlaceholderData,
    error: sweep.error,
  });
  const shown = status === 'withheld' || status === 'failed' ? undefined : sweep.data;
  const selected = shown ? selection(shown, state.t) : null;
  // A response whose recommendation is none of its points draws no cut (the results say so).
  const drawn = shown && selected && selected.index >= 0 ? { sweep: shown, selected } : null;
  const current = status === 'current';
  const resetDone =
    scenario === null || (defaults !== null && atDefaults(scenario, defaults, state.t));

  const settle = (key: FieldKey, text: string) => {
    const change: { -readonly [K in FieldKey]?: string } = {};
    // A decimal comma reaches the address, and the request, as the point.
    change[key] = pointed(text);
    set(change, { replace: true });
  };
  // A cut chosen on figures for other assumptions is kept as chosen: their
  // recommendation is not the one the assumptions set will get.
  const cutAt = (index: number, replace: boolean) => {
    const point = shown?.points[index];
    if (!shown || !point) return;
    set({ t: current ? cutAfter(shown, index) : cut(point.threshold) }, { replace });
  };

  // A failure, withheld outcomes or a response with no cut to draw take the
  // figures away, and turn the cut fader off. If the focus was on one
  // of those, it goes to the results heading, not the body.
  const layout = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<Element | null>(null);
  useEffect(() => {
    const remember = (event: FocusEvent) => {
      if (event.target instanceof Element && layout.current?.contains(event.target))
        lastFocus.current = event.target;
    };
    document.addEventListener('focusin', remember);
    return () => document.removeEventListener('focusin', remember);
  }, []);
  const drew = drawn !== null;
  useEffect(() => {
    const lost = lastFocus.current;
    // Gone, or still there but disabled (the cut fader with no cuts to offer).
    const away = lost && (!lost.isConnected || lost.matches(':disabled'));
    if (away && document.activeElement === document.body)
      document.getElementById(RESULTS_ID)?.focus();
  }, [status, drew]);
  // A tab left by Back or Forward takes its focused control with it: the panel takes the focus.
  // A tab chosen here (where some browsers leave a clicked button unfocused) is not one of them.
  const chosen = useRef(false);
  useEffect(() => {
    const lost = lastFocus.current;
    const byChoice = chosen.current;
    chosen.current = false;
    if (!byChoice && lost && !lost.isConnected && document.activeElement === document.body)
      layout.current?.querySelector<HTMLElement>('[role="tabpanel"]')?.focus();
  }, [state.tab]);
  const apply = (pin: Pin) => set({ ...textsOf(pin.assumptions), t: pin.t });
  // A tab is a place to go back to: one history entry each, none for the same tab.
  const choose = (tab: SimulatorTab) => {
    if (tab === state.tab) return;
    chosen.current = true;
    set({ tab });
  };
  const pinNow = pinning(pins, shown, selected, current);
  const control: CutControl = {
    points: drawn ? drawn.sweep.points : [],
    churners: shown?.churners ?? 0,
    index: drawn ? drawn.selected.index : null,
    recommended: drawn ? drawn.selected.recommended : -1,
    served: drawn ? pointIndex(drawn.sweep.points, drawn.sweep.shipped_threshold) : -1,
    following: drawn?.selected.following ?? true,
    loading: status === 'loading' || status === 'updating',
    unavailable:
      drawn || status === 'loading' || status === 'updating'
        ? null
        : cutUnavailable(status, shown !== undefined),
    echo: drawn ? drawn.sweep.assumptions : null,
    stale: !current,
    onCut: (index) => cutAt(index, true),
    onFollow: () => set({ t: '' }),
  };

  function panel(): ReactNode {
    if (!scenario || !bounds || !defaults || !info.data) return null;
    const assumptionProps = { scenario, bounds, defaults, onSettle: settle };
    if (state.tab === 'compare') {
      return (
        <div className={styles.stack}>
          {drawn ? (
            <ThreeCuts sweep={drawn.sweep} selected={drawn.selected} stale={!current} />
          ) : (
            results(null)
          )}
          {status !== 'withheld' && (
            <Scenarios store={pins} pinning={pinNow} current={current} onApply={apply} />
          )}
        </div>
      );
    }
    if (state.tab === 'method') {
      return (
        <div className={styles.stack}>
          <HowMade model={info.data} bounds={bounds} defaults={defaults} sweep={shown} />
          {drawn ? (
            <div className={styles.readings}>
              <WhyNotPeak sweep={drawn.sweep} stale={!current} />
              <Sensitivity
                sweep={drawn.sweep}
                range={bounds.offer_success_rate}
                current={current}
                onApply={(rate) => set({ success: String(rate), t: '' })}
              />
            </div>
          ) : (
            results(null)
          )}
        </div>
      );
    }
    return (
      <div className={styles.workspaceFrame}>
        <div className={styles.workspace}>
          {/*
            In the order a phone shows them: the controls, what they give, the
            rest of the controls, then the overview. Wider layouts place them
            side by side without changing that order.
          */}
          <div className={styles.areaPrimary}>
            <PrimaryControls {...assumptionProps} control={control} />
          </div>
          <div className={styles.areaResults}>
            {results(
              status === 'withheld' ? null : <PinButton pinning={pinNow} whyId="pin-why-results" />,
            )}
          </div>
          <div className={styles.areaAdvanced}>
            <AdvancedControls {...assumptionProps} />
          </div>
          <div className={styles.areaOverview}>
            <Overview
              scenario={scenario}
              defaults={defaults}
              handCut={drawn && !drawn.selected.following ? state.t : ''}
              recommended={drawn ? drawn.sweep.recommended.threshold : null}
            />
            {drawn && (
              <SweepCharts
                sweep={drawn.sweep}
                selected={drawn.selected}
                stale={!current}
                onPick={(index) => cutAt(index, false)}
              />
            )}
          </div>
        </div>
      </div>
    );
  }

  function results(pin: ReactNode) {
    return (
      <Results
        status={status}
        sweep={shown}
        selected={selected}
        error={sweep.error}
        sweepNote={sweepNote}
        onRetry={() => void sweep.refetch()}
        pin={pin}
      />
    );
  }

  return (
    <>
      <Header section="Act" page="Retention simulator">
        <Button
          size="lg"
          aria-disabled={resetDone}
          // Always set: an edit not yet settled is written first, then reset;
          // with none, at the defaults, the address does not change.
          onClick={() => set(RESET)}
        >
          Reset to defaults
        </Button>
      </Header>
      <Page>
        <PageHeader eyebrow="Retention simulator" tone="sim" title="Where should the cut-off sit?">
          {LEDE}
        </PageHeader>
        <DataSourceNote parts={['evidence']} />
        <div role="note" className={styles.simNote}>
          <icons.warning size={18} className={styles.simNoteIcon} />
          <p>
            <b>{SIMULATED_NOTE.title}</b> {SIMULATED_NOTE.body}
          </p>
        </div>
        {info.isPending ? (
          <Skeleton label="Loading the served assumptions" rows={6} />
        ) : info.isError ? (
          <ErrorState onRetry={() => void info.refetch()} focusTo={MAIN_ID}>
            The served model’s assumptions and their bounds could not be loaded, so nothing can be
            simulated yet.
          </ErrorState>
        ) : !scenario || !bounds || !defaults ? (
          <p role="alert" className={styles.unserved}>
            The served model does not say which assumptions it accepts, or their defaults, so
            nothing can be simulated here.
          </p>
        ) : (
          <div ref={layout}>
            <PageTabs
              def={SIMULATOR_TABS}
              label="Simulator sections"
              value={state.tab}
              onChange={choose}
            >
              {panel()}
            </PageTabs>
          </div>
        )}
      </Page>
    </>
  );
}
