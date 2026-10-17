import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  streamAgents,
  streamSessionLive,
  type AgentsStreamHandlers,
} from "@/lib/api";
import { useAgentBatch } from "@/lib/agent-batch";
import type { AgentBatchNote, AgentsBatch } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A parallel batch's result in the conversation that started it (2026-10-17).
 *
 * The owner asked where the result of the batch appears, and it appeared nowhere but the board: each
 * task's answer was dropped and nothing reached the conversation. A finished batch is now recorded in
 * it, read back with it, and drawn after the exchange that confirmed it.
 */
const NOTE: AgentBatchNote = {
  tasks: [
    { index: 0, task: "map the architecture", success: true, ending: "no_op", answer: "Three **layers**: api, core, ui.", changed_paths: [], error: "" },
    { index: 1, task: "fix the build", success: true, ending: "success", answer: "Pinned vite.", changed_paths: ["package.json"], error: "" },
  ],
  conflicts: ["src/shared.ts"],
  merged: 1,
  is_repo: true,
  at: "2026-10-17T10:00:00Z",
};

const EXCHANGE = { you: "map it and fix the build", answer: "Running these as a batch.", tools: [], edits: [] };

function mount() {
  return renderWithProviders(
    <>
      <Conversation
        workspace="/proj"
        openFile={null}
        resumeSession="s1"
        posture={{ reach: "workspace" as never, approval: "ask" as never }}
        profile={"balanced" as never}
        onHandOff={() => {}}
        onBatch={() => {}}
        onEdited={() => {}}
        busyElsewhere={false}
        controls={null}
      />
      <StartBatch />
    </>,
  );
}

/** Starts a batch for conversation s1 the way the Code screen does on a confirmation. */
function StartBatch() {
  const { start } = useAgentBatch();
  return (
    <button
      type="button"
      onClick={() =>
        start({
          tasks: ["map the architecture", "fix the build"],
          workspace: "/proj",
          sessionId: "s1",
          posture: { reach: "workspace", approval: "suspicious" },
          profile: "balanced",
        })
      }
    >
      start batch
    </button>
  );
}

describe("a batch in its conversation", () => {
  beforeEach(() => {
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    vi.mocked(streamAgents).mockReset();
    localStorage.clear();
  });

  it("draws a recorded batch after the exchange that started it, in the agents' own words", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [{ ...EXCHANGE, done: null, verified: null, batches: [NOTE] }] });
    mount();

    const record = await screen.findByRole("region", { name: "Parallel batch: 2 tasks" });
    // Rendered as markdown, like an answer.
    expect(within(record).getByText("layers").tagName).toBe("STRONG");
    expect(within(record).getByText("answered, no edits")).toBeInTheDocument();
    expect(within(record).getByText("passed")).toBeInTheDocument();
    expect(within(record).getByText(/package\.json/)).toBeInTheDocument();
    expect(within(record).getByText("src/shared.ts")).toBeInTheDocument();
    // Not merged into the turn's own answer, which reads as it did.
    expect(screen.getByText("Running these as a batch.")).toBeInTheDocument();
  });

  it("reads an old conversation, with no batch recorded, exactly as before", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [{ ...EXCHANGE, done: null, verified: null }] });
    mount();

    await screen.findByText("Running these as a batch.");
    expect(screen.queryByRole("region", { name: /Parallel batch/ })).not.toBeInTheDocument();
  });

  it("adds the batch to the conversation the moment it ends, and names the conversation to the server", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [{ ...EXCHANGE, done: null, verified: null }] });
    let handlers: AgentsStreamHandlers | null = null;
    vi.mocked(streamAgents).mockImplementation((_req, h) => {
      handlers = h;
      h.onBatchId?.("b1");
      return new Promise<void>(() => {});
    });
    mount();
    await screen.findByText("Running these as a batch.");

    await userEvent.click(screen.getByRole("button", { name: "start batch" }));
    expect(vi.mocked(streamAgents).mock.calls[0][0]).toMatchObject({ session_id: "s1" });
    expect(screen.queryByRole("region", { name: /Parallel batch/ })).not.toBeInTheDocument();

    const done: AgentsBatch = {
      is_repo: true,
      merged: 1,
      conflicts: [],
      results: [
        { index: 0, task: "map the architecture", success: true, attempts: 1, reverted: false, changed_paths: [], diffs: [], error: "", answer: "Three layers.", ending: "no_op" },
        { index: 1, task: "fix the build", success: false, attempts: 3, reverted: true, changed_paths: [], diffs: [], error: "", answer: "", ending: "exhausted" },
      ],
    };
    act(() => (handlers as AgentsStreamHandlers | null)?.onBatchDone?.(done));

    const record = await screen.findByRole("region", { name: "Parallel batch: 2 tasks" });
    expect(within(record).getByText("Three layers.")).toBeInTheDocument();
    expect(within(record).getByText("out of attempts")).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: /Parallel batch/ })).toHaveLength(1);
  });
});
