/**
 * Scenario controls: the assumptions the simulation rests on and the cut, each
 * inside what ``/model-info`` and the sweep serve. The cost and the success
 * rate, with the cut, are always in view; the horizon and the margin sit in
 * the Advanced controls, closed at first, whose summary always names their
 * values and says when one is changed or cannot be sent.
 *
 * Money and months are typed (a decimal comma reads as the point); a rate is
 * a fader over whole percents inside the served bounds; the cut is a fader
 * over the response's own cuts, and nothing else. What is typed or moved
 * settles into the address, and so into a request, as the other pages' boxes
 * do.
 */
import { useId, useState, type ReactNode } from 'react';
import type { SweepPoint } from '../../api/queries';
import { useSettledDraft } from '../../app/useSettledDraft';
import { Button } from '../../components/ui/Button';
import { Disclosure } from '../../components/ui/Disclosure';
import { Fader } from '../../components/ui/Fader';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut } from '../../lib/format';
import { cx } from '../../lib/style';
import {
  advancedSummary,
  boundsNote,
  CUT_CAPTION,
  FIELD_COPY,
  fieldMessage,
  fieldValue,
  readout,
} from './copy';
import { ForAssumptions } from './ForAssumptions';
import { Formula } from './Formula';
import {
  ADVANCED,
  decimalsOf,
  FIELDS,
  percentIndex,
  percentSteps,
  percentText,
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

interface AssumptionProps {
  scenario: Scenario;
  bounds: Bounds;
  defaults: Echo;
  onSettle: (key: FieldKey, text: string) => void;
}

/** The fields ``keys`` names, in the fields' order, each as its unit is set. */
function Fields({
  scenario,
  bounds,
  defaults,
  onSettle,
  keys,
}: AssumptionProps & { keys: readonly FieldKey[] }) {
  return FIELDS.filter((spec) => keys.includes(spec.key)).map((spec) => {
    const props = {
      spec,
      text: scenario.texts[spec.key],
      range: bounds[spec.assumption],
      fallback: defaults[spec.assumption],
      currency: defaults.currency,
      onSettle: (text: string) => onSettle(spec.key, text),
    };
    return spec.unit === 'percent' ? (
      <RateFader key={spec.key} {...props} />
    ) : (
      <AmountField key={spec.key} {...props} />
    );
  });
}

export interface CutControl {
  /** The response's points, or none while there is no response to choose from. */
  points: readonly SweepPoint[];
  churners: number;
  index: number | null;
  recommended: number;
  served: number;
  following: boolean;
  loading: boolean;
  /** Why no cut can be chosen, while none can. */
  unavailable: string | null;
  echo: Echo | null;
  stale: boolean;
  onCut: (index: number) => void;
  onFollow: () => void;
}

interface PrimaryProps extends AssumptionProps {
  control: CutControl;
}

/** Always in view: the cost, the success rate and the cut. */
export function PrimaryControls({ control, ...assumptions }: PrimaryProps) {
  return (
    <Card titleId="controls-title" variant="sim" className={cx(styles.panel, styles.primary)}>
      <CardHeader
        id="controls-title"
        title="Scenario controls"
        subtitle="You set the assumptions; none is measured. The defaults are the served configuration’s."
        aside={<Tag tone="sim">ASSUMED</Tag>}
      />
      <Fields {...assumptions} keys={['cost', 'success']} />
      <CutFader control={control} />
    </Card>
  );
}

/** The horizon, the margin and the formula: closed at first, never out of sight. */
export function AdvancedControls(props: AssumptionProps) {
  const { scenario, defaults } = props;
  const hidden = ADVANCED.map((key) => scenario.readings[key]);
  const problem = hidden.some((reading) => reading.problem !== null);
  const changed = FIELDS.some(
    (spec) =>
      ADVANCED.includes(spec.key) &&
      scenario.readings[spec.key].value !== null &&
      scenario.readings[spec.key].value !== defaults[spec.assumption],
  );
  // A field that cannot be sent is shown, not hidden: opened on arrival.
  const [openAtFirst] = useState(problem);
  return (
    <Card titleId="advanced-title" className={cx(styles.panel, styles.advanced)}>
      <h2 id="advanced-title" className="sr">
        Advanced controls
      </h2>
      <Disclosure
        defaultOpen={openAtFirst}
        className={styles.advancedBox}
        summary={
          <span className={styles.advancedSummary}>
            <span className={styles.advancedName}>Advanced controls</span>
            <span className={styles.advancedValues}>
              {advancedSummary(scenario, defaults.currency)}
            </span>
            {problem ? (
              <span className={styles.needsFix}>needs correcting</span>
            ) : (
              changed && <Tag tone="sim">CHANGED</Tag>
            )}
          </span>
        }
      >
        <Fields {...props} keys={ADVANCED} />
        <Formula />
      </Disclosure>
    </Card>
  );
}

/** The cut: a fader over the response's cuts, its figures, and whether it follows the recommendation. */
function CutFader({ control }: { control: CutControl }) {
  const { points, index, recommended, served, loading, unavailable } = control;
  const point = index === null ? undefined : points[index];
  return (
    <div className={styles.cutBlock}>
      {control.echo && <ForAssumptions echo={control.echo} stale={control.stale} />}
      <Fader
        label="Decision threshold"
        caption={CUT_CAPTION}
        values={points.map((p) => p.threshold)}
        index={index}
        onChange={control.onCut}
        format={cut}
        defaultIndex={recommended >= 0 ? recommended : undefined}
        marks={served >= 0 ? [served] : []}
        tone="cyan"
        loading={loading}
        disabled={!loading && unavailable !== null}
      />
      <p className={styles.readout} aria-live="polite">
        {point && !loading
          ? readout(point, control.churners)
          : (unavailable ?? 'Loading the cuts.')}
      </p>
      {unavailable === null && points.length > 0 && (
        <div className={styles.follow}>
          <span className={styles.followNote}>
            {control.following ? 'Following the recommendation' : 'Set by hand'}
          </span>
          <Button
            variant="simGhost"
            size="sm"
            aria-disabled={control.following || loading}
            onClick={() => {
              if (!control.following && !loading) control.onFollow();
            }}
          >
            Use recommended
          </Button>
        </div>
      )}
    </div>
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

/** The draft, how it reads, and its message. */
function useField({ spec, text, range, currency, onSettle }: FieldProps) {
  const id = useId();
  const [draft, setDraft] = useSettledDraft(text, onSettle);
  // Read as typed, so a problem shows at once, before the draft settles.
  const reading = readField(draft, range, decimalsOf(spec, currency));
  return {
    id,
    draft,
    setDraft,
    reading,
    message: reading.problem && fieldMessage(spec, reading.problem, currency),
    shown: reading.value === null ? '—' : fieldValue(spec, reading.value, currency),
  };
}

function Problem({ id, message }: { id: string; message: string | null }) {
  return message ? (
    <p id={id} className={styles.problem}>
      {message}
    </p>
  ) : null;
}

/** Money or months, typed: only what a number is written with is kept. */
function AmountField(props: FieldProps) {
  const field = useField(props);
  const note = `${field.id}-bounds`;
  const error = `${field.id}-problem`;
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
          aria-describedby={field.message ? `${error} ${note}` : note}
          onChange={(event) => field.setDraft(typedNumber(event.target.value))}
        />
        {unit && (
          <span className={styles.unit} aria-hidden="true">
            {unit}
          </span>
        )}
      </span>
      <p id={note} className={styles.bounds}>
        {boundsNote(props.spec, props.range, props.fallback, props.currency)}
      </p>
      <Problem id={error} message={field.message} />
    </div>
  );
}

/** A rate in whole percents, inside the served bounds: a fader that offers nothing else. */
function RateFader(props: FieldProps) {
  const field = useField(props);
  const steps = percentSteps(props.range);
  const index = percentIndex(steps, field.draft);
  const fallback = percentIndex(steps, String(props.fallback));
  const caption: ReactNode = (
    <>
      {props.spec.key === 'success' && (
        <span className={styles.caveat}>
          Cannot be measured here: no campaign outcomes are recorded.{' '}
        </span>
      )}
      <span className={styles.boundsInline}>
        {boundsNote(props.spec, props.range, props.fallback, props.currency)}
      </span>
      {field.message && <span className={styles.problemInline}> {field.message}</span>}
    </>
  );
  return (
    <div className={cx(styles.faderField, field.reading.problem && styles.invalidFader)}>
      <Fader
        label={FIELD_COPY[props.spec.key].label}
        caption={caption}
        values={steps}
        index={index}
        onChange={(i) => {
          const step = steps[i];
          if (step !== undefined) field.setDraft(percentText(step));
        }}
        format={(step) => `${String(step)}%`}
        unit="%"
        defaultIndex={fallback ?? undefined}
        tone="amber"
        invalid={field.reading.problem !== null}
      />
    </div>
  );
}
