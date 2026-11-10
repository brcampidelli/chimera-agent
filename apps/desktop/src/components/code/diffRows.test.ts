import { describe, expect, it } from "vitest";

import { parseDiffRows } from "@/components/code/diffRows";

describe("numbering the lines of a diff", () => {
  it("numbers context and added lines on the new side and removed lines on the old side", () => {
    const patch = ["--- a/x.ts", "+++ b/x.ts", "@@ -3,3 +3,4 @@", " a", "-b", "+B", "+C", " d"].join("\n");
    const rows = parseDiffRows(patch);

    expect(rows.slice(0, 3).every((row) => row.ref === undefined)).toBe(true);
    expect(rows[3]?.ref).toEqual({ side: "new", line: 3, marker: " ", content: "a" });
    expect(rows[4]?.ref).toEqual({ side: "old", line: 4, marker: "-", content: "b" });
    expect(rows[5]?.ref).toEqual({ side: "new", line: 4, marker: "+", content: "B" });
    expect(rows[6]?.ref).toEqual({ side: "new", line: 5, marker: "+", content: "C" });
    expect(rows[7]?.ref).toEqual({ side: "new", line: 6, marker: " ", content: "d" });
  });

  it("restarts the count at each hunk header", () => {
    const patch = ["@@ -1 +1 @@", "+one", "@@ -10 +20 @@", "+twenty"].join("\n");
    const rows = parseDiffRows(patch);
    expect(rows[1]?.ref?.line).toBe(1);
    expect(rows[3]?.ref?.line).toBe(20);
  });

  it("does not make the hunk header, the no-newline marker or the trailing empty line commentable", () => {
    const patch = ["@@ -1 +1 @@", "-old", "+new", "\\ No newline at end of file", ""].join("\n");
    const rows = parseDiffRows(patch);
    expect(rows[3]?.ref).toBeUndefined();
    expect(rows[4]?.ref).toBeUndefined();
  });
});
