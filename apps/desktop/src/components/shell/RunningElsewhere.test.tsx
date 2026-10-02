import { useState } from "react";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { RunningElsewhere } from "@/components/shell/RunningElsewhere";
import { AgentProvider } from "@/lib/agent-context";
import { listRunningTurns, stopCodeTurn, streamCodeTurn, type RunningTurn } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/components/ServerBadge", () => ({ ServerBadge: () => null }));

/**
 * Several conversations running at once, from the status bar.
 *
 * Found reading the code on 2026-09-30 (R4 and R5 of the review). The bar reads one agent state, and
 * every conversation wrote into it for as long as its handlers lived: start a turn in A, switch to B
 * and send, and when A finished it set "done, not busy", so B's Stop vanished while B ran. And the
 * other running turns were invisible from the bar: with three working, it described one and offered
 * one Stop.
 */
function turn(id: string, workspace: string, message: string): RunningTurn {
  return { turn_id: id, session_id: `s-${id}`, workspace, message, started_at: 1, live_since: 0, transcript_saved: false };
}

describe("the turns running in other conversations", () => {
  beforeEach(() => {
    vi.mocked(listRunningTurns).mockReset();
    vi.mocked(stopCodeTurn).mockClear();
  });

  it("lists every running turn but the one on screen, and stops the one you pick", async () => {
    vi.mocked(listRunningTurns).mockResolvedValue([
      turn("t1", "/p/shop", "fix the cart"),
      turn("t2", "/p/blog", "resize images"),
    ]);
    const user = userEvent.setup();
    renderWithProviders(<RunningElsewhere current="t1" />);

    await user.click(await screen.findByRole("button", { name: /1 more turn/i }));
    expect(await screen.findByText("resize images")).toBeInTheDocument();
    expect(screen.queryByText("fix the cart")).not.toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: /Stop this turn: resize images/i }));

    await waitFor(() => expect(stopCodeTurn).toHaveBeenCalledWith("t2"));
  });

  it("renders nothing when the only running turn is the one on screen", async () => {
    vi.mocked(listRunningTurns).mockResolvedValue([turn("t1", "/p/shop", "fix the cart")]);
    const { container } = renderWithProviders(<RunningElsewhere current="t1" />);

    await waitFor(() => expect(listRunningTurns).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe("switching conversations while a turn runs", () => {
  it("keeps the Stop of the conversation on screen when the one you left finishes", async () => {
    const handlers: Array<{ onSession?: (id: string, t?: string) => void; onDone?: (d: unknown) => void }> = [];
    vi.mocked(listRunningTurns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(async (_req, h) => {
      handlers.push(h as never);
      (h as { onSession?: (id: string, t?: string) => void }).onSession?.(`s${handlers.length}`, `t${handlers.length}`);
      await new Promise<void>(() => {});
    });

    function Harness() {
      const [which, setWhich] = useState("a");
      return (
        <AgentProvider>
          <AgentStatusBar />
          <button type="button" onClick={() => setWhich("b")}>
            switch
          </button>
          <Conversation
            key={which}
            workspace="/proj"
            openFile={null}
            resumeSession={undefined as never}
            posture={{ reach: "workspace" as never, approval: "ask" as never }}
            profile={"balanced" as never}
            onHandOff={() => {}}
            onBatch={() => {}}
            onEdited={() => {}}
            busyElsewhere={false}
            controls={null}
            onOpenFile={() => {}}
          />
        </AgentProvider>
      );
    }
    const user = userEvent.setup();
    renderWithProviders(<Harness />);

    await user.type(screen.getAllByRole("textbox")[0], "first task{Enter}");
    await waitFor(() => expect(handlers).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "switch" }));
    await user.type(screen.getAllByRole("textbox")[0], "second task{Enter}");
    await waitFor(() => expect(handlers).toHaveLength(2));
    // The STATUS BAR's Stop, not the composer's: the composer of the conversation on screen has its own,
    // which is why a first version of this test passed with the defect in place.
    const stopButtons = () => within(screen.getByRole("contentinfo")).queryAllByRole("button", { name: /^stop$/i });
    expect(stopButtons().length).toBeGreaterThan(0);

    // The conversation that was left finishes now.
    await act(async () => {
      handlers[0].onDone?.({
        answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "m",
        prompt_tokens: 1, completion_tokens: 1, usd: 0, tainted: false, memory_facts_used: 0,
        memory_layer: "", fused: false,
      });
    });

    expect(stopButtons().length).toBeGreaterThan(0);
  });
});
