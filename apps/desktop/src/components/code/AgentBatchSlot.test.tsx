import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { AgentBatchSlot, useStartAgentBatch } from "@/components/code/AgentBatchSlot";
import { AppShell } from "@/components/shell/AppShell";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider } from "@/lib/agent-context";
import { cancelAgents, streamAgents, type AgentsStreamHandlers } from "@/lib/api";
import { STORAGE_KEY } from "@/lib/layout/store";
import type { AgentResult, AgentsBatch } from "@/lib/types";
import { AGENT_AFTER_A_TURN } from "@/test/agent";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
  getResources: vi.fn(async () => {
    throw new Error("not in this test");
  }),
  listJobs: vi.fn(async () => ({ jobs: [] })),
  streamAgents: vi.fn(),
  cancelAgents: vi.fn(),
}));

const mockStream = vi.mocked(streamAgents);
const mockCancel = vi.mocked(cancelAgents);

/**
 * The owner's request of 2026-10-17: the batch board ("Agent Manager") minimises, closes and moves like
 * the other panels. What these tests hold it to is the half that is not visible: hiding the board must
 * never stop the batch. Only Stop and Stop all stop anything.
 */
const TASKS = ["map the architecture", "explain the build"];

let handlers: AgentsStreamHandlers | null = null;

function Starter() {
  const start = useStartAgentBatch();
  return (
    <button
      type="button"
      onClick={() =>
        start({ tasks: TASKS, workspace: "/repo", posture: { reach: "workspace", approval: "suspicious" }, profile: "balanced" })
      }
    >
      confirm batch
    </button>
  );
}

function mount() {
  return renderWithProviders(
    <ToastProvider>
      <AgentProvider value={AGENT_AFTER_A_TURN}>
        <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
          <Starter />
          <AgentBatchSlot />
        </AppShell>
      </AgentProvider>
    </ToastProvider>,
  );
}

async function startBatch() {
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "confirm batch" }));
  await screen.findByTitle("map the architecture");
  return user;
}

function result(index: number, success: boolean): AgentResult {
  return { index, task: TASKS[index], success, attempts: 1, reverted: false, changed_paths: [], diffs: [], error: "", answer: "", ending: "success" };
}

function finish(results: AgentResult[]) {
  const done: AgentsBatch = { is_repo: true, merged: 0, conflicts: [], results };
  act(() => handlers?.onBatchDone?.(done));
}

const board = () => screen.queryByRole("region", { name: "Agent Manager" });
const right = () => screen.getByRole("region", { name: "Right panel" });

describe("the batch board as a panel", () => {
  beforeEach(() => {
    localStorage.clear();
    handlers = null;
    mockStream.mockReset();
    mockCancel.mockReset();
    mockCancel.mockResolvedValue({ ok: true, cancelled: 1 });
    mockStream.mockImplementation((_req, h) => {
      handlers = h;
      h.onBatchId?.("b1");
      h.onStart?.({ tasks: TASKS, workspace: "/repo", max_workers: 2 });
      return new Promise<void>(() => {}); // in flight
    });
  });

  it("minimises to its header, which keeps a summary, and the batch keeps running", async () => {
    const user = await startBatch();

    await user.click(screen.getByRole("button", { name: "Minimize Agent Manager" }));

    const frame = board() as HTMLElement;
    expect(within(frame).queryByTitle("map the architecture")).not.toBeInTheDocument();
    expect(frame.querySelector("[data-panel-summary]")).toHaveTextContent("2 running");
    expect(mockCancel).not.toHaveBeenCalled();
    expect(mockStream).toHaveBeenCalledOnce();

    // The batch went on while it was minimised: its results are there when it is opened again.
    finish([result(0, true), result(1, false)]);
    expect(frame.querySelector("[data-panel-summary]")).toHaveTextContent("1/2 passed · 1 failed");
    await user.click(screen.getByRole("button", { name: "Expand Agent Manager" }));
    expect(within(board() as HTMLElement).getByTitle("explain the build")).toBeInTheDocument();
    expect(within(board() as HTMLElement).getByText("failed")).toBeInTheDocument();
  });

  it("closes to a chip that brings back the same cards, without stopping or restarting anything", async () => {
    const user = await startBatch();
    act(() => handlers?.onEvent?.({ kind: "attempt", text: "attempt 1/3", index: 1 }));

    await user.click(screen.getByRole("button", { name: "Close Agent Manager" }));

    expect(board()).not.toBeInTheDocument();
    const chip = screen.getByRole("button", { name: "Show the agent batch: 2 running" });
    expect(chip).toHaveTextContent("Batch: 2 running");
    // Running, it cannot be dismissed: that would be a batch with nothing on screen and no Stop.
    expect(screen.queryByRole("button", { name: "Dismiss the batch results" })).not.toBeInTheDocument();
    expect(mockCancel).not.toHaveBeenCalled();

    await user.click(chip);

    const back = board() as HTMLElement;
    expect(within(back).getByTitle("map the architecture")).toBeInTheDocument();
    expect(within(back).getByText("attempt 1/3")).toBeInTheDocument();
    expect(mockStream).toHaveBeenCalledOnce();
  });

  it("keeps a finished batch's results behind the chip until they are dismissed", async () => {
    const user = await startBatch();
    finish([result(0, true), result(1, true)]);
    await user.click(screen.getByRole("button", { name: "Close Agent Manager" }));

    expect(screen.getByRole("button", { name: "Show the agent batch: 2/2 passed" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss the batch results" }));

    expect(screen.queryByRole("button", { name: /Show the agent batch/ })).not.toBeInTheDocument();
    expect(board()).not.toBeInTheDocument();
  });

  it("moves to the right panel and back home, keeping its live state and its results", async () => {
    const user = await startBatch();
    act(() => handlers?.onEvent?.({ kind: "attempt", text: "attempt 2/3", index: 0 }));

    await user.click(screen.getByRole("button", { name: "Move Agent Manager" }));
    await user.click(await screen.findByRole("menuitem", { name: "To the right panel" }));

    const docked = within(right()).getByRole("region", { name: "Agent Manager" });
    expect(within(docked).getByText("attempt 2/3")).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "Agent Manager" })).toHaveLength(1);

    // The batch ends while the board is docked.
    finish([result(0, true), result(1, false)]);
    expect(within(docked).getByText("passed")).toBeInTheDocument();

    await user.click(within(docked).getByRole("button", { name: "Move Agent Manager" }));
    await user.click(await screen.findByRole("menuitem", { name: "To the main column" }));

    expect(within(right()).queryByRole("region", { name: "Agent Manager" })).not.toBeInTheDocument();
    const home = board() as HTMLElement;
    expect(within(home).getByText("passed")).toBeInTheDocument();
    expect(within(home).getByTitle("explain the build")).toBeInTheDocument();
    expect(mockStream).toHaveBeenCalledOnce();
  });

  it("is not offered a window of its own, where the batch would not follow it", async () => {
    const user = await startBatch();
    await user.click(screen.getByRole("button", { name: "Move Agent Manager" }));
    await screen.findByRole("menuitem", { name: "To the right panel" });
    expect(screen.queryByRole("menuitem", { name: "To a window of its own" })).not.toBeInTheDocument();
  });

  it("still stops tasks with Stop and Stop all, wherever it is", async () => {
    const user = await startBatch();
    await user.click(screen.getByRole("button", { name: "Move Agent Manager" }));
    await user.click(await screen.findByRole("menuitem", { name: "To the right panel" }));
    const docked = within(right()).getByRole("region", { name: "Agent Manager" });

    const card = within(docked).getByTitle("explain the build").closest("div.flex-col") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: /^Stop$/ }));
    expect(mockCancel).toHaveBeenCalledWith("b1", 1);

    await user.click(within(docked).getByRole("button", { name: /Stop all/ }));
    expect(mockCancel).toHaveBeenLastCalledWith("b1", null);
  });

  it("is driven from the keyboard: its controls are buttons reached with Tab and pressed with Enter", async () => {
    const user = await startBatch();
    const frame = board() as HTMLElement;

    within(frame).getByRole("button", { name: "Drag Agent Manager" }).focus();
    await user.tab();
    expect(within(frame).getByRole("button", { name: "Move Agent Manager" })).toHaveFocus();
    await user.tab();
    expect(within(frame).getByRole("button", { name: "Minimize Agent Manager" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(within(frame).queryByTitle("map the architecture")).not.toBeInTheDocument();

    within(frame).getByRole("button", { name: "Close Agent Manager" }).focus();
    await user.keyboard("{Enter}");
    const chip = screen.getByRole("button", { name: /Show the agent batch/ });
    chip.focus();
    await user.keyboard("{Enter}");
    expect(board()).toBeInTheDocument();
    expect(mockCancel).not.toHaveBeenCalled();
  });

  it("appears and goes at once, with nothing for reduced motion to take away", async () => {
    document.documentElement.dataset.motion = "reduced";
    try {
      const user = await startBatch();
      const frame = board() as HTMLElement;
      expect(frame.className).not.toMatch(/animate-|transition/);
      expect((frame.querySelector("[data-panel-controls]") as HTMLElement).className).not.toMatch(/transition|animate-/);
      await user.click(screen.getByRole("button", { name: "Close Agent Manager" }));
      const row = document.querySelector("[data-batch-chip]") as HTMLElement;
      expect(row.className).not.toMatch(/animate-|transition/);
      // The chip's hover colour change uses the motion tokens, which the reduced setting shortens.
      expect(screen.getByRole("button", { name: /Show the agent batch/ }).className).toMatch(/duration-1 ease-out/);
    } finally {
      delete document.documentElement.dataset.motion;
    }
  });

  it("remembers that it was closed, and opens again for the next confirmed batch", async () => {
    const user = await startBatch();
    await user.click(screen.getByRole("button", { name: "Close Agent Manager" }));
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").panels["agents.batch"].mode).toBe("closed"));

    await user.click(screen.getByRole("button", { name: "confirm batch" }));

    expect(board()).toBeInTheDocument();
    expect(mockStream).toHaveBeenCalledTimes(2);
  });

  it("draws nothing at all before there is a batch", () => {
    mount();
    expect(board()).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show the agent batch/ })).not.toBeInTheDocument();
  });
});
