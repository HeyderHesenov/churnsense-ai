/**
 * Two rankings, both published: each attribute's association with churn on
 * its own (training outcomes) against its rank inside the model (mean
 * |contribution| on the cached sample). A line only for an attribute ranked
 * both ways; neither ranking is bent to fit the other.
 */
import type { GlobalExplanation } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { Card, CardHeader } from '../../components/ui/Surface';
import { cx } from '../../lib/style';
import { ASSOCIATION_METHOD, CARDS, featureName } from './copy';
import { slopeLines } from './explain';
import styles from './Explain.module.css';

const S = { width: 560, left: 150, right: 410, top: 26, step: 15.4, bottom: 16 };

function rankY(rank: number): number {
  return S.top + (rank - 1) * S.step;
}

function curve(a: number, m: number): string {
  const ya = rankY(a).toFixed(1);
  const ym = rankY(m).toFixed(1);
  return `M${S.left} ${ya}C${S.left + 130} ${ya} ${S.right - 120} ${ym} ${S.right} ${ym}`;
}

interface Props {
  global: GlobalExplanation;
  feature: string | null;
  onPick: (feature: string) => void;
}

export function Rankings({ global, feature, onPick }: Props) {
  const lines = slopeLines(global);
  const name = (f: string) => global.labels[f] ?? featureName(f);
  const header = (
    <CardHeader id="rankings-title" title={CARDS.rankings.title} subtitle={ASSOCIATION_METHOD} />
  );
  if (lines.length === 0) {
    return (
      <Card titleId="rankings-title" className={styles.card}>
        {header}
        <p className={styles.note}>{CARDS.rankings.unavailable}</p>
      </Card>
    );
  }

  const deepest = Math.max(...lines.map((l) => Math.max(l.association, l.model)));
  const height = rankY(deepest) + S.bottom;
  const selected = lines.find((l) => l.feature === feature);

  return (
    <Card titleId="rankings-title" className={styles.card}>
      {header}
      <div className={styles.rankings}>
        <ChartFrame
          className={styles.slopeFrame}
          label={`Rank on its own against rank in the model, for ${lines.length} attributes ranked both ways`}
          table={{
            caption: 'Ranks on their own and in the model',
            columns: ['Attribute', 'On its own', 'In the model'],
            rows: lines.map((l) => [name(l.feature), `#${l.association}`, `#${l.model}`]),
          }}
        >
          <svg viewBox={`0 0 ${S.width} ${height}`}>
            <text className={styles.slopeHead} x={S.left} y={12} textAnchor="end">
              ON ITS OWN
            </text>
            <text className={styles.slopeHead} x={S.right} y={12}>
              IN THE MODEL
            </text>
            {lines
              .filter((l) => l !== selected)
              .map((l) => (
                <path key={l.feature} className={styles.slope} d={curve(l.association, l.model)} />
              ))}
            {selected && (
              <>
                <path className={styles.slopeOn} d={curve(selected.association, selected.model)} />
                <text
                  className={styles.slopeLabel}
                  x={S.left - 8}
                  y={rankY(selected.association) + 4}
                  textAnchor="end"
                >
                  {name(selected.feature)} #{selected.association}
                </text>
                <text className={styles.slopeLabel} x={S.right + 8} y={rankY(selected.model) + 4}>
                  #{selected.model} {name(selected.feature)}
                </text>
              </>
            )}
          </svg>
        </ChartFrame>
        <div className={styles.chips}>
          <p className={styles.note}>{CARDS.rankings.hint}</p>
          <ul className={styles.chipList}>
            {lines.map((l) => {
              const on = l.feature === feature;
              return (
                <li key={l.feature}>
                  <button
                    type="button"
                    className={cx(styles.chip, on && styles.chipOn)}
                    aria-pressed={on}
                    onClick={() => onPick(l.feature)}
                  >
                    {name(l.feature)}
                    <span className={styles.chipRanks}>
                      #{l.association} → #{l.model}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </Card>
  );
}
