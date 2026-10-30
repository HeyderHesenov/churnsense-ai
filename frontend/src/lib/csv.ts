/**
 * CSV files built in the browser: the template and one prediction. A scored
 * batch is built by the server (``BatchScoreResponse.csv``).
 *
 * Every cell is defused exactly as the server's exporter defuses it
 * (churnsense.exports): a leading =, +, -, @, tab or carriage return gets an
 * apostrophe, and so does a trigger after ;, a tab, a line break or a quote,
 * where a ';'-separator locale would start a new cell. Both are tested against
 * tests/fixtures/csv_neutralisation_cases.json.
 */

const TRIGGERS = ['=', '+', '-', '@', '\t', '\r'];
const SPLIT_FORMULA = /(?<=[;\t\r\n"])(?=[=+\-@])/g;

/** One text cell with every place a spreadsheet would start a formula made inert. */
export function defuse(text: string): string {
  const fixed = text.replace(SPLIT_FORMULA, "'");
  return TRIGGERS.some((t) => text.startsWith(t)) ? `'${fixed}` : fixed;
}

export type Cell = string | number | boolean | null | undefined;

/** One cell as CSV: text defused, then quoted when it holds a comma, quote or line break. */
function encode(value: Cell): string {
  if (value === null || value === undefined) return '';
  const text = typeof value === 'string' ? defuse(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** A whole file: a header row, then one line per row, CRLF-free. */
export function toCsv(header: readonly string[], rows: readonly (readonly Cell[])[]): string {
  return [header, ...rows].map((row) => row.map(encode).join(',')).join('\n') + '\n';
}
