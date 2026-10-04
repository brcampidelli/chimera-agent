import { describe, expect, it } from "vitest";

import { chartSpecOf, nonFiniteAsNull } from "@/lib/chart/page";
// What chart.py's `_html` writes, byte for byte: tests/test_chart.py checks the file against it.
import CHART_PAGE from "@/lib/chart/fixtures/render-chart-page.html?raw";

/**
 * Finding the spec in a page `render_chart` wrote, so the viewer draws it instead of running it.
 *
 * Only the tool's own two templates count. Recognising more would mean drawing, as a chart, a page
 * that was meant to run — and recognising them loosely would let a page dress up as one of ours.
 */
const SPEC = {
  title: "Sales",
  data: { values: [{ a: "A", b: 5 }, { a: "B", b: 8 }] },
  mark: "bar",
  encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } },
};

// The template chart.py wrote before the spec had its own element; files on disk still have it.
const LEGACY = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
</head>
<body>
  <div id="vis"></div>
  <script>
    const spec = ${JSON.stringify(SPEC, null, 2)};
    vegaEmbed('#vis', spec).catch(console.error);
  </script>
</body>
</html>
`;

describe("chartSpecOf", () => {
  it("reads the spec out of the page render_chart writes today", () => {
    expect(chartSpecOf(CHART_PAGE)).toEqual(SPEC);
  });

  it("reads the spec out of the template it wrote before", () => {
    expect(chartSpecOf(LEGACY)).toEqual(SPEC);
  });

  it("reads an old page whose spec held NaN or an infinity", () => {
    // The old template wrote the spec with `json.dumps`, which spells these as bare words: valid in
    // the object literal the page ran, refused by JSON.parse. Such a page fell back to a frame whose
    // CDN script the policy refuses, and showed nothing. A word inside a string stays a word.
    const body = JSON.stringify({ ...SPEC, title: "NaN rows, -Infinity \\\" NaN" }, null, 2).replace(
      '"b": 5',
      '"b": NaN, "c": Infinity, "d": -Infinity',
    );
    const page = LEGACY.replace(JSON.stringify(SPEC, null, 2), body);
    expect(page).not.toBe(LEGACY);
    expect(chartSpecOf(page)).toEqual({
      ...SPEC,
      title: 'NaN rows, -Infinity \\" NaN',
      data: { values: [{ a: "A", b: null, c: null, d: null }, { a: "B", b: 8 }] },
    });
  });

  it("writes only bare non-finite words as null", () => {
    expect(nonFiniteAsNull('[NaN, -Infinity, Infinity, "NaN", "a\\"NaN"]')).toBe('[null, null, null, "NaN", "a\\"NaN"]');
  });

  it("reads a spec whose text was escaped so it could not close its script", () => {
    const title = "</script><script>alert(1)</script>";
    const escaped = JSON.stringify({ ...SPEC, title }).replace(/</g, "\\u003c").replace(/>/g, "\\u003e");
    const page = CHART_PAGE.replace(/(id="chimera-chart-spec">)[\s\S]*?(<\/script>)/, `$1${escaped}$2`);
    expect(chartSpecOf(page)?.title).toBe(title);
  });

  it("leaves every other page alone", () => {
    expect(chartSpecOf("<h1>Café Aurora</h1>")).toBeNull();
    // Our element, without the tool's loader: not one of ours.
    expect(chartSpecOf(`<script type="application/json" id="chimera-chart-spec">${JSON.stringify(SPEC)}</script>`)).toBeNull();
    // The loader, with a body that is not JSON.
    expect(chartSpecOf(CHART_PAGE.replace(/(id="chimera-chart-spec">)[\s\S]*?(<\/script>)/, "$1{nope$2"))).toBeNull();
    // JSON that is not an object.
    expect(chartSpecOf(CHART_PAGE.replace(/(id="chimera-chart-spec">)[\s\S]*?(<\/script>)/, "$1[1,2]$2"))).toBeNull();
  });
});
