/**
 * The view's highest observed churn rates, each with its 95% interval and its
 * size, against the partition's overall rate. The axis starts at zero, so a
 * small difference looks small. Each row pins its group in the table.
 */
import type { Cell, Segments } from '../../api/queries';
import { Card, CardHeader } from '../../components/ui/Surface';
import { int, pct } from '../../lib/format';
import { cx } from '../../lib/style';
import { cellName, interval, RANKED, rankedSubtitle } from './copy';
import { sameGroup, type View } from './patterns';
import styles from './Patterns.module.css';

interface Props {
  view: View;
  data: Segments;
  pinned: Cell | null;
  stale: boolean;
  onToggle: (cell: Cell) => void;
  /** Escape lets go of the pin, as in the table; none while nothing is pinned. */
  onUnpin: (() => void) | undefined;
}

export function Ranked({ view, data, pinned, stale, onToggle, onUnpin }: Props) {
  const groups = data.columns === null ? data.row_totals : data.cells;
  const reported = groups.filter((c) => !c.suppressed).length;
  // From zero to the highest interval end (or the overall rate), rounded up to a tenth.
  const reach = Math.max(data.base_rate ?? 0, ...data.ranked.map((c) => c.rate_high));
  const top = Math.min(1, Math.max(0.1, Math.ceil(reach * 10) / 10));
  const at = (value: number) => value / top;
  return (
    <Card titleId="ranked-title" className={styles.card}>
      <CardHeader
        id="ranked-title"
        level={3}
        title={RANKED.title}
        subtitle={rankedSubtitle(reported, data.min_customers)}
      />
      {data.ranked.length === 0 ? (
        <p className={styles.note}>{RANKED.empty}</p>
      ) : (
        <div className={cx(stale && styles.stale)} inert={stale}>
          <ol className={styles.ranked}>
            {data.ranked.map((cell) => {
              const on = pinned !== null && sameGroup(pinned, cell);
              return (
                <li key={`${cell.row}|${cell.column ?? ''}`}>
                  <button
                    type="button"
                    className={cx(styles.rankRow, on && styles.rankOn)}
                    aria-pressed={on}
                    onClick={() => onToggle(cell)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape' && onUnpin) onUnpin();
                    }}
                  >
                    <span className={styles.rankName}>{cellName(view, cell)}</span>
                    <span className={styles.rankFigures}>
                      <b>{pct(cell.rate)}</b> {interval(cell)} · n {int(cell.n)}
                    </span>
                    {/* No viewBox: places are shares of the width, the dot stays round. */}
                    <svg className={styles.track} aria-hidden="true">
                      <rect className={styles.trackGround} x="0" y="4" width="100%" height="2" />
                      <rect
                        className={styles.whisker}
                        x={share(at(cell.rate_low))}
                        y="4"
                        width={share(at(cell.rate_high) - at(cell.rate_low))}
                        height="2"
                      />
                      {data.base_rate !== null && (
                        <line
                          className={styles.baseLine}
                          x1={share(at(data.base_rate))}
                          x2={share(at(data.base_rate))}
                          y1="0"
                          y2="10"
                        />
                      )}
                      <circle className={styles.dot} cx={share(at(cell.rate))} cy="5" r="4" />
                    </svg>
                  </button>
                </li>
              );
            })}
          </ol>
          <p className={styles.axis} aria-hidden="true">
            <span>0%</span>
            <span>{RANKED.axis}</span>
            <span>{pct(top, 0)}</span>
          </p>
        </div>
      )}
    </Card>
  );
}

/** A place on the track as a share of its width (0 to 1 as "0%" to "100%"). */
function share(at: number): string {
  return `${String(at * 100)}%`;
}
