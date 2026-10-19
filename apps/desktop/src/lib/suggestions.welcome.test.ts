import { describe, expect, it } from "vitest";

import { welcomeSuggestions } from "@/lib/suggestions";

const keys = (facts: Parameters<typeof welcomeSuggestions>[0]) => welcomeSuggestions(facts).map((s) => s.key);

describe("the welcome's first asks", () => {
  it("is about the file when one is open, whatever git says", () => {
    expect(keys({ openFile: true, isRepo: true, changedFiles: 3 })).toEqual(["empty.file"]);
  });

  it("offers to set up a folder git says is not a project, and still lets it be explained", () => {
    expect(keys({ openFile: false, isRepo: false, changedFiles: 0 })).toEqual(["empty.folder", "one"]);
  });

  it("offers to review changes only when git reports some", () => {
    expect(keys({ openFile: false, isRepo: true, changedFiles: 2 })).toEqual(["empty.git", "empty.tests", "one"]);
    expect(keys({ openFile: false, isRepo: true, changedFiles: 0 })).toEqual(["empty.tests", "one"]);
  });

  it("falls back to explaining the project when nothing is known", () => {
    expect(keys({ openFile: false, isRepo: null, changedFiles: 0 })).toEqual(["one"]);
  });
});
