/**
 * The selection's figures, every one the API's summary of all matches (not
 * only the page shown). They depend on the filters only, so a new page,
 * order or page size keeps them. While a new selection loads its figures read
 * "…", never the last selection's, and "—" if they could not be loaded (with
 * a way to ask again); a screen reader hears one line once they are in.
 */
import type { CustomerPage, ModelInfo } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { ErrorState } from '../../components/ui/States';
import { StatTile } from '../../components/ui/Surface';
import { bandTop } from '../../lib/bands';
import { cut, int, money, pct, probability } from '../../lib/format';
import { flaggedLabel, observedTile, selectionStatus } from './copy';
import type { IdleReason } from './explorer';
import styles from './Explorer.module.css';

type SelectionSummary = CustomerPage['summary'];

interface SummaryProps {
  /** The API's answer for the current filters; undefined while it loads or fails. */
  selection: CustomerPage | undefined;
  /** That answer could not be loaded. */
  failed: boolean;
  onRetry: () => void;
  idle: IdleReason;
  info: ModelInfo | undefined;
}

/** Where the reader continues when the figures are asked for again. */
const SUMMARY_ID = 'selection-summary';

export function Summary({ selection, failed, onRetry, idle, info }: SummaryProps) {
  const threshold = info?.threshold;
  // While idle the list says why; the summary does not say it again.
  const status = !idle && selection ? selectionStatus(selection.summary) : '';
  const blank = idle || failed ? '—' : '…';
  return (
    <section
      id={SUMMARY_ID}
      tabIndex={-1}
      aria-label="Selection summary"
      className={styles.summary}
    >
      <p role="status" className="sr">
        {status}
      </p>
      {!idle && selection ? (
        <Tiles
          summary={selection.summary}
          threshold={threshold}
          withheld={selection.withheld_reason}
        />
      ) : (
        <>
          <StatTile compact label="Customers" value={idle ? '0' : blank} />
          <StatTile compact label={flaggedLabel(threshold)} value={blank} />
          <StatTile compact label="Mean probability" value={blank} />
          <StatTile compact label="Observed churn" value={blank} />
          <StatTile compact label="Monthly charges" value={blank} />
        </>
      )}
      {failed && (
        <div className={styles.summaryError}>
          <ErrorState inline onRetry={onRetry} focusTo={SUMMARY_ID}>
            The selection’s figures could not be loaded.
          </ErrorState>
        </div>
      )}
      {/* A tile of its own: a figure cannot sit inside a stat tile's value (a span). */}
      <div className={styles.spreadTile}>
        <span className={styles.spreadLabel}>Probability spread</span>
        {!idle && selection ? (
          <Spread
            histogram={selection.summary.histogram}
            bands={info?.risk_bands}
            threshold={threshold}
          />
        ) : (
          <span className={styles.spreadEmpty}>{blank}</span>
        )}
      </div>
    </section>
  );
}

function Tiles({
  summary,
  threshold,
  withheld,
}: {
  summary: SelectionSummary;
  threshold: number | undefined;
  withheld: string | null;
}) {
  const observed = observedTile(summary, withheld);
  return (
    <>
      <StatTile
        compact
        label="Customers"
        value={int(summary.customers)}
        sub={summary.share_of_book === null ? '—' : `${pct(summary.share_of_book)} of the book`}
      />
      <StatTile
        compact
        label={flaggedLabel(threshold)}
        value={int(summary.flagged)}
        sub={summary.flagged_share === null ? '—' : `${pct(summary.flagged_share)} of selection`}
      />
      <StatTile
        compact
        label="Mean probability"
        value={summary.mean_probability === null ? '—' : probability(summary.mean_probability)}
        sub={`expected churners ${int(summary.expected_churners)}`}
      />
      <StatTile compact label="Observed churn" value={observed.value} sub={observed.sub} />
      <StatTile
        compact
        label="Monthly charges"
        value={money(summary.monthly_charges)}
        sub={`${money(summary.at_risk)} / mo at risk, customers recorded active only`}
      />
    </>
  );
}

const WIDTH = 200;
const BASE = 40;
const TALLEST = 38;
type RiskBand = NonNullable<ModelInfo['risk_bands']>[number];

/**
 * Customers per bin of probability, drawn in band colours. A bar is split at
 * the served bands' edges, so the picture places the API's ranges on the axis
 * and assigns no customer to a band itself.
 */
function Spread({
  histogram,
  bands,
  threshold,
}: {
  histogram: readonly number[];
  bands: readonly RiskBand[] | null | undefined;
  threshold: number | undefined;
}) {
  const bins = histogram.length;
  const slot = WIDTH / bins;
  const tallest = Math.max(1, ...histogram);
  const x = (p: number) => p * WIDTH;
  return (
    <ChartFrame
      label="Histogram of probability for the selection"
      className={styles.spread}
      table={{
        caption: `Customers per ${cut(1 / bins)} of probability`,
        columns: ['Probability', 'Number of customers'],
        rows: histogram.map((count, i) => [`${cut(i / bins)}–${cut((i + 1) / bins)}`, int(count)]),
      }}
    >
      <svg viewBox={`0 0 ${String(WIDTH)} 44`} className={styles.spreadSvg} aria-hidden="true">
        {histogram.map((count, i) => {
          const left = i * slot + 1;
          const right = (i + 1) * slot - 1;
          const height = (count / tallest) * TALLEST;
          if (height === 0) return null;
          const parts = (bands ?? [])
            .map((band) => ({
              band: band.name,
              from: Math.max(left, x(band.min)),
              to: Math.min(right, x(bandTop(band))),
            }))
            .filter((part) => part.to > part.from);
          const segments = parts.length > 0 ? parts : [{ band: undefined, from: left, to: right }];
          return segments.map((part) => (
            <rect
              key={`${String(i)}-${part.band ?? 'none'}`}
              className={styles.spreadBar}
              data-band={part.band}
              x={part.from}
              y={BASE - height}
              width={part.to - part.from}
              height={height}
            />
          ));
        })}
        {threshold !== undefined && (
          <line
            className={styles.spreadThreshold}
            x1={x(threshold)}
            x2={x(threshold)}
            y1={0}
            y2={BASE}
          />
        )}
        <line className={styles.spreadAxis} x1={0} x2={WIDTH} y1={BASE + 0.5} y2={BASE + 0.5} />
      </svg>
    </ChartFrame>
  );
}
