/**
 * The spec inside a page `render_chart` wrote, so the viewer can draw it without the page's CDN.
 *
 * The page loads Vega from cdn.jsdelivr.net, which is right for a file opened in a browser and was
 * the one reason the app's policy let a CDN and `'unsafe-eval'` in (the preview frame inherits it).
 * The viewer now finds the spec and draws it with the app's own renderer instead, so the policy no
 * longer needs either.
 *
 * Only `render_chart`'s own two templates are recognised, byte for byte. Anything else is not one
 * of ours and stays a page in the frame.
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Today's template: the spec as inert JSON in its own element (chart.py: `_html`).
const EMBEDDED = /<script type="application\/json" id="chimera-chart-spec">([\s\S]*?)<\/script>/;
// The template before it: the spec as an object literal in a script. Exactly the bytes
// `json.dumps(spec, indent=2)` produced, so JSON.parse reads it.
const LEGACY =
  /<div id="vis"><\/div>\s*<script>\s*const spec = ([\s\S]*?);\s*vegaEmbed\('#vis', spec\)\.catch\(console\.error\);\s*<\/script>/;
// Both templates load vega-embed from here; a page that does not is not one of ours.
const LOADER = /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/vega-embed@\d+"><\/script>/;

// The bare words `json.dumps` writes for a float JSON has no spelling for. Longest first.
const NON_FINITE = ["-Infinity", "Infinity", "NaN"];

/**
 * `text` with each bare NaN and infinity outside a string written as `null`.
 *
 * The old template wrote the spec with `json.dumps`, which spells a NaN or infinite value `NaN` /
 * `Infinity` / `-Infinity`. As the object literal the page ran, that was valid JavaScript (they are
 * globals), so such a page drew; `JSON.parse` refuses it, and the page fell back to a frame whose
 * CDN script the policy refuses, which drew nothing and said nothing. Vega reads null as missing,
 * as it read NaN; chart.py's `_finite` makes the same replacement for pages written today. A word
 * inside a string (a title "NaN rows") is text, and is left alone.
 */
export function nonFiniteAsNull(text: string): string {
  let out = "";
  let inString = false;
  for (let i = 0; i < text.length; ) {
    const c = text[i];
    if (inString) {
      // An escape takes the character after it with it, so `\"` does not end the string.
      const take = c === "\\" ? 2 : 1;
      out += text.slice(i, i + take);
      if (c === '"') inString = false;
      i += take;
      continue;
    }
    if (c === '"') inString = true;
    const word = NON_FINITE.find((w) => text.startsWith(w, i));
    if (word) {
      out += "null";
      i += word.length;
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

/** The Vega-Lite spec inside a page `render_chart` wrote, or null for any other page. */
export function chartSpecOf(html: string): Record<string, unknown> | null {
  if (!LOADER.test(html)) return null;
  const embedded = EMBEDDED.exec(html)?.[1];
  const legacy = embedded === undefined ? LEGACY.exec(html)?.[1] : undefined;
  // Only the old template can hold the bare words: today's spec went through `_finite` first.
  const body = embedded ?? (legacy === undefined ? undefined : nonFiniteAsNull(legacy));
  if (body === undefined) return null;
  try {
    const spec: unknown = JSON.parse(body);
    return isRecord(spec) ? spec : null;
  } catch {
    return null;
  }
}
