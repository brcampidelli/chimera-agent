import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContextCard } from "@/components/code/side/ContextCard";
import type { ConversationSide } from "@/lib/conversation-side";
import { renderWithProviders } from "@/test/utils";

const side: ConversationSide = {
  workspace: "",
  sessionId: "s1",
  posture: null,
  checkout: { kind: null, project: "", projectName: "" },
  live: null,
  peak: { used: 12_000, window: 200_000 },
  worksActive: 0,
  plan: null,
  approvalWaiting: false,
  instructions: ["AGENTS.md", "CLAUDE.md", "~/.claude/CLAUDE.md"],
};

vi.mock("@/lib/conversation-side", async (original) => ({
  ...(await original<typeof import("@/lib/conversation-side")>()),
  useConversationSide: () => currentSide,
}));

let currentSide: ConversationSide | null = side;

describe("ContextCard — names the instruction files the turn was given", () => {
  it("lists every file that was loaded, by name", () => {
    currentSide = side;
    renderWithProviders(<ContextCard />);
    const line = screen.getByTestId("side-context-instructions");
    expect(line).toHaveTextContent("AGENTS.md, CLAUDE.md, ~/.claude/CLAUDE.md");
  });

  it("shows no instruction line when no file was loaded", () => {
    currentSide = { ...side, instructions: [] };
    renderWithProviders(<ContextCard />);
    expect(screen.queryByTestId("side-context-instructions")).toBeNull();
    expect(screen.getByTestId("side-context")).toBeInTheDocument();
  });
});
