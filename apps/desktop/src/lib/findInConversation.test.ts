import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMatches, FIND_CURRENT, FIND_HIGHLIGHT, findRanges, paintMatches } from "@/lib/findInConversation";

function log(html: string): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = html;
  document.body.append(el);
  return el;
}

describe("finding in the transcript", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
  });

  it("finds every occurrence, ignoring case, in document order", () => {
    const root = log("<p>Alpha beta</p><p>ALPHA and alpha</p>");
    expect(findRanges(root, "alpha").map((r) => r.toString())).toEqual(["Alpha", "ALPHA", "alpha"]);
  });

  it("matches a phrase across a bold or a code span", () => {
    const root = log("<p>run <code>npm</code> install now</p>");
    expect(findRanges(root, "npm install").map((r) => r.toString())).toEqual(["npm install"]);
  });

  it("finds nothing for an empty query, and treats the query as text, not a pattern", () => {
    const root = log("<p>a.b axb (x)</p>");
    expect(findRanges(root, "  ")).toEqual([]);
    expect(findRanges(root, "a.b").map((r) => r.toString())).toEqual(["a.b"]);
    expect(findRanges(root, "(x)")).toHaveLength(1);
  });

  it("paints the matches and the current one, and clearing leaves nothing painted", () => {
    const registry = new Map<string, unknown>();
    vi.stubGlobal("CSS", { highlights: registry });
    vi.stubGlobal("Highlight", class { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } });
    const ranges = findRanges(log("<p>x x x</p>"), "x");
    paintMatches(ranges, 1);
    expect((registry.get(FIND_HIGHLIGHT) as { ranges: Range[] }).ranges).toHaveLength(3);
    expect((registry.get(FIND_CURRENT) as { ranges: Range[] }).ranges[0]).toBe(ranges[1]);
    clearMatches();
    expect(registry.size).toBe(0);
  });

  it("does nothing, rather than throwing, where the highlight API is missing", () => {
    const ranges = findRanges(log("<p>x</p>"), "x");
    expect(() => paintMatches(ranges, 0)).not.toThrow();
    expect(() => clearMatches()).not.toThrow();
  });
});
