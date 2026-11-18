/**
 * The precision fader: a choice among values the service publishes, such as
 * the cuts of a threshold sweep.
 *
 * It never makes a value of its own. The native range under the visuals moves
 * over the *positions* of ``values`` (0 to n - 1, step 1), as the simulator's
 * and the performance page's cut sliders always have, so whatever the reader
 * does - drag, arrow keys, the stepper, typing - the result is one of
 * ``values``, index for index. A typed number is taken only when it is one of
 * them; anything between two of them is refused with the two nearest, never
 * rounded to one.
 *
 * Keys on the range: an arrow steps one value, Shift+arrow (and Page Up or
 * Down) ten, Home and End jump to the first and last; with Alt, Ctrl or Cmd
 * they are left to the browser. With no value chosen yet, End chooses the last
 * value, any other of these keys or either stepper button the first, and a
 * typed value itself. The stepper's "-" and "+"
 * step one value; its field takes "0,36" as well as "0.36" (Enter or leaving
 * it applies, Escape puts the shown value back; a value set from outside
 * drops what was typed). The reset chip, shown only
 * away from the default, goes back to it.
 *
 * ``disabled``: nothing to choose (native disabled). ``loading``: the choice is
 * on its way; the controls keep their place in the tab order and focus, say
 * they are unavailable, and do nothing until it arrives.
 */
import { useId, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './Fader.module.css';

interface FaderProps {
  label: string;
  /** A line under the label: what the value decides. */
  caption?: ReactNode;
  /** The values on offer, ascending, exactly as the service sent them. */
  values: readonly number[];
  /** The chosen value's position in ``values``; ``null`` when none is chosen. */
  index: number | null;
  onChange: (index: number) => void;
  /** A value as shown and as read out, e.g. ``cut`` (0.36). */
  format: (value: number) => string;
  /** The value the reset chip goes back to, and a mark on the rail. */
  defaultIndex?: number | undefined;
  /** Positions given a major tick and a label under the rail. */
  marks?: readonly number[];
  /** cyan: a cut; amber: an assumption; neutral: anything else. */
  tone?: 'cyan' | 'amber' | 'neutral';
  disabled?: boolean;
  loading?: boolean;
}

/** One value on the scale is "the same" as a typed number within this. */
const SAME = 1e-9;
/** Shift+arrow and Page Up/Down move this many values. */
const BIG_STEP = 10;
/** Every value gets a minor tick when there are no more than this many. */
const MAX_MINOR_TICKS = 30;

/** The typed text as a number, a decimal comma read as a point; ``null`` if it is not one. */
export function typedValue(text: string): number | null {
  const trimmed = text.trim();
  const commas = trimmed.split(',').length - 1;
  if (commas > 1 || (commas === 1 && trimmed.includes('.'))) return null;
  const plain = trimmed.replace(',', '.');
  if (!/^-?[\d.]+$/.test(plain)) return null;
  const number = Number(plain);
  return Number.isFinite(number) ? number : null;
}

/** The position of the value equal to ``typed``, or ``null``: never the nearest. */
export function positionOf(values: readonly number[], typed: number): number | null {
  const index = values.findIndex((value) => Math.abs(value - typed) <= SAME);
  return index === -1 ? null : index;
}

/** Why a typed number was refused: the values on either side of it, or the scale's ends. */
function refusal(values: readonly number[], text: string, format: (v: number) => string): string {
  const typed = typedValue(text);
  const first = values[0];
  const last = values[values.length - 1];
  const ends =
    first === undefined || last === undefined
      ? ''
      : ` The scale runs from ${format(first)} to ${format(last)}.`;
  if (typed === null) return `“${text.trim()}” is not a number.${ends}`;
  const below = values.filter((value) => value < typed).at(-1);
  const above = values.find((value) => value > typed);
  if (below === undefined || above === undefined) {
    return `${text.trim()} is outside the scale.${ends}`;
  }
  return `${text.trim()} is not one of the values on this scale. The nearest are ${format(below)} and ${format(above)}.`;
}

/** The same scale, entry for entry: a page that rebuilds its array changes nothing. */
function sameValues(a: readonly number[], b: readonly number[]): boolean {
  return a === b || (a.length === b.length && a.every((value, i) => value === b[i]));
}

/** A position along the rail, as an SVG length: the thumb's centre at that value. */
function at(index: number, count: number): string {
  return count > 1 ? `${(index / (count - 1)) * 100}%` : '0%';
}

export function Fader({
  label,
  caption,
  values,
  index,
  onChange,
  format,
  defaultIndex,
  marks = [],
  tone = 'cyan',
  disabled = false,
  loading = false,
}: FaderProps) {
  const id = useId();
  const rangeId = `${id}-range`;
  const labelId = `${id}-label`;
  const captionId = `${id}-caption`;
  const errorId = `${id}-error`;
  // An SVG reference must be a plain name: useId's own may hold characters a url() cannot.
  const gradientId = `fader${id.replace(/[^a-zA-Z0-9]/g, '')}`;
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const count = values.length;
  const last = count - 1;
  const current = index !== null && index >= 0 && index <= last ? index : null;
  const value = current === null ? undefined : values[current];
  const shown = value === undefined ? '—' : format(value);
  const off = disabled || count === 0;
  const waiting = loading && !off;
  const idle = off || waiting;
  const defaultValue = defaultIndex === undefined ? undefined : values[defaultIndex];

  // A value set from outside (a preset, Back, a fresh sweep) or a load starting
  // drops what was typed and refused: the field shows what the fader holds.
  const [held, setHeld] = useState({ current, values, waiting });
  if (held.current !== current || !sameValues(held.values, values) || held.waiting !== waiting) {
    setHeld({ current, values, waiting });
    setDraft(null);
    setError(null);
  }

  const go = (next: number) => {
    if (idle) return;
    const clamped = Math.min(last, Math.max(0, next));
    setDraft(null);
    setError(null);
    if (clamped !== current) onChange(clamped);
  };

  const onRangeKey = (event: KeyboardEvent<HTMLInputElement>) => {
    // Alt, Ctrl and Cmd with an arrow are the browser's (Back, Forward, line ends).
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const here = current ?? 0;
    const big = event.shiftKey ? BIG_STEP : 1;
    const moves: Record<string, number> = {
      ArrowRight: here + big,
      ArrowUp: here + big,
      ArrowLeft: here - big,
      ArrowDown: here - big,
      PageUp: here + BIG_STEP,
      PageDown: here - BIG_STEP,
      Home: 0,
      End: last,
    };
    const move = moves[event.key];
    if (move === undefined) return;
    // From no value, End chooses the last and every other key the first.
    const next = current !== null ? move : event.key === 'End' ? last : 0;
    // Every key is handled here, the same in every browser; the native step never
    // runs, so an idle fader does not move under the key either.
    event.preventDefault();
    go(next);
  };

  const commit = () => {
    if (draft === null || idle) return;
    const typed = typedValue(draft);
    const position = typed === null ? null : positionOf(values, typed);
    if (position === null) {
      setError(refusal(values, draft, format));
      return;
    }
    go(position);
    setDraft(null);
    setError(null);
  };

  const onFieldKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commit();
    } else if (event.key === 'Escape' && (draft !== null || error !== null)) {
      event.preventDefault();
      setDraft(null);
      setError(null);
    }
  };

  const reset = () => {
    if (defaultIndex === undefined || idle) return;
    go(defaultIndex);
    // The chip leaves with the change: the focus stays on the fader.
    document.getElementById(rangeId)?.focus();
  };

  const majors = marks.filter((mark) => mark >= 0 && mark <= last);
  const minors = count <= MAX_MINOR_TICKS ? values.map((_, i) => i) : [];
  const showReset = defaultValue !== undefined && current !== defaultIndex && !off;
  const describedBy = caption ? captionId : undefined;
  const fieldDescribedBy = [error && errorId, describedBy].filter(Boolean).join(' ') || undefined;

  return (
    <div
      aria-busy={waiting || undefined}
      className={cx(styles.fader, off && styles.off)}
      data-tone={tone}
    >
      <div className={styles.head}>
        <div className={styles.text}>
          <label id={labelId} htmlFor={rangeId} className={styles.label}>
            {label}
          </label>
          {caption && (
            <p id={captionId} className={styles.caption}>
              {caption}
            </p>
          )}
        </div>
        <div className={styles.controls}>
          {showReset && (
            <button
              type="button"
              className={styles.reset}
              aria-disabled={waiting || undefined}
              onClick={reset}
            >
              <span aria-hidden="true">↺</span>
              <span className="sr">Reset {label} to</span> {format(defaultValue)}
            </button>
          )}
          <div className={styles.stepper}>
            <button
              type="button"
              className={styles.step}
              aria-label={`Lower ${label}`}
              disabled={off}
              aria-disabled={waiting || current === 0 || undefined}
              // From no value, either button chooses the first.
              onClick={() => go(current === null ? 0 : current - 1)}
            >
              −
            </button>
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              spellCheck={false}
              className={styles.field}
              aria-label={`${label} value`}
              aria-describedby={fieldDescribedBy}
              aria-invalid={error !== null}
              aria-disabled={waiting || undefined}
              readOnly={waiting}
              disabled={off}
              value={draft ?? (value === undefined ? '' : format(value))}
              placeholder={value === undefined ? '—' : undefined}
              onChange={(event) => {
                setDraft(event.target.value);
                setError(null);
              }}
              onKeyDown={onFieldKey}
              onBlur={commit}
            />
            <button
              type="button"
              className={styles.step}
              aria-label={`Raise ${label}`}
              disabled={off}
              aria-disabled={waiting || current === last || undefined}
              onClick={() => go(current === null ? 0 : current + 1)}
            >
              +
            </button>
          </div>
        </div>
      </div>
      <div className={cx(styles.track, majors.length > 0 && styles.marked)}>
        <svg className={styles.scale} aria-hidden="true" focusable="false">
          <defs>
            <linearGradient id={gradientId}>
              <stop offset="0" className={styles.from} />
              <stop offset="1" className={styles.to} />
            </linearGradient>
          </defs>
          {minors.map((i) => (
            <line
              key={i}
              x1={at(i, count)}
              x2={at(i, count)}
              y1="22"
              y2="26"
              className={styles.minor}
            />
          ))}
          {majors.map((i) => (
            <line
              key={i}
              x1={at(i, count)}
              x2={at(i, count)}
              y1="22"
              y2="29"
              className={styles.major}
            />
          ))}
          <rect x="0" y="15" width="100%" height="4" rx="2" className={styles.rail} />
          {current !== null && (
            <rect
              x="0"
              y="15"
              width={at(current, count)}
              height="4"
              rx="2"
              fill={`url(#${gradientId})`}
              className={styles.fill}
            />
          )}
          {defaultIndex !== undefined && defaultValue !== undefined && (
            <line
              x1={at(defaultIndex, count)}
              x2={at(defaultIndex, count)}
              y1="5"
              y2="12"
              className={styles.default}
            />
          )}
          {majors.map((i) => {
            const mark = values[i];
            if (mark === undefined) return null;
            const anchor = i === 0 ? 'start' : i === last ? 'end' : 'middle';
            return (
              <text key={i} x={at(i, count)} y="40" textAnchor={anchor} className={styles.markText}>
                {format(mark)}
              </text>
            );
          })}
          {current !== null && value !== undefined && (
            <svg x={at(current, count)} y="0" overflow="visible" className={styles.bubble}>
              <rect
                x={-(shown.length * 3.3 + 6)}
                y="-16"
                width={shown.length * 6.6 + 12}
                height="15"
                rx="4"
              />
              <path d="M -4 -1 L 0 3 L 4 -1 Z" />
              <text x="0" y="-5" textAnchor="middle">
                {shown}
              </text>
            </svg>
          )}
        </svg>
        <input
          id={rangeId}
          type="range"
          className={styles.range}
          min={0}
          max={Math.max(0, last)}
          step={1}
          value={current ?? 0}
          disabled={off}
          aria-disabled={waiting || undefined}
          aria-valuetext={value === undefined ? 'no value' : shown}
          aria-describedby={describedBy}
          onKeyDown={onRangeKey}
          onChange={(event) => go(Number(event.target.value))}
        />
      </div>
      <div aria-live="polite" className={styles.message}>
        {error && (
          <p id={errorId} className={styles.error}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
