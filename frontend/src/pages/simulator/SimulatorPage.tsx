/**
 * Retention simulator: where the cut-off could sit, and what a campaign at it
 * would be worth, under assumptions the reader sets. The API sweeps the
 * validation partition for them (``GET /api/threshold/sweep``); the page shows
 * its figures and works none out.
 *
 * The assumptions and the cut live in the address (``simulator.ts``), checked
 * against the bounds and defaults ``/model-info`` serves. A request goes once
 * all four can be sent, and a response is shown for other assumptions only
 * under a line that says so. Pinned scenarios stay in memory.
 */
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { useModelInfo, useSweep } from '../../api/queries';
import { useParams } from '../../app/params';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { MAIN_ID } from '../../components/shell/Shell';
import { Button } from '../../components/ui/Button';
import { DataSourceNote, useSweepNote } from '../../components/ui/DataSourceNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { cut } from '../../lib/format';
import { Assumptions } from './Assumptions';
import { LEDE, SIMULATED_NOTE } from './copy';
import { CutCard, Results, RESULTS_ID } from './Results';
import { Scenarios } from './Scenarios';
import { Sensitivity, WhyNotPeak } from './Sensitivity';
import {
  atDefaults,
  cutAfter,
  scenarioOf,
  selection,
  SIMULATOR_PARAMS,
  statusOf,
  textsOf,
  type FieldKey,
  type Pin,
} from './simulator';
import { SweepCharts } from './SweepCharts';
import styles from './Simulator.module.css';

const RESET = { cost: null, success: null, horizon: null, margin: null, t: '' } as const;

export default function SimulatorPage() {
  const [state, set] = useParams(SIMULATOR_PARAMS);
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
    change[key] = text;
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
  // figures, and the controls drawn from them, away. If the focus was on one
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
    if (lost && !lost.isConnected && document.activeElement === document.body)
      document.getElementById(RESULTS_ID)?.focus();
  }, [status, drew]);
  const apply = (pin: Pin) => set({ ...textsOf(pin.assumptions), t: pin.t });

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
          <div ref={layout} className={styles.layout}>
            <div className={styles.controls}>
              <Assumptions
                scenario={scenario}
                bounds={bounds}
                defaults={defaults}
                onSettle={settle}
              />
              {drawn && (
                <CutCard
                  sweep={drawn.sweep}
                  selected={drawn.selected}
                  stale={!current}
                  onCut={(index) => cutAt(index, true)}
                  onFollow={() => set({ t: '' })}
                />
              )}
            </div>
            <div className={styles.output}>
              <Results
                status={status}
                sweep={shown}
                selected={selected}
                error={sweep.error}
                sweepNote={sweepNote}
                onRetry={() => void sweep.refetch()}
              />
              {drawn && (
                <>
                  <SweepCharts
                    sweep={drawn.sweep}
                    selected={drawn.selected}
                    stale={!current}
                    onPick={(index) => cutAt(index, false)}
                  />
                  <div className={styles.readings}>
                    <WhyNotPeak sweep={drawn.sweep} stale={!current} />
                    <Sensitivity
                      sweep={drawn.sweep}
                      range={bounds.offer_success_rate}
                      current={current}
                      onApply={(rate) => set({ success: String(rate), t: '' })}
                    />
                  </div>
                </>
              )}
              {status !== 'withheld' && (
                <Scenarios sweep={shown} selected={selected} current={current} onApply={apply} />
              )}
            </div>
          </div>
        )}
      </Page>
    </>
  );
}
