/**
 * ``make eda``'s ranking of every attribute's association with churn on the
 * training partition, as served. An attribute the page can break churn down by
 * opens that breakdown; the others are listed for completeness only.
 */
import type { Segments } from '../../api/queries';
import { Card, CardHeader } from '../../components/ui/Surface';
import { FEATURE, isFeature } from '../../lib/labels';
import { cssVars, cx } from '../../lib/style';
import { ASSOCIATIONS, measureSymbol } from './copy';
import { associationRows, type View } from './patterns';
import styles from './Patterns.module.css';

interface Props {
  data: Segments;
  view: View;
  onView: (view: View) => void;
}

export function Associations({ data, view, onView }: Props) {
  const rows = associationRows(data);
  const top = Math.max(0.5, Math.ceil(Math.max(0, ...rows.map((r) => r.strength)) * 10) / 10);
  return (
    <Card titleId="associations-title" className={styles.card}>
      <CardHeader
        id="associations-title"
        level={3}
        title={ASSOCIATIONS.title}
        subtitle={ASSOCIATIONS.subtitle}
      />
      {rows.length === 0 ? (
        <p className={styles.note}>{ASSOCIATIONS.unavailable}</p>
      ) : (
        <ul className={styles.associations}>
          {rows.map((row) => {
            const name = isFeature(row.feature) ? FEATURE[row.feature] : row.feature;
            const figure = `${measureSymbol(row.measure)} ${row.strength.toFixed(3)}`;
            const bar = (
              <span
                className={styles.strength}
                aria-hidden="true"
                style={cssVars({ '--w': row.strength / top })}
              />
            );
            if (row.dimension === null) {
              return (
                <li key={row.feature} className={styles.association}>
                  <span className={styles.associationName}>
                    {name}
                    <span className={styles.noBreakdown}> · {ASSOCIATIONS.noBreakdown}</span>
                  </span>
                  <span className={styles.associationFigure}>{figure}</span>
                  {bar}
                </li>
              );
            }
            const dimension = row.dimension;
            const on = view.rows === dimension && view.cols === null;
            return (
              <li key={row.feature}>
                <button
                  type="button"
                  className={cx(styles.association, styles.associationButton, on && styles.rankOn)}
                  aria-pressed={on}
                  onClick={() => onView({ rows: dimension, cols: null })}
                >
                  <span className={styles.associationName}>{name}</span>
                  <span className={styles.associationFigure}>{figure}</span>
                  {bar}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
