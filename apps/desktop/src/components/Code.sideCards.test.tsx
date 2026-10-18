import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { ContextCard } from "@/components/code/side/ContextCard";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, listCodeSessions, streamCodeTurn, type CodeApprovalEvent } from "@/lib/api";
import { AgentProvider } from "@/lib/agent-context";
import { ConversationSideProvider, useConversationSide } from "@/lib/conversation-side";
import { writeWorkspace } from "@/lib/workspace";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

const ASKED: CodeApprovalEvent = {
  id: "q-1",
  action: "write_file(path='notes.md')",
  reason: "write_file is restricted after this run consumed untrusted content",
  asked_at: Date.now() / 1000,
  wait_seconds: 300,
};

/**
 * The wire behind the side cards (C5): the Code screen and its conversation publish what the cards
 * read, and take it back when the screen goes. The cards are tested on their own with these facts
 * handed in; this is the test that the screen actually hands them over.
 */

function Probe() {
  const side = useConversationSide();
  return <output data-testid="probe">{JSON.stringify(side)}</output>;
}

function read(): Record<string, unknown> | null {
  return JSON.parse(screen.getByTestId("probe").textContent ?? "null") as Record<string, unknown> | null;
}

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <AgentProvider>
      <ConversationSideProvider>
        <button type="button" onClick={() => setOpen(false)}>
          leave
        </button>
        {open ? <Code /> : null}
        <Probe />
        <ContextCard />
      </ConversationSideProvider>
    </AgentProvider>
  );
}

describe("Code — the facts the side cards read", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([]);
    writeWorkspace("/repo");
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("publishes the folder, the posture, the conversation and its receipt's peak, and clears them on leaving", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ session: "s-new", context: [{ used: 41_000, window: 200_000 }], done: { context_peak_tokens: 52_000 } }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Harness />);

    await waitFor(() => expect(read()).toMatchObject({ workspace: "/repo", posture: { reach: "workspace", approval: "suspicious" } }));
    expect(read()).toMatchObject({ sessionId: null, live: null, peak: null });

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what is this?{Enter}");
    // The turn finished: no live frame, the receipt's peak with the window its frame reported.
    await waitFor(() =>
      expect(read()).toMatchObject({ sessionId: "s-new", live: null, peak: { used: 52_000, window: 200_000 } }),
    );
    expect(screen.getByRole("progressbar", { name: "Context 26%" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "leave" }));
    expect(read()).toBeNull();
  });

  it("publishes the running turn's live frame while it runs", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        context: [{ used: 30_000, window: 100_000 }],
        // A question parks the turn with its stream open, so the turn is still running when read.
        approval: ASKED,
        parked: true,
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Harness />);

    await user.type(await screen.findByPlaceholderText(/^Ask about this code/), "go{Enter}");
    await waitFor(() => expect(read()).toMatchObject({ live: { used: 30_000, window: 100_000 } }));
    expect(screen.getByText("this turn")).toBeInTheDocument();
  });
});
