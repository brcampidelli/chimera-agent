import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Agents } from "@/components/Agents";
import { cancelAgents, streamAgents, type AgentsStreamHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { AgentResult, AgentsBatch } from "@/lib/types";

vi.mock("@/lib/api", () => ({ streamAgents: vi.fn(), cancelAgents: vi.fn() }));

const mockStreamAgents = vi.mocked(streamAgents);
const mockCancelAgents = vi.mocked(cancelAgents);

function result(over: Partial<AgentResult> = {}): AgentResult {
  return {
    index: 0,
    task: "add a test",
    success: true,
    attempts: 1,
    reverted: false,
    changed_paths: [],
    diffs: [],
    error: "",
    ...over,
  };
}

function batch(over: Partial<AgentsBatch> = {}): AgentsBatch {
  return { is_repo: true, merged: 0, conflicts: [], results: [result()], ...over };
}

/** Mount the board for a confirmed batch and hand it the given `batch_done` payload — the same
 *  shape the real SSE delivers.
 *
 *  There is nothing to fill in and nothing to click. The launcher this used to drive is gone: the
 *  confirmation happened on a card in the conversation, before the worktrees existed, and a board
 *  that offered a second Run would mean that card was not the decision it looked like. */
async function runBatch(done: AgentsBatch, tasks: string[] = ["add a test", "fix the lint"]) {
  const user = userEvent.setup();
  mockStreamAgents.mockImplementation(async (_req, handlers: AgentsStreamHandlers) => {
    handlers.onStart?.({ tasks, workspace: "/repo", max_workers: 4 });
    handlers.onBatchDone?.(done);
  });
  renderWithProviders(<Agents workspace="/repo" tasks={tasks} {...seams} />);
  return user;
}

/** Start a batch whose stream stays open: it announces its batch id, starts two tasks, and then hangs
 *  — exactly like a real batch mid-attempt. Returns the captured handlers so a test can end it. */
async function startHangingBatch(
  batchId: string | null = "batch_42",
  tasks: string[] = ["add a test", "fix the lint"],
) {
  const user = userEvent.setup();
  let captured!: AgentsStreamHandlers;
  mockStreamAgents.mockImplementation((_req, handlers: AgentsStreamHandlers) => {
    captured = handlers;
    if (batchId) handlers.onBatchId?.(batchId);
    handlers.onStart?.({ tasks, workspace: "/repo", max_workers: 4 });
    return new Promise<void>(() => {}); // never settles: the batch is in flight
  });
  renderWithProviders(<Agents workspace="/repo" tasks={tasks} {...seams} />);
  return { user, handlers: () => captured };
}

/** The Stop button inside task `label`'s card (the per-task control, not the batch-wide one). */
function cardStop(label: string) {
  const card = screen.getByTitle(label).closest("div.flex-col") as HTMLElement;
  return within(card).queryByRole("button", { name: /Stop/ });
}

/** Posture and profile travel with every batch. Omitting them is not neutral: server-side an absent
 *  posture means no tool denials and no pause, and an absent profile means the reviewer is the model
 *  that wrote the patch. */
const seams = { posture: { reach: "workspace" as const, approval: "suspicious" as const }, profile: "balanced" as const };

describe("Agents", () => {
  beforeEach(() => {
    mockStreamAgents.mockReset();
    mockCancelAgents.mockReset();
    mockCancelAgents.mockResolvedValue({ ok: true, cancelled: 1 });
  });

  it("offers no launcher of its own", async () => {
    // The regression this guards is the old screen creeping back: eight task boxes, a model field,
    // a worker count and three fusion modes, all asked before anyone knew whether the work was
    // parallel. What the batch needs, the system resolves — the same way it does for a single run.
    await runBatch(batch());

    expect(screen.queryByRole("button", { name: /Run all/ })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Describe a change/)).not.toBeInTheDocument();
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

  it("prints why a card has no result, so a timed-out task cannot read as a failed one", async () => {
    // The batch's wall-clock deadline blew while task 1 was still running. It comes back with the
    // same `success: false` + zero attempts + no files as a task that genuinely did not pass, so
    // without the server's reason on screen the two are the same card.
    await runBatch(
      batch({
        results: [
          result({ index: 0, task: "add a test" }),
          result({
            index: 1,
            task: "fix the lint",
            success: false,
            attempts: 0,
            error: "timed out after 14400.0s",
          }),
        ],
      }),
    );

    const stalled = screen.getByTitle("fix the lint").closest("div.flex-col") as HTMLElement;
    const passed = screen.getByTitle("add a test").closest("div.flex-col") as HTMLElement;
    expect(within(stalled).getByText("timed out after 14400.0s")).toBeInTheDocument();
    // And a card that really ran says nothing extra — the line is a reason, not decoration.
    expect(within(passed).queryByText(/timed out/)).not.toBeInTheDocument();
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
    mockStreamAgents.mockImplementation(async (_req, handlers: AgentsStreamHandlers) => {
      handlers.onError?.("HTTP 500");
    });
    renderWithProviders(<Agents workspace="/repo" tasks={["add a test"]} {...seams} />);

    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });

  it("submits exactly the confirmed tasks, and asks for no verify command", async () => {
    // No `verify` per task any more. The project's own command is resolved server-side, the same
    // way a single run resolves it — a batch that asked again would be the one place still asking.
    mockStreamAgents.mockImplementation(async () => {});
    renderWithProviders(<Agents workspace="/repo" tasks={["add a test", "  ", "fix the lint"]} {...seams} />);

    await waitFor(() => expect(mockStreamAgents).toHaveBeenCalledOnce());
    expect(mockStreamAgents.mock.calls[0][0].tasks).toEqual([
      { task: "add a test", verify: null },
      { task: "fix the lint", verify: null },
    ]);
  });

  it("is not structurally weaker than the same task run alone", async () => {
    // Omitting these is not neutral. Server-side an absent posture resolves to no tool denials and
    // no pause — more permissive than any corner of the grid a user could pick — and an absent
    // profile makes the reviewer the model that wrote the patch. Being one of several must not
    // quietly change what an agent is allowed to do.
    mockStreamAgents.mockImplementation(async () => {});
    renderWithProviders(<Agents workspace="/repo" tasks={["add a test"]} {...seams} />);

    await waitFor(() => expect(mockStreamAgents).toHaveBeenCalledOnce());
    expect(mockStreamAgents.mock.calls[0][0]).toMatchObject(seams);
  });
});

describe("Agents — stopping tasks", () => {
  beforeEach(() => {
    mockStreamAgents.mockReset();
    mockCancelAgents.mockReset();
    mockCancelAgents.mockResolvedValue({ ok: true, cancelled: 1 });
  });

  it("offers no Stop until a batch is in flight", () => {
    renderWithProviders(<Agents workspace="/repo" tasks={[]} {...seams} />);

    expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
  });

  it("shows a Stop on each running task card, plus one Stop all for the batch", async () => {
    await startHangingBatch();

    expect(await screen.findByRole("button", { name: /Stop all/ })).toBeEnabled();
    expect(cardStop("add a test")).toBeEnabled();
    expect(cardStop("fix the lint")).toBeEnabled();
  });

  it("cancels just one task by its index and says it stops after that attempt", async () => {
    await startHangingBatch("batch_42");
    const user = userEvent.setup();

    await user.click(cardStop("fix the lint") as HTMLElement);

    // Task 1 ("fix the lint") — its OWN index, not the batch-wide null.
    expect(mockCancelAgents).toHaveBeenCalledWith("batch_42", 1);
    const card = screen.getByTitle("fix the lint").closest("div.flex-col") as HTMLElement;
    expect(await within(card).findByText(/Stopping after this attempt/)).toBeInTheDocument();
    // The batch's OTHER task is untouched — it keeps running and still offers its own Stop.
    const other = screen.getByTitle("add a test").closest("div.flex-col") as HTMLElement;
    expect(within(other).queryByText(/Stopping after this attempt/)).not.toBeInTheDocument();
    expect(cardStop("add a test")).toBeEnabled();
  });

  it("cancels every task when Stop all is clicked", async () => {
    await startHangingBatch("batch_42");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Stop all/ }));

    // No index = the whole batch.
    expect(mockCancelAgents).toHaveBeenCalledWith("batch_42", null);
    expect(await screen.findAllByText(/Stopping after this attempt/)).toHaveLength(2);
  });

  it("does not offer to cancel before the batch has reported an id", async () => {
    await startHangingBatch(null);

    expect(await screen.findByRole("button", { name: /Stop all/ })).toBeDisabled();
    expect(cardStop("add a test")).toBeDisabled();
    expect(mockCancelAgents).not.toHaveBeenCalled();
  });

  it("shows no Stop on a task once the batch has finished it", async () => {
    const { handlers } = await startHangingBatch("batch_42");

    await screen.findByRole("button", { name: /Stop all/ });
    // The terminal frame lands: both tasks have real results now, so nothing is still running.
    await act(async () => {
      handlers().onBatchDone?.(
        batch({
          results: [
            result({ index: 0, task: "add a test" }),
            result({ index: 1, task: "fix the lint", success: false }),
          ],
        }),
      );
    });

    await waitFor(() => expect(cardStop("add a test")).not.toBeInTheDocument());
    expect(cardStop("fix the lint")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Stop all/ })).not.toBeInTheDocument();
  });
});

describe("Agents — the project comes from one place", () => {
  it("does not ask for a workspace of its own", async () => {
    // There used to be a second path field here. Two boxes asking one question meant you could
    // point Code at your app and this at somewhere else, launch a parallel batch, and find the
    // worktrees in a repository you had stopped thinking about.
    renderWithProviders(<Agents workspace="/repo" tasks={["do the thing"]} {...seams} />);

    expect(screen.queryByPlaceholderText(/workspace path|folder path/i)).not.toBeInTheDocument();
    expect(await screen.findByText("/repo")).toBeInTheDocument();
  });

  it("sends the chosen project to the batch, not an empty string", async () => {
    renderWithProviders(<Agents workspace="/repo" tasks={["do the thing"]} {...seams} />);

    await waitFor(() => expect(streamAgents).toHaveBeenCalled());
    expect(vi.mocked(streamAgents).mock.calls[0][0]).toMatchObject({ workspace: "/repo" });
  });
});
