/**
 * How V2 figures are written. Formatting only: an amount arrives as the API's
 * exact decimal text ("1250.50") and is written from that text - never turned
 * into a binary float, never added to another.
 */

const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

const shortDateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function asDate(iso: string): Date | null {
  if (!ISO_DATE.test(iso)) return null;
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "2026-10-31" -> "31 October 2026"; anything else as it came. */
export function longDate(iso: string): string {
  const date = asDate(iso);
  return date ? dateFormat.format(date) : iso;
}

/** "2026-10-31" -> "31 Oct 2026"; anything else as it came. */
export function shortDate(iso: string): string {
  const date = asDate(iso);
  return date ? shortDateFormat.format(date) : iso;
}

const moneyFormats = new Map<string, Intl.NumberFormat>();

/**
 * An amount in its currency, from the API's decimal text: "1250.5", "EUR" ->
 * "€1,250.50". Intl reads the text as an exact decimal; at most four decimals
 * are kept, as many as the contract allows, so nothing is rounded away.
 */
export function amount(value: string, currency: string): string {
  let format = moneyFormats.get(currency);
  if (!format) {
    try {
      format = new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
        maximumFractionDigits: 4,
      });
    } catch {
      return `${value} ${currency}`;
    }
    moneyFormats.set(currency, format);
  }
  return format.format(value as Intl.StringNumericLiteral);
}

/** A count with thousands separators. */
export function count(value: number): string {
  return value.toLocaleString('en-US');
}

/** "1 account" / "3 accounts". */
export function plural(value: number, one: string, many = `${one}s`): string {
  return `${count(value)} ${value === 1 ? one : many}`;
}
