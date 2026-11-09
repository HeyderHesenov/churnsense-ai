/**
 * The one place numbers become text. Every figure on every page goes through
 * these, so "0.36", "26.5%" and "$456,117" are written the same way everywhere.
 * Formatting only: nothing here computes a figure.
 */

const MINUS = '−';

/**
 * The sign of a figure as it is shown: a true minus for a negative, nothing
 * for zero. Taken from the rounded value, so -0.04% is "0.0%", never "−0.0%".
 */
function minus(shown: number): string {
  return shown < 0 ? MINUS : '';
}

/** Whole number with thousands separators: 7043 -> "7,043", -1200 -> "−1,200". */
export function int(value: number): string {
  const shown = Math.round(value);
  return minus(shown) + Math.abs(shown).toLocaleString('en-US');
}

/** A share as a percentage: 0.265 -> "26.5%". */
export function pct(value: number, digits = 1): string {
  const shown = Number((value * 100).toFixed(digits));
  return `${minus(shown)}${Math.abs(shown).toFixed(digits)}%`;
}

/**
 * A probability, honest at the ends of the scale. The model clamps to
 * [epsilon, 1 - epsilon], so 0.99988 must not read as a certain "1.000":
 * above 0.999 and below 0.001 four decimals are shown.
 */
export function probability(value: number): string {
  return value > 0.999 || value < 0.001 ? value.toFixed(4) : value.toFixed(3);
}

/** A probability as a percentage, with the same care at the ends: "80.3%", "99.99%". */
export function probabilityPct(value: number): string {
  return value > 0.999 || value < 0.001 ? `${(value * 100).toFixed(2)}%` : pct(value);
}

/** A threshold on the 0.01 grid: 0.36 -> "0.36". */
export function cut(value: number): string {
  return value.toFixed(2);
}

interface CurrencyFormat {
  /** What an amount is written after: "$" for USD, "€" for EUR, "CHF " where there is no symbol. */
  symbol: string;
  /** The digits of its minor unit: 2 for cents, 0 for yen, 3 for fils. */
  digits: number;
}

const formats = new Map<string, CurrencyFormat>();

/**
 * How amounts in ``currency`` are written. The currency is configuration, not
 * a checked code: one ``Intl`` cannot read is written as given, in cents.
 */
function currencyFormat(currency: string): CurrencyFormat {
  let known = formats.get(currency);
  if (known === undefined) {
    known = readFormat(currency);
    formats.set(currency, known);
  }
  return known;
}

function readFormat(currency: string): CurrencyFormat {
  if (currency.trim() === '') return { symbol: '', digits: 2 };
  try {
    const format = new Intl.NumberFormat('en-US', { style: 'currency', currency });
    const parts = format.formatToParts(0);
    const before = parts.findIndex((part) => part.type === 'integer');
    return {
      symbol: parts
        .slice(0, before)
        .map((part) => part.value)
        .join(''),
      digits: format.resolvedOptions().maximumFractionDigits ?? 2,
    };
  } catch {
    return { symbol: `${currency}\u00a0`, digits: 2 };
  }
}

function symbol(currency: string): string {
  return currencyFormat(currency).symbol;
}

/** The decimals an amount of ``currency`` is written to: 2 for USD, 0 for JPY. */
export function minorDigits(currency: string): number {
  return currencyFormat(currency).digits;
}

/** Whole dollars (or another currency), with a true minus sign: -1234 -> "−$1,234". */
export function money(value: number, currency = 'USD'): string {
  const text = `${symbol(currency)}${Math.round(Math.abs(value)).toLocaleString('en-US')}`;
  return value < 0 && Math.round(Math.abs(value)) !== 0 ? MINUS + text : text;
}

/** Dollars and cents (a currency's minor unit): 105.9 -> "$105.90". */
export function cents(value: number, currency = 'USD'): string {
  const digits = minorDigits(currency);
  return `${symbol(currency)}${value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** An amount someone set: cents only when it has some, 50 -> "$50", 12.5 -> "$12.50". */
export function price(value: number, currency = 'USD'): string {
  const scale = 10 ** minorDigits(currency);
  return Math.round(value * scale) % scale === 0 ? money(value, currency) : cents(value, currency);
}

/** A signed amount of dollars: "+$32", "−$1,200"; nothing to sign is "$0". */
export function signedMoney(value: number, currency = 'USD'): string {
  const shown = Math.round(value);
  return (shown > 0 ? '+' : minus(shown)) + money(Math.abs(value), currency);
}

/**
 * Compact dollars for an axis: 26579 -> "$26.6k", 14090000 -> "$14.1M",
 * 950 -> "$950". Each step up happens at the rounded value, so nothing reads
 * "$1000k".
 */
export function moneyShort(value: number, currency = 'USD'): string {
  const dollars = Math.round(Math.abs(value));
  const hundreds = Math.round(dollars / 100);
  const figure =
    hundreds >= 10_000
      ? `${Math.round(dollars / 100_000) / 10}M`
      : dollars >= 1000
        ? `${hundreds / 10}k`
        : String(dollars);
  const text = symbol(currency) + figure;
  return value < 0 && dollars !== 0 ? MINUS + text : text;
}

/** Thousands of dollars to one decimal, for a card: 226179 -> "$226.2k". */
export function moneyK(value: number): string {
  const shown = Number((value / 1000).toFixed(1));
  return `${minus(shown)}$${Math.abs(shown).toFixed(1)}k`;
}

/** A signed contribution in percentage points: 0.317 -> "+31.7", -0.209 -> "−20.9". */
export function signedPoints(fraction: number, digits = 1): string {
  const shown = Number((fraction * 100).toFixed(digits));
  return (shown > 0 ? '+' : minus(shown)) + Math.abs(shown).toFixed(digits);
}

/**
 * Axis labels for ``ticks`` (evenly spaced fractions) in percentage points, as
 * many decimals as their step needs: a step of 0.2 points must not read "0, 0, +1".
 * Only the text changes; a tick stays where it is. A tick at 0 reads "0".
 */
export function tickLabels(ticks: readonly number[]): string[] {
  const steps = ticks.slice(1).map((tick, i) => Math.abs(tick - (ticks[i] ?? tick)) * 100);
  const step = steps.length > 0 ? Math.min(...steps) : 1;
  // The tolerance absorbs a computed step's rounding error (0.1 * 3).
  const digits = step > 0 ? Math.max(0, Math.ceil(-Math.log10(step) - 1e-9)) : 0;
  return ticks.map((tick) => (tick === 0 ? '0' : signedPoints(tick, digits)));
}

/** The sign ``signedPoints`` shows: 1, −1, or 0 for a contribution that rounds to nothing. */
export function shownSign(fraction: number, digits = 1): -1 | 0 | 1 {
  const shown = Number((fraction * 100).toFixed(digits));
  return shown > 0 ? 1 : shown < 0 ? -1 : 0;
}

/** A ratio to a baseline: 2.36 -> "2.36×". */
export function times(value: number, digits = 2): string {
  return `${value.toFixed(digits)}×`;
}

/** Tenure in months: 1 -> "1 month", 3 -> "3 months". */
export function months(value: number): string {
  return `${value} ${value === 1 ? 'month' : 'months'}`;
}
