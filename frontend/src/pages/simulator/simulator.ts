/**
 * The simulator's state, and what follows from it, without React.
 *
 * The four assumptions live in the address as they were typed (text, so a box
 * can hold "12." on its way to "12.5"), along with the cut ``t``. They are
 * checked against the bounds and the defaults ``/model-info`` serves, never a
 * copy, and sent only once all four can be: always all four, so a default
 * left out and one written ask for the same sweep. Every figure the page then
 * shows is the response's; nothing here works out a business figure.
 */
import type { Assumptions, ModelInfo, Sweep, SweepPoint } from '../../api/queries';
import { param, type Values } from '../../app/params';
import { cut, minorDigits } from '../../lib/format';

export type Bounds = NonNullable<ModelInfo['assumption_bounds']>;
export type Range = Bounds[keyof Bounds];
export type Echo = Sweep['assumptions'];

export type FieldKey = keyof Assumptions;

/**
 * How a field is shown, which sets how precisely it can be sent: what is
 * sent is what is shown. Money to the cent, a rate in whole percents (the
 * slider's steps); whether months must be whole, the served bounds say.
 */
export type Unit = 'money' | 'percent' | 'months';

export interface FieldSpec {
  /** The key in the address and in the request. */
  key: FieldKey;
  /** The assumption it sets, as the API's bounds, defaults and echo name it. */
  assumption: keyof Bounds;
  unit: Unit;
}

export const FIELDS: readonly FieldSpec[] = [
  { key: 'cost', assumption: 'retention_offer_cost', unit: 'money' },
  { key: 'success', assumption: 'offer_success_rate', unit: 'percent' },
  { key: 'horizon', assumption: 'expected_horizon_months', unit: 'months' },
  { key: 'margin', assumption: 'gross_margin', unit: 'percent' },
];

/** Long enough for any number a field takes: anything longer is refused whole. */
export const MAX_TEXT = 12;
/**
 * What a box and the address keep: well past ``MAX_TEXT``, so text too long
 * to send is refused as it was typed, never cut down to a number that could
 * be sent; short enough to keep a pasted essay out of the address.
 */
const KEPT_TEXT = 64;

export const SIMULATOR_PARAMS = {
  cost: param.decimal({ maxLength: KEPT_TEXT }),
  success: param.decimal({ maxLength: KEPT_TEXT }),
  horizon: param.decimal({ maxLength: KEPT_TEXT }),
  margin: param.decimal({ maxLength: KEPT_TEXT }),
  /** The cut set by hand, as ``cut`` writes it ("0.40", on the 0-to-1 grid); empty while following the recommendation. */
  t: param.text({ pattern: /^(0\.\d\d|1\.00)$/, maxLength: 4 }),
};

export type State = Values<typeof SIMULATOR_PARAMS>;

/**
 * Why a field cannot be sent: nothing given (``required``), longer than any
 * number it takes (``long``), not a number (``format``), not whole where the API takes whole months (``whole``), more
 * precise than the field shows (``step``), or outside the served bounds.
 */
export type FieldProblem =
  | { reason: 'required' | 'long' | 'format' | 'whole' | 'step' }
  | { reason: 'range'; min: number; max: number };

export type Reading = { value: number; problem: null } | { value: null; problem: FieldProblem };

/**
 * What a number box keeps of what is typed: all of it but spaces, so the
 * address can read it back. Nothing is turned into another number ("12,50"
 * stays "12,50", for ``readField`` to refuse); only the true minus the page
 * writes negatives with is read as a minus. Only a paste far longer than
 * any number is cut, to ``KEPT_TEXT``, and what is left is still refused.
 */
export function typedNumber(raw: string): string {
  return raw.replace(/\s+/g, '').replace(/^−/, '-').slice(0, KEPT_TEXT);
}

/** At least one digit, at most one point, a minus in front. */
const NUMBER = /^-?(\d+\.?\d*|\.\d+)$/;

/** Digits after the point that matter: "12.340" has two. */
function places(text: string): number {
  const fraction = text.split('.')[1] ?? '';
  return fraction.replace(/0+$/, '').length;
}

/**
 * ``text`` as the number it would send, or why it cannot be sent. ``decimals``
 * is how precisely the field is shown: a money's minor unit, or 2 for a rate
 * (whole percents).
 */
export function readField(text: string, range: Range, decimals: number): Reading {
  const refuse = (problem: FieldProblem): Reading => ({ value: null, problem });
  if (text === '') return refuse({ reason: 'required' });
  if (text.length > MAX_TEXT) return refuse({ reason: 'long' });
  if (!NUMBER.test(text)) return refuse({ reason: 'format' });
  if (range.integer && places(text) > 0) return refuse({ reason: 'whole' });
  if (places(text) > decimals) return refuse({ reason: 'step' });
  const value = Number(text);
  if (value < range.min || value > range.max)
    return refuse({ reason: 'range', min: range.min, max: range.max });
  return { value, problem: null };
}

/** How precisely a field is shown, and so sent: money to its minor unit, a rate in whole percents. */
export function decimalsOf(spec: FieldSpec, currency: string): number {
  return spec.unit === 'money' ? minorDigits(currency) : 2;
}

export interface Scenario {
  /** What each field shows: the address's text, or the default's. */
  texts: Record<FieldKey, string>;
  readings: Record<FieldKey, Reading>;
  /** The request, once every field can be sent. */
  assumptions: Assumptions | null;
}

/** The assumptions the address sets, the served defaults standing in for any left out. */
export function scenarioOf(state: State, bounds: Bounds, defaults: Echo): Scenario {
  const texts = {} as Record<FieldKey, string>;
  const readings = {} as Record<FieldKey, Reading>;
  const values: Partial<Assumptions> = {};
  for (const spec of FIELDS) {
    const text = state[spec.key] ?? String(defaults[spec.assumption]);
    const reading = readField(text, bounds[spec.assumption], decimalsOf(spec, defaults.currency));
    texts[spec.key] = text;
    readings[spec.key] = reading;
    if (reading.problem === null) values[spec.key] = reading.value;
  }
  const { cost, success, horizon, margin } = values;
  const complete =
    cost !== undefined && success !== undefined && horizon !== undefined && margin !== undefined;
  return { texts, readings, assumptions: complete ? { cost, success, horizon, margin } : null };
}

/** Every field at its served default, and the cut following the recommendation. */
export function atDefaults(scenario: Scenario, defaults: Echo, t: string): boolean {
  return (
    t === '' &&
    FIELDS.every((spec) => scenario.readings[spec.key].value === defaults[spec.assumption])
  );
}

/** The response is for ``assumptions``: the server's echo, field by field. */
export function echoes(sweep: Sweep, assumptions: Assumptions): boolean {
  return FIELDS.every((spec) => sweep.assumptions[spec.assumption] === assumptions[spec.key]);
}

/**
 * What the figures on screen are. ``current``: the response to the
 * assumptions shown. ``updating``: an earlier response, kept while the next
 * loads. ``mismatch``: a response the server echoed other assumptions for.
 * ``unsent``: a field cannot be sent, so nothing was asked for. ``failed``:
 * the request for these assumptions failed. ``withheld``: the server shows no
 * validation outcomes, so there is no sweep to ask for.
 */
export type Status =
  'current' | 'loading' | 'updating' | 'mismatch' | 'unsent' | 'failed' | 'withheld';

export function statusOf({
  withheld,
  assumptions,
  sweep,
  placeholder,
  error,
}: {
  withheld: boolean;
  assumptions: Assumptions | null;
  sweep: Sweep | undefined;
  placeholder: boolean;
  error: Error | null;
}): Status {
  if (withheld) return 'withheld';
  if (assumptions === null) return 'unsent';
  if (error) return 'failed';
  if (!sweep) return 'loading';
  if (placeholder) return 'updating';
  return echoes(sweep, assumptions) ? 'current' : 'mismatch';
}

/** The index of the point labelled ``label``, or -1. Ten grid values are inexact floats (0.35000000000000003), so labels, never floats, are compared. */
function indexOf(points: readonly SweepPoint[], label: string): number {
  return points.findIndex((point) => cut(point.threshold) === label);
}

export interface Selection {
  index: number;
  recommended: number;
  /**
   * The address sets no cut, so the cut shown is the recommendation and moves
   * with it. A cut set by hand is not followed, even where it is the
   * recommendation now: it stays put when the assumptions change.
   */
  following: boolean;
}

/** The cut ``t`` names, or the recommendation when it names none. */
export function selection(sweep: Sweep, t: string): Selection {
  const recommended = indexOf(sweep.points, cut(sweep.recommended.threshold));
  const named = t === '' ? -1 : indexOf(sweep.points, t);
  const index = named === -1 ? recommended : named;
  return { index, recommended, following: named === -1 };
}

/** What ``t`` becomes when point ``index`` is chosen: nothing for the recommendation, which is then followed. */
export function cutAfter(sweep: Sweep, index: number): string {
  const point = sweep.points[index];
  if (!point || index === selection(sweep, '').recommended) return '';
  return cut(point.threshold);
}

/** One response kept as it came: its echo and the point at its cut. */
export interface Pin {
  assumptions: Echo;
  /** The cut as the address held it: empty while following the recommendation. */
  t: string;
  point: SweepPoint;
  /** The response's churners, for the point's recall. */
  churners: number;
  /**
   * The point's cut was the recommendation under its assumptions (the REC
   * tag), whether followed or set by hand: ``t`` says which Apply restores.
   */
  atRecommendation: boolean;
}

export function samePin(a: Pick<Pin, 'assumptions' | 't'>, b: Pick<Pin, 'assumptions' | 't'>) {
  return (
    a.t === b.t &&
    a.assumptions.currency === b.assumptions.currency &&
    FIELDS.every((spec) => a.assumptions[spec.assumption] === b.assumptions[spec.assumption])
  );
}

/** An echo's assumptions as the address holds them, to set them again. */
export function textsOf(echo: Echo): Record<FieldKey, string> {
  return {
    cost: String(echo.retention_offer_cost),
    success: String(echo.offer_success_rate),
    horizon: String(echo.expected_horizon_months),
    margin: String(echo.gross_margin),
  };
}
