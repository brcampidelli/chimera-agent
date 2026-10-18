import { describe, expect, it } from "vitest";

import { hunks, lineDiff, linesOf, proposalOf, replaceLines, sameText, wholeLines } from "@/lib/editorSelection";

const DOC = "one\ntwo\nthree\nfour\n";

describe("wholeLines", () => {
  it("takes every line the selection touches, whole", () => {
    // From inside "two" to inside "three".
    expect(wholeLines(DOC, 5, 10, "f")).toEqual({ path: "f", startLine: 2, endLine: 3, text: "two\nthree" });
  });

  it("does not take the next line when a drag stopped at its first column", () => {
    // "two\n" selected through its line break: line 3 is not part of it.
    expect(wholeLines(DOC, 4, 8, "f")).toEqual({ path: "f", startLine: 2, endLine: 2, text: "two" });
  });

  it("reads a backwards selection the same way, and an empty one as nothing", () => {
    expect(wholeLines(DOC, 10, 5, "f")?.startLine).toBe(2);
    expect(wholeLines(DOC, 5, 5, "f")).toBeNull();
  });
});

describe("replaceLines / linesOf", () => {
  it("replaces exactly the range, keeping everything around it", () => {
    expect(replaceLines(DOC, 2, 3, "TWO")).toBe("one\nTWO\nfour\n");
    expect(replaceLines(DOC, 1, 1, "a\nb")).toBe("a\nb\ntwo\nthree\nfour\n");
    expect(linesOf(DOC, 3, 4)).toBe("three\nfour");
  });
});

describe("proposalOf", () => {
  it("takes the first fenced block, with or without a language", () => {
    expect(proposalOf("Sure:\n```python\nx = 2\n```\nDone.")).toBe("x = 2");
    expect(proposalOf("```\na\nb\n```")).toBe("a\nb");
  });

  it("finds nothing in prose rather than guessing where the code is", () => {
    expect(proposalOf("Change x to 2.")).toBeNull();
  });
});

describe("lineDiff", () => {
  it("marks only what changed", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "b" },
      { kind: "add", text: "B" },
      { kind: "same", text: "c" },
    ]);
  });

  it("aligns an insertion instead of rewriting everything after it", () => {
    const diff = lineDiff("a\nc\nd", "a\nb\nc\nd");
    expect(diff.filter((l) => l.kind !== "same")).toEqual([{ kind: "add", text: "b" }]);
  });

  it("folds long unchanged stretches between changes", () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join("\n");
    const after = before.replace("l10", "L10");
    const shown = hunks(lineDiff(before, after), 1);
    // One line of context each side; the folded stretches at either end leave no marker behind.
    expect(shown).toEqual([
      { kind: "same", text: "l9" },
      { kind: "del", text: "l10" },
      { kind: "add", text: "L10" },
      { kind: "same", text: "l11" },
    ]);
    // Two changes far apart keep a marker between them, where lines were skipped.
    const two = hunks(lineDiff(before, after.replace("l2\n", "L2\n")), 1);
    expect(two.filter((l) => l === null)).toHaveLength(1);
  });
});

describe("sameText", () => {
  it("treats a CRLF file and its LF reading as the same text", () => {
    expect(sameText("a\r\nb", "a\nb")).toBe(true);
    expect(sameText("a\nb", "a\nc")).toBe(false);
  });
});
