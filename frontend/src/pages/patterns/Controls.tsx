/** What to break churn down by: rows, columns, the measure shown, and four suggested views. */
import { icons } from '../../components/icons';
import { IconButton } from '../../components/ui/Button';
import { Select } from '../../components/ui/Field';
import { Segmented, ToggleChip } from '../../components/ui/Toggle';
import { DIMENSIONS } from '../../lib/labels';
import { MEASURE_OPTIONS } from './copy';
import { NONE, SUGGESTED, type Measure, type View } from './patterns';
import styles from './Patterns.module.css';

const ROW_OPTIONS = DIMENSIONS.map(([value, label]) => ({ value, label }));

interface Props {
  view: View;
  measure: Measure;
  onView: (view: View) => void;
  onMeasure: (measure: Measure) => void;
}

export function Controls({ view, measure, onView, onMeasure }: Props) {
  // A dimension is never crossed with itself: the columns offer every other one.
  const columnOptions = [
    { value: NONE, label: 'None (one attribute)' },
    ...ROW_OPTIONS.filter((o) => o.value !== view.rows),
  ];
  return (
    <section className={styles.controls} aria-label="Breakdown controls">
      <div className={styles.pickers}>
        <Select
          label="Rows"
          value={view.rows}
          options={ROW_OPTIONS}
          onChange={(rows) => onView({ rows, cols: rows === view.cols ? null : view.cols })}
        />
        <IconButton
          label="Swap rows and columns"
          size="md"
          disabled={view.cols === null}
          onClick={() => view.cols !== null && onView({ rows: view.cols, cols: view.rows })}
        >
          <icons.swap size={14} />
        </IconButton>
        <Select
          label="Columns"
          value={view.cols ?? NONE}
          options={columnOptions}
          onChange={(cols) => onView({ rows: view.rows, cols: cols === NONE ? null : cols })}
        />
        <Segmented
          label="Show"
          options={MEASURE_OPTIONS}
          value={measure}
          onChange={onMeasure}
          small
        />
      </div>
      <div role="group" aria-label="Suggested views" className={styles.suggested}>
        {SUGGESTED.map((s) => (
          <ToggleChip
            key={s.label}
            variant="preset"
            pressed={s.rows === view.rows && s.cols === view.cols}
            onToggle={() => onView({ rows: s.rows, cols: s.cols })}
          >
            {s.label}
          </ToggleChip>
        ))}
      </div>
    </section>
  );
}
