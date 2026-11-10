import { describe, expect, it } from "vitest";

import {
  buildLineCommentsMessage,
  clearLineComments,
  codeSpan,
  editTurn,
  escapeText,
  forgetLineComments,
  formatLineComments,
  latestEditTurns,
  lineCommentId,
  readLineComments,
  removeLineComment,
  saveLineComment,
  type LineCommentLabels,
  type LineRef,
} from "@/lib/line-comments";

const labels: LineCommentLabels = {
  header: (n) => `Review (${n}):`,
  added: "added",
  removed: "removed",
  context: "context",
  snippet: "Snippet",
  comment: "Comment",
  fromEdit: (turn) => `edit of turn ${turn}`,
  fromGit: "uncommitted",
  stale: (turn) => `Possibly outdated (turn ${turn})`,
  blank: "(blank line)",
};

let counter = 0;
/** A conversation id no other test in this file shares, so the module-level map starts empty. */
function conversation(): string {
  counter += 1;
  return `conv-${counter}`;
}

function ref(line: number, overrides: Partial<LineRef> = {}): LineRef {
  return { origin: "git", file: "src/a.ts", side: "new", line, marker: "+", content: `const x${line} = 1;`, ...overrides };
}

describe("pending line comments", () => {
  it("keeps the comments in the order they were written", () => {
    const s = conversation();
    saveLineComment(s, ref(7), "seven");
    saveLineComment(s, ref(3), "three");
    expect(readLineComments(s).map((c) => c.text)).toEqual(["seven", "three"]);
  });

  it("replaces the comment on a line that already has one, rather than adding a second", () => {
    const s = conversation();
    saveLineComment(s, ref(4), "first");
    saveLineComment(s, ref(4), "  second  ");
    const items = readLineComments(s);
    expect(items).toHaveLength(1);
    expect(items[0]?.text).toBe("second");
  });

  it("removes the comment on a line when it is saved empty", () => {
    const s = conversation();
    saveLineComment(s, ref(4), "note");
    saveLineComment(s, ref(4), "   ");
    expect(readLineComments(s)).toEqual([]);
  });

  it("keeps the same line of two different diffs apart, and the two sides of one diff apart", () => {
    const s = conversation();
    saveLineComment(s, ref(2), "from git");
    saveLineComment(s, ref(2, { origin: "edit:0:0" }), "from the edit");
    saveLineComment(s, ref(2, { side: "old", marker: "-" }), "removed side");
    expect(readLineComments(s)).toHaveLength(3);
    expect(lineCommentId(ref(2))).not.toBe(lineCommentId(ref(2, { origin: "edit:0:0" })));
    expect(lineCommentId(ref(2, { side: "old" }))).not.toBe(lineCommentId(ref(2)));
  });

  it("keeps one conversation's comments out of another's", () => {
    const a = conversation();
    const b = conversation();
    saveLineComment(a, ref(1), "only in a");
    expect(readLineComments(b)).toEqual([]);
    expect(readLineComments(a)).toHaveLength(1);
  });

  it("persists the comments to storage so they survive a reload", () => {
    const s = conversation();
    saveLineComment(s, ref(9), "kept");
    const raw = localStorage.getItem(`chimera:code:line-comments:${s}`);
    expect(JSON.parse(raw ?? "[]")).toEqual([expect.objectContaining({ text: "kept", line: 9 })]);
  });

  it("removes one comment by id and leaves the rest", () => {
    const s = conversation();
    saveLineComment(s, ref(1), "one");
    saveLineComment(s, ref(2), "two");
    removeLineComment(s, lineCommentId(ref(1)));
    expect(readLineComments(s).map((c) => c.text)).toEqual(["two"]);
  });

  it("clears every comment of a conversation", () => {
    const s = conversation();
    saveLineComment(s, ref(1), "one");
    clearLineComments(s);
    expect(readLineComments(s)).toEqual([]);
    expect(localStorage.getItem(`chimera:code:line-comments:${s}`)).toBeNull();
  });

  it("forgets a deleted conversation's comments, from memory and storage", () => {
    const s = conversation();
    saveLineComment(s, ref(1), "gone soon");
    forgetLineComments(s);
    expect(readLineComments(s)).toEqual([]);
    expect(localStorage.getItem(`chimera:code:line-comments:${s}`)).toBeNull();
  });
});

describe("the message the comments become", () => {
  it("formats one block per comment: where, which diff, what it said, and the comment", () => {
    const s = conversation();
    saveLineComment(s, ref(5, { content: "  foo()  " }), "rename");
    saveLineComment(s, ref(2, { side: "old", marker: "-", content: "bar" }), "keep?");
    saveLineComment(s, { origin: "git", file: "src/b.ts", side: "new", line: 1, marker: " ", content: "const y = 2;" }, "multi\nline");

    const message = formatLineComments(readLineComments(s), labels);

    expect(message).toBe(
      [
        "Review (3):",
        "",
        "1. src/a.ts:2 (removed; uncommitted)",
        "   Snippet: `bar`",
        "   Comment: keep?",
        "2. src/a.ts:5 (added; uncommitted)",
        "   Snippet: `foo()`",
        "   Comment: rename",
        "3. src/b.ts:1 (context; uncommitted)",
        "   Snippet: `const y = 2;`",
        "   Comment: multi",
        "   line",
      ].join("\n"),
    );
  });

  it("names the edit of the turn a comment came from", () => {
    const message = formatLineComments(
      [{ ...ref(1), origin: "edit:2:0", id: "x", text: "again" }],
      labels,
    );
    expect(message).toContain("src/a.ts:1 (added; edit of turn 3)");
  });

  it("annotates a comment that a later edit of the file may have outdated, without dropping it", () => {
    const exchanges = [{ edits: [{ path: "src/a.ts" }] }, { edits: [{ path: "src/a.ts" }] }];
    const latest = latestEditTurns(exchanges);
    const message = formatLineComments([{ ...ref(1), origin: "edit:0:0", id: "x", text: "old one" }], labels, latest);
    expect(message).toContain("Comment: old one");
    expect(message).toContain("Possibly outdated (turn 1)");
  });

  it("does not annotate the latest edit of a file", () => {
    const latest = latestEditTurns([{ edits: [{ path: "src/a.ts" }] }]);
    const message = formatLineComments([{ ...ref(1), origin: "edit:0:0", id: "x", text: "fresh" }], labels, latest);
    expect(message).not.toContain("Possibly outdated");
  });

  it("reads the turn of an edit origin and ignores the rest", () => {
    expect(editTurn("edit:0:0")).toBe(1);
    expect(editTurn("edit:4:2")).toBe(5);
    expect(editTurn("git")).toBeNull();
  });

  it("wraps snippets and comments so backticks and fences cannot break the message", () => {
    const message = formatLineComments(
      [{ ...ref(1, { content: "use `x` here" }), id: "x", text: "```ts fence```" }],
      labels,
    );
    // The snippet is in a code span one backtick longer than the longest run, the comment is escaped.
    expect(message).toContain("Snippet: ``use `x` here``");
    expect(message).toContain("Comment: \\`\\`\\`ts fence\\`\\`\\`");
  });

  it("builds the message without clearing, so the caller clears only once it is accepted", () => {
    const s = conversation();
    saveLineComment(s, ref(1), "only");
    const message = buildLineCommentsMessage(s, labels, {});
    expect(message).toContain("Comment: only");
    expect(readLineComments(s)).toHaveLength(1);
    clearLineComments(s);
    expect(buildLineCommentsMessage(s, labels, {})).toBeNull();
  });
});

describe("escaping free text", () => {
  it("escapes a backslash and a backtick, so a backslash cannot escape the escape", () => {
    // Input is a backslash followed by a backtick: both are escaped, the backslash first.
    expect(escapeText("\\`")).toBe("\\\\\\`");
    expect(escapeText("plain")).toBe("plain");
  });

  it("writes a blank line's snippet as a label, not as empty backticks", () => {
    const message = formatLineComments(
      [{ ...ref(1, { content: "   " }), id: "x", text: "add a line" }],
      labels,
    );
    expect(message).toContain("Snippet: (blank line)");
    expect(message).not.toContain("``");
  });

  it("states the possible staleness without claiming the file was edited again", () => {
    const latest = latestEditTurns([{ edits: [{ path: "src/a.ts" }] }, { edits: [{ path: "src/a.ts" }] }]);
    const message = formatLineComments([{ ...ref(1), origin: "edit:0:0", id: "x", text: "old" }], labels, latest);
    expect(message).toContain("Possibly outdated (turn 1)");
    expect(message).not.toMatch(/edited again|editado de novo/);
  });
});

describe("code spans", () => {
  it("uses a longer delimiter than any backtick run inside", () => {
    expect(codeSpan("plain")).toBe("`plain`");
    expect(codeSpan("a `b` c")).toBe("``a `b` c``");
    expect(codeSpan("```")).toBe("```` ``` ````");
  });
});
