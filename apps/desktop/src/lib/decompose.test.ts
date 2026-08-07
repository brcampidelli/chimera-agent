import { describe, expect, it } from "vitest";
import { decompose } from "@/lib/decompose";

/**
 * The asymmetry these tests hold: a missed split costs the user a numbered list, while a false one
 * interrupts an ordinary message with a card about git worktrees. So everything ambiguous resolves
 * to "one job".
 */
describe("decompose", () => {
  it("reads a numbered list as separate jobs", () => {
    expect(decompose("1. add a test for the parser\n2. fix the lint errors")).toEqual([
      "add a test for the parser",
      "fix the lint errors",
    ]);
  });

  it("reads bullets the same way, whichever marker", () => {
    expect(decompose("- rename the module\n* update the imports\n• fix the docs link")).toHaveLength(3);
  });

  it("keeps prose as one job even when it lists things in a sentence", () => {
    // The commonest false positive there could be: a request that mentions several things is still
    // one request, and splitting it would run three agents over one intent.
    expect(decompose("rename the module, update the imports and fix the docs")).toEqual([]);
  });

  it("keeps a single list item as one job", () => {
    expect(decompose("Please do this:\n- rename the module")).toEqual([]);
  });

  it("ignores a bare marker left over from typing", () => {
    expect(decompose("1. add a test for the parser\n2. ")).toEqual([]);
  });

  it("refuses to propose more than the batch can actually run", () => {
    // Nine items with an eight-worker ceiling would be proposing something we cannot do.
    const nine = Array.from({ length: 9 }, (_, i) => `${i + 1}. task number ${i + 1} here`).join("\n");
    expect(decompose(nine)).toEqual([]);
  });

  it("drops the marker but keeps the text intact", () => {
    expect(decompose("1) first job with words\n2) second job with words")[0]).toBe(
      "first job with words",
    );
  });
});
