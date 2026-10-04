/**
 * Delimited text (CSV, TSV, any one-character delimiter) read into rows without making code.
 *
 * Vega reads a spec's inline `"format": {"type": "csv"}` data with d3-dsv, and d3-dsv builds the
 * function that turns a row into an object with `new Function`. The app's page policy has no
 * `'unsafe-eval'` (`chimera/api/page_csp.py`), so a chart with CSV data threw instead of drawing.
 *
 * This reads by d3-dsv's rules, so a chart draws the same here as in a browser: RFC 4180 quoting
 * (`""` is a quote; a quoted field may hold the delimiter and line breaks), `\n`, `\r\n` or `\r`
 * between rows, one trailing line break ignored, whatever follows a closing quote taken as the
 * separator, and a missing field read as `""`. Each object is built by assignment. `render.ts`
 * registers it as Vega's reader for `csv`, `tsv` and `dsv`.
 *
 * Imported only by `render.ts`, so it lives in the renderer's chunk.
 */

const QUOTE = 34;
const NEWLINE = 10;
const RETURN = 13;

/** What ended a field: the delimiter, a line break, or the end of the text. */
type Ended = "field" | "row" | "text";

/** Split `text` into rows of fields. */
export function parseRows(text: string, delimiter: string): string[][] {
  const delim = delimiter.charCodeAt(0);
  const rows: string[][] = [];
  // Only text with nothing in it has no rows: a lone line break is one empty row, as in d3-dsv.
  if (text.length === 0) return rows;
  let n = text.length;
  if (text.charCodeAt(n - 1) === NEWLINE) --n;
  if (text.charCodeAt(n - 1) === RETURN) --n;

  let row: string[] = [];
  let i = 0;
  for (;;) {
    let field: string;
    let ended: Ended = "text";
    if (text.charCodeAt(i) === QUOTE) {
      let j = i + 1;
      while (j < n) {
        if (text.charCodeAt(j) !== QUOTE) ++j;
        else if (j + 1 < n && text.charCodeAt(j + 1) === QUOTE) j += 2;
        else break;
      }
      // A quote never closed runs to the end, and, as in d3-dsv, takes one character of the line
      // break trimmed off the end with it. Malformed text, but the same malformed result.
      field = text.slice(i + 1, j < n ? j : n + 1).replace(/""/g, '"');
      i = j + 1; // past the closing quote; the character there separates, whatever it is
      if (i < n) {
        const c = text.charCodeAt(i++);
        if (c === NEWLINE) ended = "row";
        else if (c === RETURN) {
          ended = "row";
          if (text.charCodeAt(i) === NEWLINE) ++i;
        } else ended = "field";
      }
    } else {
      let j = i;
      while (j < n) {
        const c = text.charCodeAt(j);
        if (c === delim) {
          ended = "field";
          break;
        }
        if (c === NEWLINE || c === RETURN) {
          ended = "row";
          break;
        }
        ++j;
      }
      field = text.slice(i, j);
      i = j + 1;
      if (ended === "row" && text.charCodeAt(j) === RETURN && text.charCodeAt(i) === NEWLINE) ++i;
    }
    row.push(field);
    if (ended === "text") {
      rows.push(row);
      return rows;
    }
    if (ended === "row") {
      rows.push(row);
      row = [];
    }
    // A delimiter or line break just before the end leaves one more, empty, field to read: the
    // next pass reads it as "" and ends the text, as d3-dsv does.
  }
}

/** Rows as objects keyed by the header row (or by `header`, when the format names the columns). */
export function parseObjects(text: string, delimiter: string, header?: readonly string[]): Record<string, string>[] {
  const rows = parseRows(text, delimiter);
  const columns = header ?? rows.shift() ?? [];
  return rows.map((row) => {
    const object: Record<string, string> = {};
    columns.forEach((column, k) => {
      // defineProperty, not assignment: a column named `__proto__` is a field like any other, never
      // the object's prototype.
      Object.defineProperty(object, column, {
        value: row[k] ?? "",
        enumerable: true,
        writable: true,
        configurable: true,
      });
    });
    return object;
  });
}
