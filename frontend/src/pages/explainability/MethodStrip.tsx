/** How the cached attributions were measured, in the served figures. */
import type { GlobalExplanation } from '../../api/queries';
import { StatTile } from '../../components/ui/Surface';
import { int, pct } from '../../lib/format';
import { additivity, explainedSub, METHOD_NOTES } from './copy';
import styles from './Explain.module.css';

export function MethodStrip({ global }: { global: GlobalExplanation }) {
  return (
    <section aria-label="Method" className={styles.method}>
      <StatTile compact label="Base value" value={pct(global.base_value)} sub={METHOD_NOTES.base} />
      <StatTile
        compact
        label="Explained customers"
        value={int(global.n_explained)}
        sub={explainedSub(global)}
      />
      <StatTile
        compact
        label="Background"
        value={int(global.n_background)}
        sub={METHOD_NOTES.background}
      />
      <StatTile
        compact
        label="Orderings per customer"
        value={int(global.permutations)}
        sub={METHOD_NOTES.orderings}
      />
      <StatTile
        compact
        label="Additivity"
        value={additivity(global.max_additivity_gap)}
        sub={METHOD_NOTES.additivity}
      />
    </section>
  );
}
