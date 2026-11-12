import { describe, expect, it } from "vitest";

import { readInstructions, type TurnContextFacts } from "@/lib/conversation-side";

const receipt = (instruction_sources?: { path: string; truncated: boolean }[]): TurnContextFacts => ({
  done: { context_peak_tokens: 1000, ...(instruction_sources ? { instruction_sources } : {}) },
});

describe("readInstructions — the files the newest receipt says the turn was given", () => {
  it("lists the paths of the newest receipt that carries them, in load order", () => {
    const turns = [
      receipt([{ path: "AGENTS.md", truncated: false }]),
      receipt([
        { path: "AGENTS.md", truncated: false },
        { path: "CLAUDE.md", truncated: true },
      ]),
    ];
    expect(readInstructions(turns)).toEqual(["AGENTS.md", "CLAUDE.md"]);
  });

  it("is empty with no turns, a running turn, or a receipt that predates the field", () => {
    expect(readInstructions([])).toEqual([]);
    expect(readInstructions([{ done: null }])).toEqual([]);
    expect(readInstructions([receipt()])).toEqual([]);
  });

  it("reads an empty list as a turn that was given no instruction file", () => {
    expect(readInstructions([receipt([])])).toEqual([]);
  });
});
