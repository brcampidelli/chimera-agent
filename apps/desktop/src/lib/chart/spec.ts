/**
 * What the app decides about a chart spec before it loads Vega to draw one.
 *
 * Kept out of `render.ts` on purpose: these run on every frame and every previewed file, and
 * importing them must not pull the ~1 MB renderer into the main bundle.
 */

/** Why a chart is not drawn here. The same three words the server's `chart_frame` sends. */
export type Withheld = "external" | "heavy" | "large";

/** What a turn's `chart` frame carries (`chimera/tools/chart.py: chart_frame`). */
export interface CodeChartFrame {
  /** The file `render_chart` wrote, relative to the project when it is inside it. */
  path: string;
  format: string;
  title: string | null;
  /** The Vega-Lite spec, or null when it is withheld. */
  spec: Record<string, unknown> | null;
  withheld: Withheld | null;
  /** The spec's size as compact JSON, so a withheld-as-large card can say how large. */
  bytes: number;
}

// A spec reaches outside itself through these keys: `url` (data, a lookup, an image mark) and
// `href` (a link a click follows). Mirrors `_REACHING_KEYS` in chart.py.
const REACHING_KEYS = new Set(["url", "href"]);

/**
 * Whether a spec names anything to load or follow, at any depth.
 *
 * Inline rows are not read: `values` under a `data` and the tables in `datasets` are inert, and a
 * column that happens to be called `url` loads nothing. Rows reach outside only through a
 * `url`/`href` encoding channel or an image mark's `url`, which are keys of the spec and still caught.
 */
export function reachesOutside(node: unknown, inData = false): boolean {
  if (Array.isArray(node)) return node.some((item) => reachesOutside(item, inData));
  if (node !== null && typeof node === "object")
    return Object.entries(node).some(([k, v]) => {
      if (REACHING_KEYS.has(k)) return true;
      if (k === "datasets" || (inData && k === "values")) return false;
      return reachesOutside(v, k === "data");
    });
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** A spec's title, in each form Vega-Lite allows (a string, `{text}`, or a list of lines). */
export function titleOf(spec: Record<string, unknown>): string | null {
  let title = spec.title;
  if (isRecord(title)) title = title.text;
  if (Array.isArray(title)) title = title.map(String).join(" ");
  return typeof title === "string" && title.trim() ? title : null;
}

// The most rows a spec may make up on its own and still be drawn here. Mirrors
// `CHART_MAX_GENERATED_ROWS` in chart.py, where the measurements are: a 125-byte spec asking for a
// sequence of four million rows held Vega for 29.6 s and 2.19 GB before a single SVG node, and the
// chart draws on the thread that also runs the composer and the approval card.
export const CHART_MAX_GENERATED_ROWS = 50_000;

// Strings under these keys are text a person reads, not expressions Vega runs.
const TEXT_KEYS = new Set(["title", "subtitle", "text", "description"]);

// `sequence(start, stop, step)` makes an array and `pad(text, length)` a string, each as large as an
// argument says. A call whose arguments are not all number literals cannot be sized.
const SEQUENCE_CALL = /\bsequence\s*\(/g;
const SEQUENCE_LITERAL = /\bsequence\s*\(([-+0-9.eE\s,]*)\)/g;
const PAD_CALL = /\bpad\s*\(/g;
const PAD_LITERAL = /\bpad\s*\([^,()]*,\s*([-+0-9.eE]+)\s*[,)]/g;

/** `value` as a finite number, or null for anything else (a signal, a string, a boolean). */
function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A count the spec states: the number, or infinite when it is not one. */
function count(value: unknown): number {
  const n = finiteNumber(value);
  return n === null ? Infinity : Math.max(0, n);
}

/** How many values `range(start, stop, step)` holds; infinite when it cannot be told. */
function span(start: unknown, stop: unknown, step: unknown): number {
  const a = finiteNumber(start);
  const b = finiteNumber(stop);
  const s = finiteNumber(step);
  if (a === null || b === null || s === null || s === 0) return Infinity;
  return Math.max(0, Math.ceil((b - a) / s));
}

/** The comma-separated number literals in `text`, or null when any part is not one. */
function literals(text: string): number[] | null {
  const parts = text.split(",").map((part) => part.trim());
  // `Number("")` is 0 and `Number("1e999")` is Infinity; Python's `float` refuses the first and the
  // server counts the second as infinite, so neither is a size here either.
  if (parts.some((part) => part === "" || !Number.isFinite(Number(part)))) return null;
  return parts.map(Number);
}

/** What the `sequence()` and `pad()` calls in one string make. */
function expressionRows(text: string): number {
  let total = 0;
  const sized = [...text.matchAll(SEQUENCE_LITERAL)];
  if (sized.length !== (text.match(SEQUENCE_CALL) ?? []).length) return Infinity;
  for (const [, args] of sized) {
    const values = literals(args);
    if (values === null || values.length > 3) return Infinity;
    total += values.length === 1 ? span(0, values[0], 1) : span(values[0], values[1], values[2] ?? 1);
  }
  const lengths = [...text.matchAll(PAD_LITERAL)];
  if (lengths.length !== (text.match(PAD_CALL) ?? []).length) return Infinity;
  for (const [, length] of lengths) {
    const values = literals(length);
    total += values === null ? Infinity : count(values[0]);
  }
  return total;
}

/** `node[key]` when the key is there (even as null), else `fallback`: Python's `dict.get`. */
function get(node: Record<string, unknown>, key: string, fallback?: unknown): unknown {
  return key in node ? node[key] : fallback;
}

/** What one object of the spec makes up, read from its own keys. */
function nodeRows(node: Record<string, unknown>): number {
  let total = 0;
  const sequence = node.sequence;
  if (isRecord(sequence)) total += span(get(sequence, "start", 0), get(sequence, "stop"), get(sequence, "step", 1));
  const keyvals = node.keyvals;
  if (isRecord(keyvals)) total += span(get(keyvals, "start", 0), get(keyvals, "stop"), get(keyvals, "step", 1));
  if ("density" in node) total += count(get(node, "steps", get(node, "maxsteps", 200)));
  if ("quantile" in node) {
    const step = finiteNumber(get(node, "step", 0.01));
    total += step !== null && step > 0 ? Math.ceil(1 / step) : Infinity;
  }
  const binning = node.bin;
  if (isRecord(binning)) {
    const step = binning.step ?? null;
    const extent = binning.extent;
    if (step === null) total += count(get(binning, "maxbins", 10));
    else if (Array.isArray(extent) && extent.length === 2) total += span(extent[0], extent[1], step);
    // A step over the data's own extent cannot be sized from the spec: `preflight.ts` stops that one.
  }
  const tickCount = node.tickCount ?? null;
  // A time interval (a string, or `{interval, step}`) depends on the data, like a bin step.
  if (tickCount !== null && typeof tickCount !== "string" && !isRecord(tickCount)) total += count(tickCount);
  return total;
}

/**
 * How many rows, marks or characters a spec asks Vega to make up beyond what it carries.
 *
 * Read without running it, for the generators measured to stall the window. A value it cannot size
 * counts as infinite. Mirrors `generated_rows` in chart.py, which says what each generator is.
 */
export function generatedRows(node: unknown, inData = false): number {
  if (Array.isArray(node)) return node.reduce((sum: number, item: unknown) => sum + generatedRows(item, inData), 0);
  if (typeof node === "string") return expressionRows(node);
  if (!isRecord(node)) return 0;
  let total = nodeRows(node);
  for (const [k, v] of Object.entries(node)) {
    if (k === "datasets" || (inData && k === "values")) continue;
    if (TEXT_KEYS.has(k) && (typeof v === "string" || Array.isArray(v))) continue;
    total += generatedRows(v, k === "data");
  }
  return total;
}

/**
 * Why a spec is not drawn here, or null when it may be. The server decides the same for a frame
 * (`chart_frame`); this decides again for a frame off the wire and for a spec found in a file.
 */
export function withheldOf(spec: Record<string, unknown>): "external" | "heavy" | null {
  if (reachesOutside(spec)) return "external";
  if (generatedRows(spec) > CHART_MAX_GENERATED_ROWS) return "heavy";
  return null;
}
