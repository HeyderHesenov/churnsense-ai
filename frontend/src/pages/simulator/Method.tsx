/**
 * Method: how the figures are made, in the API's and the model card's own
 * terms. The formula as the API documents it, who is measured (the
 * response's counts), how the served cut was set (``/model-info``'s
 * ``threshold_source``), the bounds and defaults it serves, and the two
 * readings of the recommendation.
 */
import type { ModelInfo, Sweep } from '../../api/queries';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut } from '../../lib/format';
import { cx } from '../../lib/style';
import { Coded } from '../performance/Provenance';
import { FIELD_COPY, FORMULA_NOTE, fieldValue, provenance, rangeText } from './copy';
import { Formula } from './Formula';
import { FIELDS, type Bounds, type Echo } from './simulator';
import styles from './Simulator.module.css';

interface MethodProps {
  model: ModelInfo;
  bounds: Bounds;
  defaults: Echo;
  /** The response on screen, when there is one. */
  sweep: Sweep | undefined;
}

export function HowMade({ model, bounds, defaults, sweep }: MethodProps) {
  const currency = defaults.currency;
  return (
    <div className={styles.methodGrid}>
      <Card titleId="made-title" className={styles.panel}>
        <CardHeader
          id="made-title"
          title="How the figures are made"
          subtitle={FORMULA_NOTE}
          aside={<Tag tone="sim">ASSUMED</Tag>}
        />
        <Formula />
        {sweep && <p className={styles.methodText}>{provenance(sweep)}</p>}
      </Card>
      <Card titleId="served-cut-title" className={styles.panel}>
        <CardHeader id="served-cut-title" title="The served cut-off" />
        <p className={styles.methodCut}>{cut(model.threshold)}</p>
        <p className={styles.methodText}>
          <Coded text={model.threshold_source} />
        </p>
      </Card>
      <Card titleId="bounds-title" className={cx(styles.panel, styles.methodWide)}>
        <CardHeader
          id="bounds-title"
          title="What the API accepts"
          subtitle="The bounds and defaults the served configuration names; a value outside them is refused before it is sent."
        />
        {/* A table that scrolls sideways must take the keyboard's focus, or its hidden
            columns are out of reach (axe scrollable-region-focusable): nothing inside is focusable. */}
        {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex */}
        <div role="region" aria-label="Bounds table" tabIndex={0} className={styles.tableWrap}>
          <table className={styles.boundsTable}>
            <caption className="sr">Assumption bounds and defaults</caption>
            <thead>
              <tr>
                <th scope="col">Assumption</th>
                <th scope="col">Accepted</th>
                <th scope="col">Default</th>
              </tr>
            </thead>
            <tbody>
              {FIELDS.map((spec) => (
                <tr key={spec.key}>
                  <th scope="row">{FIELD_COPY[spec.key].label}</th>
                  <td>{rangeText(spec, bounds[spec.assumption], currency)}</td>
                  <td>{fieldValue(spec, defaults[spec.assumption], currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
