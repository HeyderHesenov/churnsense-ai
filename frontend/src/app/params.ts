/**
 * A page's state in the address bar, typed.
 *
 * A page declares each key once, at module level, with its parser and its
 * default; ``useParams`` reads them and writes changes back. An address is
 * typed by people and pasted from elsewhere, so it is untrusted: a value that
 * does not parse reads as the default - never an error, never a request with
 * it. Defaults are left out of the address, so a page with nothing chosen has
 * a clean URL.
 */
import { useCallback, useMemo } from 'react';
import { useLocation, useSearchParams, withParams, type ParamValue } from './router';

export interface Param<T> {
  readonly fallback: T;
  /** The value of every occurrence of the key, in order; ``[]`` if absent. */
  parse(values: readonly string[]): T;
  /** What to put in the address; ``undefined`` leaves the key out. */
  write(value: T): ParamValue;
}

/** One of ``values``. */
function choice<const T extends string>(values: readonly T[], fallback: T): Param<T> {
  const valid = (raw: string | undefined): raw is T => values.includes(raw as T);
  return {
    fallback,
    parse: ([raw]) => (valid(raw) ? raw : fallback),
    write: (value) => (value === fallback ? undefined : value),
  };
}

/** One of ``values``, or nothing chosen. */
function optional<const T extends string>(values: readonly T[]): Param<T | null> {
  const valid = (raw: string | undefined): raw is T => values.includes(raw as T);
  return {
    fallback: null,
    parse: ([raw]) => (valid(raw) ? raw : null),
    write: (value) => value ?? undefined,
  };
}

/** ``raw`` as a whole number in ``[min, max]``, or ``null``. */
function whole(raw: string | undefined, min: number, max: number): number | null {
  if (raw === undefined || !/^-?\d{1,15}$/.test(raw)) return null;
  const value = Number(raw);
  return value >= min && value <= max ? value : null;
}

/** A whole number in ``[min, max]``. */
function int({
  min,
  max,
  fallback,
}: {
  min: number;
  max: number;
  fallback: number;
}): Param<number> {
  return {
    fallback,
    parse: ([raw]) => whole(raw, min, max) ?? fallback,
    write: (value) => (value === fallback ? undefined : value),
  };
}

/** A whole number in ``[min, max]``, or nothing given (a bound left open). */
function optionalInt({ min, max }: { min: number; max: number }): Param<number | null> {
  return {
    fallback: null,
    parse: ([raw]) => whole(raw, min, max),
    // ``??``, not ``||``: zero is a bound like any other.
    write: (value) => value ?? undefined,
  };
}

/** Free text a filter takes, held to the same pattern and length the API enforces. */
function text({ pattern, maxLength }: { pattern: RegExp; maxLength: number }): Param<string> {
  // A global or sticky pattern remembers where it last matched, so the same
  // value would alternate between valid and not.
  if (pattern.global || pattern.sticky)
    throw new Error('param.text takes a pattern without the g or y flag');
  return {
    fallback: '',
    parse: ([raw]) =>
      raw !== undefined && raw.length <= maxLength && pattern.test(raw) ? raw : '',
    write: (value) => value || undefined,
  };
}

/** No space or line break: what a number box keeps of what is typed. */
const UNBROKEN = /^\S*$/;

/**
 * A number as it was typed, kept as text so a box can hold "12." on its way
 * to "12.5", and "12,50" as typed rather than as another number. ``null`` is
 * the key left out (the default); ``''`` is the key given empty, which a page
 * reads as nothing given, not as the default. The page says whether the text
 * is a number in its bounds; only text no box could hold reads as ``null``.
 */
function decimal({ maxLength }: { maxLength: number }): Param<string | null> {
  return {
    fallback: null,
    parse: ([raw]) =>
      raw !== undefined && raw.length <= maxLength && UNBROKEN.test(raw) ? raw : null,
    write: (value) => value ?? undefined,
  };
}

/**
 * Several of ``values``: the key repeated. Members are kept in the order
 * ``values`` declares, so one choice has one address whatever order it was
 * made in; the default, in any order, is left out. An empty choice is written
 * as one empty value (``bands=``), so it stays distinct from "nothing chosen
 * yet", which is the default.
 *
 * A ``null`` default means "decided by the data": the page reads ``null`` as
 * "not chosen" and fills in its own default (the overview: every band that
 * holds flagged customers).
 */
function list<const T extends string, const D extends readonly T[] | null>(
  values: readonly T[],
  fallback: D,
): Param<readonly T[] | D> {
  const ordered = (chosen: Iterable<string>): T[] => {
    const set = new Set(chosen);
    return values.filter((v) => set.has(v));
  };
  const isDefault = (value: readonly T[] | null): boolean =>
    value === null || fallback === null
      ? value === fallback
      : new Set(value).size === fallback.length && value.every((v) => fallback.includes(v));
  return {
    fallback,
    parse: (raw) => {
      if (raw.length === 0) return fallback;
      if (raw.length === 1 && raw[0] === '') return [];
      const chosen = ordered(raw);
      if (chosen.length === 0 || isDefault(chosen)) return fallback;
      return chosen;
    },
    write: (value) => {
      if (value === null || isDefault(value)) return undefined;
      return value.length === 0 ? [''] : ordered(value);
    },
  };
}

export const param = { choice, optional, int, optionalInt, text, decimal, list };

type Schema = Record<string, Param<unknown>>;
export type Values<S extends Schema> = {
  readonly [K in keyof S]: S[K] extends Param<infer T> ? T : never;
};

/** The query values ``changes`` writes, keys the schema does not declare left out. */
function written<S extends Schema>(
  schema: S,
  changes: Partial<Values<S>>,
): Record<string, ParamValue> {
  const query: Record<string, ParamValue> = {};
  for (const [key, value] of Object.entries(changes)) {
    const p = schema[key];
    if (p) query[key] = p.write(value);
  }
  return query;
}

export type SetParams<S extends Schema> = (
  changes: Partial<Values<S>>,
  options?: { replace?: boolean },
) => void;

/**
 * The values ``schema`` declares, read from the address; a setter; and the
 * address a change would lead to (for a real link). The setter writes only
 * the keys it is given and keeps every other key in the query; it adds a
 * history entry unless ``replace`` is set.
 */
export function useParams<S extends Schema>(
  schema: S,
): [Values<S>, SetParams<S>, (changes: Partial<Values<S>>) => string] {
  const location = useLocation();
  const [search, update] = useSearchParams();

  const values = useMemo(() => {
    const read: Record<string, unknown> = {};
    for (const [key, p] of Object.entries(schema)) read[key] = p.parse(search.getAll(key));
    return read as Values<S>;
  }, [schema, search]);

  const set = useCallback<SetParams<S>>(
    (changes, options) => update(written(schema, changes), options),
    [schema, update],
  );

  const href = useCallback(
    (changes: Partial<Values<S>>) => withParams(location, written(schema, changes)),
    [schema, location],
  );

  return [values, set, href];
}
