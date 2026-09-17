import { describe, expect, it } from "vitest";

import { plainForSpeech, speechLocale } from "@/lib/voice/speech-text";

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
