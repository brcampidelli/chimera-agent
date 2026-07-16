import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Agents } from "@/components/Agents";
import { streamAgents, type AgentsStreamHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { AgentResult, AgentsBatch } from "@/lib/types";

vi.mock("@/lib/api", () => ({ streamAgents: vi.fn() }));

const mockStreamAgents = vi.mocked(streamAgents);

function result(over: Partial<AgentResult> = {}): AgentResult {
  return {
    index: 0,
    task: "add a test",
    success: true,
    attempts: 1,
    reverted: false,
    changed_paths: [],
    diffs: [],
    ...over,
  };
}

function batch(over: Partial<AgentsBatch> = {}): AgentsBatch {
  return { is_repo: true, merged: 0, conflicts: [], results: [result()], ...over };
}

/** Drive the mocked stream: fill in two tasks, click Run all, and hand the component the given
 *  `batch_done` payload — the same shape the real SSE delivers. */
async function runBatch(done: AgentsBatch, tasks: string[] = ["add a test", "fix the lint"]) {
  const user = userEvent.setup();
  mockStreamAgents.mockImplementation(async (_req, handlers: AgentsStreamHandlers) => {
    handlers.onStart?.({ tasks, workspace: "/repo", max_workers: 4 });
    handlers.onBatchDone?.(done);
  });
  renderWithProviders(<Agents />);

  const boxes = screen.getAllByPlaceholderText(/Describe a change the agent should make/);
  for (const [i, text] of tasks.entries()) await user.type(boxes[i], text);
  await user.click(screen.getByRole("button", { name: /Run all/ }));
  return user;
}

describe("Agents", () => {
  beforeEach(() => {
    mockStreamAgents.mockReset();
  });

  it("shows the empty state before any batch has run", () => {
    renderWithProviders(<Agents />);

    expect(screen.getByText("Add tasks above and Run all to start a parallel batch.")).toBeInTheDocument();
  });

  it("renders one card per submitted task", async () => {
    await runBatch(
      batch({
        results: [result({ index: 0, task: "add a test" }), result({ index: 1, task: "fix the lint" })],
      }),
    );

    expect(await screen.findByTitle("add a test")).toBeInTheDocument();
    expect(screen.getByTitle("fix the lint")).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
  });

  it("marks each task pass/fail from the batch_done payload", async () => {
    await runBatch(
      batch({
        results: [
          result({ index: 0, task: "add a test", success: true, attempts: 1 }),
          result({ index: 1, task: "fix the lint", success: false, attempts: 3 }),
        ],
      }),
    );

    const passed = (await screen.findByTitle("add a test")).closest("div.flex-col") as HTMLElement;
    const failed = screen.getByTitle("fix the lint").closest("div.flex-col") as HTMLElement;
    expect(within(passed).getByText("passed")).toBeInTheDocument();
    expect(within(passed).queryByText("failed")).not.toBeInTheDocument();
    expect(within(failed).getByText("failed")).toBeInTheDocument();
    expect(within(failed).getByText(/Attempts: 3/)).toBeInTheDocument();
  });

  it("renders cross-task conflicts prominently and marks the colliding file on its card", async () => {
    await runBatch(
      batch({
        merged: 1,
        conflicts: ["src/shared.ts"],
        results: [
          result({ index: 0, task: "add a test", changed_paths: ["src/shared.ts"] }),
          result({ index: 1, task: "fix the lint", changed_paths: ["src/other.ts"] }),
        ],
      }),
    );

    expect(await screen.findByText("Conflicts — left unmerged (1)")).toBeInTheDocument();
    expect(
      screen.getByText(/They were NOT merged back \(neither version silently wins\)/),
    ).toBeInTheDocument();
    // The conflicted path is badged on the card that touched it — and only there.
    const collided = screen.getByTitle("add a test").closest("div.flex-col") as HTMLElement;
    const clean = screen.getByTitle("fix the lint").closest("div.flex-col") as HTMLElement;
    expect(within(collided).getByText("conflict")).toBeInTheDocument();
    expect(within(clean).queryByText("conflict")).not.toBeInTheDocument();
  });

  it("says conflicts are absent when a git-repo batch merged cleanly", async () => {
    await runBatch(batch({ is_repo: true, merged: 2, conflicts: [] }));

    expect(await screen.findByText("No conflicts — every task's changes merged cleanly.")).toBeInTheDocument();
  });

  it("warns that a non-git batch ran WITHOUT isolation", async () => {
    await runBatch(batch({ is_repo: false }));

    expect(
      await screen.findByText(/tasks ran in-place WITHOUT isolation/),
    ).toBeInTheDocument();
  });

  it("never claims 'no conflicts' outside a git repo, where collisions cannot be detected", async () => {
    await runBatch(batch({ is_repo: false, conflicts: [] }));

    await screen.findByText(/WITHOUT isolation/);
    expect(screen.queryByText(/No conflicts/)).not.toBeInTheDocument();
  });

  it("surfaces a stream error instead of leaving the board silently empty", async () => {
    const user = userEvent.setup();
    mockStreamAgents.mockImplementation(async (_req, handlers: AgentsStreamHandlers) => {
      handlers.onError?.("HTTP 500");
    });
    renderWithProviders(<Agents />);

    await user.type(screen.getAllByPlaceholderText(/Describe a change/)[0], "add a test");
    await user.click(screen.getByRole("button", { name: /Run all/ }));

    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });

  it("only submits tasks that were actually filled in", async () => {
    const user = userEvent.setup();
    mockStreamAgents.mockImplementation(async () => {});
    renderWithProviders(<Agents />);

    // Row 2 is left blank on purpose.
    await user.type(screen.getAllByPlaceholderText(/Describe a change/)[0], "add a test");
    await user.type(screen.getAllByPlaceholderText(/exit 0/i)[0], "npm test");
    await user.click(screen.getByRole("button", { name: /Run all/ }));

    expect(mockStreamAgents).toHaveBeenCalledOnce();
    expect(mockStreamAgents.mock.calls[0][0].tasks).toEqual([{ task: "add a test", verify: "npm test" }]);
  });
});
