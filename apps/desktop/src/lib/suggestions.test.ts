import { describe, expect, it } from "vitest";

import { DICTS } from "@/lib/i18n";
import { MAX_SUGGESTIONS, turnSuggestions, type TurnFacts } from "@/lib/suggestions";

/** The English dictionary with its `{name}` placeholders filled, as the screen renders it. */
function t(key: string, params?: Record<string, string | number>): string {
  let text = DICTS.en[key] ?? key;
  for (const [name, value] of Object.entries(params ?? {})) text = text.split(`{${name}}`).join(String(value));
  return text;
}

const NOTHING: TurnFacts = { todos: [], uncommitted: null };

/**
 * The next-step suggestions under an answer, read off facts of the turn (study 29, P4.5).
 *
 * Each suggestion is pinned to the fact it stands for, and to the absence of that fact: a chip that
 * appears without its reason is a guess presented as a next step, which is the thing a deterministic
 * suggestion exists not to be.
 */
describe("next-step suggestions", () => {
  it("offers nothing for a turn with no open fact", () => {
    expect(turnSuggestions(NOTHING, t)).toEqual([]);
  });

  it("offers the fix, carrying the failure, when the check failed", () => {
    const out = turnSuggestions({ ...NOTHING, fixText: "The check `pytest` failed…" }, t);
    expect(out).toEqual([{ kind: "fix", label: "Ask it to fix the failure", text: "The check `pytest` failed…" }]);
  });

  it("continues the first item of the agent's list that is not done", () => {
    const out = turnSuggestions(
      {
        ...NOTHING,
        todos: [
          { task: "read the parser", status: "done" },
          { task: "write the failing test", status: "doing" },
          { task: "fix the parser", status: "pending" },
        ],
      },
      t,
    );
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("continue");
    expect(out[0].label).toBe("Continue: write the failing test");
    expect(out[0].text).toContain("write the failing test");
  });

  it("offers no continuation when every item is done", () => {
    const out = turnSuggestions({ ...NOTHING, todos: [{ task: "all of it", status: "done" }] }, t);
    expect(out).toEqual([]);
  });

  it("cuts a long item on the chip and keeps all of it in the box", () => {
    const task = "x".repeat(200);
    const [item] = turnSuggestions({ ...NOTHING, todos: [{ task, status: "pending" }] }, t);
    expect(item.label.length).toBeLessThan(80);
    expect(item.text).toContain(task);
  });

  it("offers a commit for the turn's files the server says are still uncommitted", () => {
    // Which of the turn's files those are is decided by the server, next to the workspace and the
    // repository root (tests/test_the_commit_chip_asks_git_about_the_turns_own_files.py): the chip
    // names what it was told and matches nothing itself.
    const [commit] = turnSuggestions({ ...NOTHING, uncommitted: ["src/b.ts"] }, t);
    expect(commit.kind).toBe("commit");
    expect(commit.label).toBe("Commit b.ts");
    expect(commit.text).toContain("src/b.ts");
  });

  it("offers no commit when git has not said, or says the files are committed", () => {
    expect(turnSuggestions({ ...NOTHING, uncommitted: null }, t)).toEqual([]);
    expect(turnSuggestions({ ...NOTHING, uncommitted: [] }, t)).toEqual([]);
  });

  it("names two files on the chip and counts the rest", () => {
    const edited = ["a.ts", "b.ts", "c.ts", "d.ts"];
    const [commit] = turnSuggestions({ ...NOTHING, uncommitted: edited }, t);
    expect(commit.label).toBe("Commit a.ts, b.ts and 2 more");
    expect(commit.text).toContain("a.ts, b.ts, c.ts, d.ts");
  });

  it("never offers to commit what the check just rejected", () => {
    const out = turnSuggestions(
      { fixText: "fix it", todos: [], uncommitted: ["src/a.ts"] },
      t,
    );
    expect(out.map((s) => s.kind)).toEqual(["fix"]);
  });

  it("offers at most three, one of each kind, fix first", () => {
    const out = turnSuggestions(
      {
        fixText: "fix it",
        todos: [
          { task: "one", status: "pending" },
          { task: "two", status: "pending" },
        ],
        uncommitted: ["a.ts"],
      },
      t,
    );
    expect(out.length).toBeLessThanOrEqual(MAX_SUGGESTIONS);
    expect(out.map((s) => s.kind)).toEqual(["fix", "continue"]);
    const clean = turnSuggestions(
      { todos: [{ task: "one", status: "pending" }], uncommitted: ["a.ts"] },
      t,
    );
    expect(clean.map((s) => s.kind)).toEqual(["continue", "commit"]);
  });
});
