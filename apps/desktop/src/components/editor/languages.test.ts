import { describe, expect, it } from "vitest";

import { extensionOf, hasLanguage, languageFor } from "@/components/editor/languages";

/**
 * The grammar table is a lookup, so the tests here are about the two decisions inside it: what
 * counts as an extension, and what happens when nothing matches.
 */

describe("extensionOf", () => {
  it("reads the extension, not the path", () => {
    expect(extensionOf("src/lib/api.ts")).toBe("ts");
    expect(extensionOf("C:\\work\\main.PY")).toBe("py");
  });

  it("uses the LAST dot", () => {
    // `api.test.ts` is TypeScript. Splitting on the first dot would call it "test".
    expect(extensionOf("src/lib/api.test.ts")).toBe("ts");
    expect(extensionOf("chart.min.js")).toBe("js");
  });

  it("treats a dotfile as having no extension", () => {
    // ".gitignore" is a NAME that begins with a dot. Reading "gitignore" as an extension would put
    // every dotfile in one imaginary language and match none of them.
    expect(extensionOf(".gitignore")).toBe("");
    expect(extensionOf("src/.env")).toBe("");
  });

  it("survives a path with no dot at all", () => {
    expect(extensionOf("Makefile")).toBe("");
    expect(extensionOf("")).toBe("");
  });

  it("is not confused by a dot in a directory name", () => {
    // The extension belongs to the file. Scanning the whole path would find "d" here.
    expect(extensionOf("my.dir/Makefile")).toBe("");
  });
});

describe("languageFor", () => {
  it("returns a grammar for the languages this project is made of", () => {
    for (const path of ["a.py", "a.ts", "a.tsx", "a.js", "a.json", "a.md", "a.css", "a.html"]) {
      expect(hasLanguage(path), path).toBe(true);
      // An extension is an array or an object; either way it must not be the empty array that
      // means "no grammar".
      expect(languageFor(path), path).not.toEqual([]);
    }
  });

  it("gives an unknown file NO grammar rather than a guessed one", () => {
    // The chat viewer auto-detects, which is right for a snippet in a message and wrong for a file
    // you are editing: a confidently wrong grammar colours the wrong words and folds in the wrong
    // places, and looks authoritative doing it. Plain is honest.
    expect(hasLanguage("notes.rtf")).toBe(false);
    expect(languageFor("notes.rtf")).toEqual([]);
    expect(languageFor("Makefile")).toEqual([]);
    expect(languageFor(".gitignore")).toEqual([]);
  });
});
