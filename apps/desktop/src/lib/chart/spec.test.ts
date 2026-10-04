import { describe, expect, it } from "vitest";

import { CHART_MAX_GENERATED_ROWS, generatedRows, reachesOutside, withheldOf } from "@/lib/chart/spec";
import GENERATED from "@/lib/chart/fixtures/generated-rows.json?raw";

/**
 * The client's copy of the server's check (`chart.py: _reaches_outside`): a spec the viewer finds in
 * a page is drawn only if it names nothing to load or follow. The card says so in every language
 * when it is not, so a chart that loads nothing must not be called external.
 */
const ENCODING = { x: { field: "url", type: "nominal" }, y: { field: "visits", type: "quantitative" } };
const ROWS = [{ url: "/home", visits: 3 }, { url: "/about", visits: 1 }];

describe("reachesOutside", () => {
  it.each([
    ["a column named url", { data: { values: ROWS }, mark: "bar", encoding: ENCODING }],
    ["a column named href", { data: { values: [{ href: "https://example.com", b: 1 }] }, mark: "bar", encoding: ENCODING }],
    ["rows in a layer", { layer: [{ data: { values: ROWS }, mark: "bar", encoding: ENCODING }] }],
    ["rows in datasets", { data: { name: "rows" }, datasets: { rows: ROWS }, mark: "bar", encoding: ENCODING }],
    [
      "rows a lookup reads",
      { data: { values: ROWS }, transform: [{ lookup: "url", from: { data: { values: ROWS }, key: "url" } }], mark: "bar", encoding: ENCODING },
    ],
  ])("does not count %s as reaching outside", (_name, spec) => {
    expect(reachesOutside(spec)).toBe(false);
  });

  it.each([
    ["data by address", { data: { url: "https://example.com/d.csv" }, mark: "bar", encoding: ENCODING }],
    ["rows drawn as links", { data: { values: ROWS }, mark: "bar", encoding: { ...ENCODING, href: { field: "url" } } }],
    ["rows drawn as images", { data: { values: ROWS }, mark: "image", encoding: { ...ENCODING, url: { field: "url" } } }],
    ["a lookup by address", { data: { values: ROWS }, transform: [{ lookup: "url", from: { data: { url: "x.csv" }, key: "url" } }] }],
    ["a layer by address", { layer: [{ data: { url: "x.json" }, mark: "bar" }] }],
  ])("counts %s as reaching outside", (_name, spec) => {
    expect(reachesOutside(spec)).toBe(true);
  });
});

// The cases chart.py's `generated_rows` is tested against too
// (tests/test_a_spec_that_makes_up_millions_of_rows_never_reaches_the_screen.py): one fixture, so the
// two estimates cannot drift apart without one of the suites going red.

type GeneratedCase = { name: string; spec: Record<string, unknown>; rows: number | "inf" };
const CASES = JSON.parse(GENERATED) as GeneratedCase[];
const expected = (c: GeneratedCase) => (c.rows === "inf" ? Infinity : c.rows);

describe("generatedRows", () => {
  it.each(CASES.map((c) => [c.name, c] as const))("reads %s the way the server does", (_name, c) => {
    expect(generatedRows(c.spec)).toBe(expected(c));
  });
});

describe("withheldOf", () => {
  it("withholds every case over the budget as heavy, and draws the rest", () => {
    const heavy = CASES.filter((c) => expected(c) > CHART_MAX_GENERATED_ROWS);
    expect(heavy.length).toBeGreaterThan(5);
    for (const c of CASES) expect(withheldOf(c.spec)).toBe(expected(c) > CHART_MAX_GENERATED_ROWS ? "heavy" : null);
  });

  it("calls a spec that reaches outside external before it calls it heavy", () => {
    const spec = { data: { sequence: { start: 0, stop: 1e7, as: "x" } }, mark: "point", encoding: { href: { field: "x" } } };
    expect(withheldOf(spec)).toBe("external");
  });
});
