import { describe, expect, it } from "vitest";

import { parseObjects, parseRows } from "@/lib/chart/dsv";

/**
 * The delimited-text reader the renderer gives Vega in place of d3-dsv's, which makes code.
 *
 * A chart must read the same here as in a browser, so every expected value below is what d3-dsv
 * 3.0.1 returns for the same text (checked against it on 600,000 generated strings when this was
 * written; d3-dsv is not a dependency of the app, so the check is not repeated here).
 */
describe("parseRows", () => {
  it.each([
    ["", []],
    ["\n", [[""]]],
    ["\r", [[""]]],
    ["a,b", [["a", "b"]]],
    ["a,b\n", [["a", "b"]]],
    ["a,b\r\nc,d\r\n", [["a", "b"], ["c", "d"]]],
    ["a\rb", [["a"], ["b"]]],
    ["a\n\n", [["a"], [""]]],
    ["a\n\nb", [["a"], [""], ["b"]]],
    ["a,", [["a", ""]]],
    [",", [["", ""]]],
    ['"x, y",2', [["x, y", "2"]]],
    ['"say ""hi""",2', [['say "hi"', "2"]]],
    ['"line\nbreak",2\n3,4', [["line\nbreak", "2"], ["3", "4"]]],
    ['"a"b,c', [["a", "", "c"]]], // whatever follows a closing quote is taken as the separator
    ['"open', [["open"]]],
    ['"open\r', [["open\r"]]], // an unclosed quote keeps one trimmed character, as d3-dsv does
  ])("reads %j as d3-dsv does", (text, rows) => {
    expect(parseRows(text, ",")).toEqual(rows);
  });

  it("splits on the delimiter it is given and on no other", () => {
    expect(parseRows("a\tb,c\n1\t2", "\t")).toEqual([["a", "b,c"], ["1", "2"]]);
    expect(parseRows("a|b\n1|2", "|")).toEqual([["a", "b"], ["1", "2"]]);
  });
});

describe("parseObjects", () => {
  it("keys each row by the header and reads a missing field as empty", () => {
    expect(parseObjects("a,b\n1,2\n3", ",")).toEqual([{ a: "1", b: "2" }, { a: "3", b: "" }]);
  });

  it("uses the columns the format names instead of a header row", () => {
    expect(parseObjects("1,2\n3,4", ",", ["x", "y"])).toEqual([{ x: "1", y: "2" }, { x: "3", y: "4" }]);
  });

  it("treats a column named __proto__ as a field, not as the prototype", () => {
    const [row] = parseObjects("__proto__,b\npolluted,2", ",");
    expect(Object.getPrototypeOf(row)).toBe(Object.prototype);
    expect(Object.keys(row)).toEqual(["__proto__", "b"]);
    expect(Object.getOwnPropertyDescriptor(row, "__proto__")?.value).toBe("polluted");
  });

  it("calls no Function to build a row", () => {
    const real = globalThis.Function;
    globalThis.Function = new Proxy(real, {
      apply: () => {
        throw new Error("Function() called");
      },
      construct: () => {
        throw new Error("new Function() called");
      },
    });
    try {
      expect(parseObjects("a,b\n1,2", ",")).toEqual([{ a: "1", b: "2" }]);
    } finally {
      globalThis.Function = real;
    }
  });
});
