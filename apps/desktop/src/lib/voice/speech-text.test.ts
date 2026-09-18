import { describe, expect, it } from "vitest";

import { countSentences, firstSentences, plainForSpeech, readyCut, screenPartStart, speechLocale } from "@/lib/voice/speech-text";

describe("plainForSpeech", () => {
  it("replaces fenced code with the marker, keeps inline code and link labels, drops the marks", () => {
    const md = [
      "# Done",
      "",
      "I changed `login.py` — see [the diff](https://example.com/diff/1) and https://example.com/docs/x.",
      "",
      "```python",
      "def login(): ...",
      "```",
      "",
      "- **bold** item",
      "- _quiet_ item",
      "",
      "| a | b |",
      "|---|---|",
      "| 1 | 2 |",
    ].join("\n");
    const spoken = plainForSpeech(md, "(code)");
    expect(spoken).not.toContain("def login");
    expect(spoken).toContain("(code)");
    expect(spoken).toContain("login.py");
    expect(spoken).toContain("the diff");
    expect(spoken).not.toContain("https://");
    expect(spoken).toContain("example.com");
    expect(spoken).not.toContain("**");
    expect(spoken).not.toContain("# ");
    expect(spoken).toContain("bold item");
    expect(spoken).toContain("quiet item");
    expect(spoken).not.toContain("|---");
    expect(spoken.startsWith("Done")).toBe(true);
  });

  it("turns paragraph breaks into pauses and collapses whitespace", () => {
    expect(plainForSpeech("First.\n\n\nSecond   line\nthird")).toBe("First. Second line third");
    expect(plainForSpeech("   ")).toBe("");
  });
});

describe("speechLocale", () => {
  it("maps the app's languages to a voice tag and falls back to English", () => {
    expect(speechLocale("pt")).toBe("pt-BR");
    expect(speechLocale("ja")).toBe("ja-JP");
    expect(speechLocale("xx")).toBe("en-US");
  });
});

describe("cutting a growing answer into pieces a voice can read now", () => {
  it("cuts after a complete sentence and never mid-sentence", () => {
    const raw = "The login is fixed. The logout is next, and";
    expect(readyCut(raw, 0)).toBe("The login is fixed.".length);
    expect(readyCut("The login is fixed", 0)).toBe(0);
    expect(readyCut("Done.", 0)).toBe(0); // a period at the very end may still be "3." of "3.14"
    expect(readyCut("Done. ", 0)).toBe(5);
  });

  it("cuts at line ends, holds a list number, and lets closing marks finish a sentence", () => {
    expect(readyCut("## Plan\n1. Read the file", 0)).toBe("## Plan\n".length);
    expect(readyCut("1. Read", 0)).toBe(0);
    expect(readyCut("**Done.** Next", 0)).toBe("**Done.**".length);
    expect(readyCut('He said "stop." Then', 0)).toBe('He said "stop."'.length);
  });

  it("holds a fenced block until it closes, so its marker is spoken once", () => {
    const open = "Run this:\n```sh\nnpm test. Then\n";
    expect(readyCut(open, 0)).toBe("Run this:\n".length);
    const closed = open + "```\nDone. ";
    expect(readyCut(closed, 0)).toBe(closed.length - 1);
  });

  it("moves on from where it was", () => {
    const raw = "One. Two. Three";
    const first = readyCut(raw, 0);
    expect(raw.slice(0, first)).toBe("One. Two.");
    expect(readyCut(raw, first)).toBe(first);
    expect(readyCut(raw + ". ", first)).toBe(raw.length + 1);
  });

  it("finds the line that separates the spoken part from the screen part", () => {
    expect(screenPartStart("Gist here.\n\n---\n\n1. a\n2. b")).toBe("Gist here.\n\n".length);
    expect(screenPartStart("Gist here.\n***\nrest")).toBe("Gist here.\n".length);
    expect(screenPartStart("No rule here --- inline")).toBe(-1);
    expect(screenPartStart("a\n--\nb")).toBe(-1);
  });

  it("keeps the first sentences and says whether anything was left", () => {
    expect(firstSentences("One. Two. Three.", 2)).toEqual({ kept: "One. Two.", truncated: true });
    expect(firstSentences("One. Two.", 2)).toEqual({ kept: "One. Two.", truncated: false });
    expect(firstSentences("One. Two.", 0)).toEqual({ kept: "", truncated: true });
    expect(firstSentences("", 3)).toEqual({ kept: "", truncated: false });
  });

  it("counts sentences, at least one for anything at all", () => {
    expect(countSentences("")).toBe(0);
    expect(countSentences("Done")).toBe(1);
    expect(countSentences("One. Two! Three? Four… five.")).toBe(5);
  });
});
