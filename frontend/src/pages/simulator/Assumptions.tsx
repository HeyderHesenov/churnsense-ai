/**
 * "Your assumptions": the four inputs the simulation rests on, each with the
 * bounds and default ``/model-info`` serves. Money and months are typed; the
 * two rates are sliders in whole percents. What is typed settles into the
 * address (and so into a request) as the other pages' boxes do.
 */
import { useId } from 'react';
import { useSettledDraft } from '../../app/useSettledDraft';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cx } from '../../lib/style';
import { boundsNote, FIELD_COPY, fieldMessage, fieldValue } from './copy';
import {
  decimalsOf,
  FIELDS,
  readField,
  typedNumber,
  type Bounds,
  type Echo,
  type FieldKey,
  type FieldSpec,
  type Range,
  type Scenario,
} from './simulator';
import styles from './Simulator.module.css';

interface AssumptionsProps {
  scenario: Scenario;
  bounds: Bounds;
  defaults: Echo;
  onSettle: (key: FieldKey, text: string) => void;
}

export function Assumptions({ scenario, bounds, defaults, onSettle }: AssumptionsProps) {
  return (
    <Card titleId="assumptions" variant="sim" className={styles.panel}>
      <CardHeader
        id="assumptions"
        title="Your assumptions"
        subtitle="You set these; none is measured. The defaults are the served configuration’s."
        aside={<Tag tone="sim">ASSUMED</Tag>}
      />
      {FIELDS.map((spec) => {
        const props = {
          spec,
          text: scenario.texts[spec.key],
          range: bounds[spec.assumption],
          fallback: defaults[spec.assumption],
          currency: defaults.currency,
          onSettle: (text: string) => onSettle(spec.key, text),
        };
        return spec.unit === 'percent' ? (
          <RateField key={spec.key} {...props} />
        ) : (
          <AmountField key={spec.key} {...props} />
        );
      })}
      <div className={styles.formula}>
        <p className={styles.formulaTitle}>How the API works the money out</p>
        <p>
          <span>value</span> = monthly charges × horizon × margin
        </p>
        <p>
          <span>retained</span> = Σ churners reached × value × success
        </p>
        <p>
          <span>cost</span> = flagged × offer
        </p>
        <p className={styles.formulaNet}>
          <span>net</span> = retained − cost
        </p>
      </div>
    </Card>
  );
}

interface FieldProps {
  spec: FieldSpec;
  /** What the address holds, or the default's text. */
  text: string;
  range: Range;
  fallback: number;
  currency: string;
  onSettle: (text: string) => void;
}

/** The ids a field's control points at: the bounds always, the problem while there is one. */
function useField({ spec, text, range, currency, onSettle }: FieldProps) {
  const id = useId();
  const [draft, setDraft] = useSettledDraft(text, onSettle);
  // Read as typed, so a problem shows at once, before the draft settles.
  const reading = readField(draft, range, decimalsOf(spec, currency));
  const note = `${id}-bounds`;
  const error = `${id}-problem`;
  return {
    id,
    draft,
    setDraft,
    reading,
    note,
    error,
    describedBy: reading.problem ? `${error} ${note}` : note,
    message: reading.problem && fieldMessage(spec, reading.problem, currency),
    shown: reading.value === null ? '—' : fieldValue(spec, reading.value, currency),
  };
}

function FieldFoot(props: FieldProps & { note: string; error: string; message: string | null }) {
  const { spec, range, fallback, currency, note, error, message } = props;
  return (
    <>
      <p id={note} className={styles.bounds}>
        {boundsNote(spec, range, fallback, currency)}
      </p>
      {message && (
        <p id={error} className={styles.problem}>
          {message}
        </p>
      )}
    </>
  );
}

/** Money or months, typed: only what a number is written with is kept. */
function AmountField(props: FieldProps) {
  const field = useField(props);
  const unit = props.spec.unit === 'months' ? 'months' : null;
  return (
    <div className={styles.field}>
      <div className={styles.fieldTop}>
        <label htmlFor={field.id} className={styles.label}>
          {FIELD_COPY[props.spec.key].label}
        </label>
        <span className={styles.fieldValue} aria-hidden="true">
          {field.shown}
        </span>
      </div>
      <span className={styles.inputRow}>
        <input
          id={field.id}
          type="text"
          inputMode={props.range.integer ? 'numeric' : 'decimal'}
          className={cx(styles.input, field.reading.problem && styles.invalid)}
          value={field.draft}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={field.reading.problem !== null}
          aria-describedby={field.describedBy}
          onChange={(event) => field.setDraft(typedNumber(event.target.value))}
        />
        {unit && (
          <span className={styles.unit} aria-hidden="true">
            {unit}
          </span>
        )}
      </span>
      <FieldFoot {...props} note={field.note} error={field.error} message={field.message} />
    </div>
  );
}

/** A rate in whole percents, inside the served bounds. */
function RateField(props: FieldProps) {
  const field = useField(props);
  const low = Math.ceil(props.range.min * 100 - 1e-9);
  const high = Math.floor(props.range.max * 100 + 1e-9);
  // A rate the slider cannot show (a refused address) sits at the nearest end,
  // while the field says why it cannot be sent.
  const typed = Math.round(Number(field.draft) * 100);
  const position = Number.isFinite(typed) ? Math.min(high, Math.max(low, typed)) : low;
  return (
    <div className={styles.field}>
      <div className={styles.fieldTop}>
        <label htmlFor={field.id} className={styles.label}>
          {FIELD_COPY[props.spec.key].label}
        </label>
        <span className={styles.fieldValue} aria-hidden="true">
          {field.shown}
        </span>
      </div>
      <input
        id={field.id}
        type="range"
        className={styles.slider}
        min={low}
        max={high}
        step={1}
        value={position}
        aria-valuetext={field.reading.value === null ? 'not set' : field.shown}
        aria-invalid={field.reading.problem !== null}
        aria-describedby={field.describedBy}
        onChange={(event) => field.setDraft(String(Number(event.target.value) / 100))}
      />
      {props.spec.key === 'success' && (
        <p className={styles.caveat}>Cannot be measured here: no campaign outcomes are recorded.</p>
      )}
      <FieldFoot {...props} note={field.note} error={field.error} message={field.message} />
    </div>
  );
}
