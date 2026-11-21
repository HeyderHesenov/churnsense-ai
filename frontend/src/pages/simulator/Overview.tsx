/**
 * Scenario overview: what is set away from the served configuration, in the
 * words of the fields and the served defaults (no figure is worked out), and
 * the cut when it is set by hand. The charts follow it.
 */
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut } from '../../lib/format';
import { AT_DEFAULTS, changeLine } from './copy';
import { changesOf, type Echo, type Scenario } from './simulator';
import styles from './Simulator.module.css';

interface OverviewProps {
  scenario: Scenario;
  defaults: Echo;
  /** The cut set by hand, as the address holds it; empty while following the recommendation. */
  handCut: string;
  /** The recommendation of the response on screen, when there is one. */
  recommended: number | null;
}

export function Overview({ scenario, defaults, handCut, recommended }: OverviewProps) {
  const changes = changesOf(scenario, defaults);
  const none = changes.length === 0 && handCut === '';
  return (
    <Card titleId="overview-title" className={styles.panel}>
      <CardHeader
        id="overview-title"
        title="Scenario overview"
        subtitle="What is set away from the served configuration."
        aside={<Tag tone="sim">ASSUMED</Tag>}
      />
      {none ? (
        <p className={styles.atDefaults}>{AT_DEFAULTS}</p>
      ) : (
        <ul className={styles.changes} aria-label="Changed from the served defaults">
          {changes.map((change) => (
            <li
              key={change.spec.key}
              className={styles.change}
              data-problem={change.value === null || undefined}
            >
              {changeLine(change, defaults.currency)}
            </li>
          ))}
          {handCut !== '' && (
            <li className={styles.change}>
              Decision threshold {handCut}, set by hand
              {recommended !== null ? ` (recommended ${cut(recommended)})` : ''}
            </li>
          )}
        </ul>
      )}
    </Card>
  );
}
