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

/** Whole dollars, with a true minus sign: -1234 -> "−$1,234". */
export function money(value: number): string {
  const text = `$${Math.round(Math.abs(value)).toLocaleString('en-US')}`;
  return value < 0 && Math.round(Math.abs(value)) !== 0 ? MINUS + text : text;
}

/** Dollars and cents: 105.9 -> "$105.90". */
export function cents(value: number): string {
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** A signed amount of dollars: "+$32", "−$1,200"; nothing to sign is "$0". */
export function signedMoney(value: number): string {
  const shown = Math.round(value);
  return (shown > 0 ? '+' : minus(shown)) + money(Math.abs(value));
}

/** Compact dollars for an axis: 26579 -> "$26.6k", 950 -> "$950". */
export function moneyShort(value: number): string {
  const dollars = Math.round(Math.abs(value));
  const text =
    dollars >= 1000
      ? `$${String(Math.round(dollars / 100) / 10).replace(/\.0$/, '')}k`
      : `$${dollars}`;
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

/** A ratio to a baseline: 2.36 -> "2.36×". */
export function times(value: number, digits = 2): string {
  return `${value.toFixed(digits)}×`;
}

/** Tenure in months: 1 -> "1 month", 3 -> "3 months". */
export function months(value: number): string {
  return `${value} ${value === 1 ? 'month' : 'months'}`;
}
