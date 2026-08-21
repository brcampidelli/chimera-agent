import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Orchestration } from "@/components/orchestration/Orchestration";
import {
  cancelOrchestration,
  previewHierarchy,
  streamCrew,
  type HierarchyStreamHandlers,
  type OrchFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { HierarchyPreview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  previewHierarchy: vi.fn(),
  streamHierarchy: vi.fn(),
  streamCrew: vi.fn(),
  cancelOrchestration: vi.fn(),
}));

const mockPreview = vi.mocked(previewHierarchy);
const mockCrew = vi.mocked(streamCrew);

/** A write-shaped task: what `classify_task` sends down the single-agent path, and what a crew
 *  exists for. */
const WRITE_PLAN: HierarchyPreview = {
  shape: "sequential_write",
  profitable_estimate: false,
  estimate_margin: 0,
  would_fall_back: true,
  fell_back_reason: "shape",
  subtasks: [],
  workers: 0,
  budget_per_worker: 0,
  sources: 0,
  plan_id: "",
  decompose_spent: false,
};

function frame(seq: number, kind: string, data: Record<string, unknown> = {}, taskId = ""): OrchFrame {
  return { seq, kind, task_id: taskId, text: "", data };
}

/** Get as far as the crew form: ask for a plan on a write-shaped task, then press the crew door. */
async function openCrewForm() {
  const user = userEvent.setup();
  mockPreview.mockResolvedValue(WRITE_PLAN);
  renderWithProviders(<Orchestration workspace="/repo" onOpenCode={vi.fn()} />);
  await user.type(screen.getByLabelText(/task/i), "implemente o retry");
  await user.click(screen.getByRole("button", { name: /see the plan/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /build a crew/i })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /build a crew/i }));
  return user;
}

/** Fill the form and run, returning the captured stream handlers. */
async function runCrew(user: ReturnType<typeof userEvent.setup>, verify = "pytest -q") {
  let captured!: HierarchyStreamHandlers;
  mockCrew.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
    captured = handlers;
    return new Promise<void>(() => {});
  });
  if (verify) await user.type(screen.getByLabelText(/the check/i), verify);
  await user.type(screen.getByLabelText(/instruction for worker 1/i), "mudança mínima");
  await user.type(screen.getByLabelText(/instruction for worker 2/i), "reescreva");
  await user.click(screen.getByRole("button", { name: /run the crew/i }));
  await waitFor(() => expect(mockCrew).toHaveBeenCalled());
  return () => captured;
}

function send(handlers: HierarchyStreamHandlers, ...frames: OrchFrame[]) {
  act(() => {
    for (const f of frames) handlers.onFrame?.(f);
  });
}

describe("the crew", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is offered for the work the hierarchy refuses", async () => {
    await openCrewForm();

    // The whole point. A write-shaped task used to get a note pointing at another screen; the
    // crew is the shape that fits it, and it is on the same screen that just said no.
    expect(screen.getByLabelText(/the check/i)).toBeInTheDocument();
  });

  it("says what happens without a check, before you run", async () => {
    await openCrewForm();

    // Not a footnote: with no check, everyone who did not crash merges, and two workers touching
    // one file both lose it — so a crew without a check usually lands nothing at all.
    expect(screen.getByText(/two workers touching the same file both lose it/i)).toBeInTheDocument();
  });

  it("refuses two workers with the same name before asking the server", async () => {
    const user = await openCrewForm();

    await user.clear(screen.getByLabelText(/name of worker 2/i));
    await user.type(screen.getByLabelText(/name of worker 2/i), "conservador");

    // The name is how each worker's results are reported; two of them would put two workers on
    // one card. The server refuses it too — this just saves the round trip.
    expect(screen.getByText(/share a name/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run the crew/i })).toBeDisabled();
  });

  it("shows which checkout each worker writes in", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", { workspace: "/tmp/wt-a", instruction: "mínima" }, "conservador"),
      frame(3, "crew_worker_started", { workspace: "/tmp/wt-b", instruction: "reescreva" }, "direto"),
    );

    // Invisible before: the worktrees are created, used and removed without ever being named.
    expect(screen.getByText("/tmp/wt-a")).toBeInTheDocument();
    expect(screen.getByText("/tmp/wt-b")).toBeInTheDocument();
  });

  it("says why a worker was discarded, and what the check printed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", { workspace: "/tmp/wt-a" }, "conservador"),
      frame(3, "crew_worker_rejected", { reason: "verify", detail: "1 failed: test_desconto" }, "conservador"),
    );

    expect(screen.getByText(/discarded/i)).toBeInTheDocument();
    // The output, not just the fact: a crew whose workers all fail the same check is a crew
    // whose check is wrong, and that is only visible if the output is.
    expect(screen.getByText(/1 failed: test_desconto/)).toBeInTheDocument();
    expect(screen.getByText(/nothing from this worker reached your files/i)).toBeInTheDocument();
  });

  it("says a contested file was kept by NEITHER worker", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "conflict", { path: "carrinho.py" }),
      frame(3, "crew_done", { merged: 0, conflicts: ["carrinho.py"], is_repo: true }),
    );

    expect(screen.getByText("carrinho.py")).toBeInTheDocument();
    // "One of them won" would be the wrong reading, and the expensive one: the file is untouched.
    expect(screen.getByText(/NEITHER version was kept/i)).toBeInTheDocument();
  });

  it("warns when there was no isolation at all", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_done", { merged: 1, conflicts: [], is_repo: false }),
    );

    // Outside a git repository every sentence about separate checkouts stops being true.
    expect(screen.getByText(/not a git repository/i)).toBeInTheDocument();
  });

  it("says plainly when nothing landed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_done", { merged: 0, conflicts: [], is_repo: true }),
    );

    // A crew that produced nothing must not look like a crew that finished quietly.
    expect(screen.getByText(/Nothing landed/i)).toBeInTheDocument();
  });

  it("stops the crew by its run id", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);
    vi.mocked(cancelOrchestration).mockResolvedValue({ ok: true, cancelled: true });

    send(handlers(), frame(1, "run", { run_id: "c1" }), frame(2, "crew_worker_started", {}, "conservador"));
    await user.click(screen.getByRole("button", { name: /^stop$/i }));

    expect(cancelOrchestration).toHaveBeenCalledWith("c1");
  });

  it("tells a check that could not run apart from one that failed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "conservador"),
      frame(
        3,
        "crew_worker_rejected",
        { reason: "verify", detail: "verification could not run: [Errno 2] No such file or directory" },
        "conservador",
      ),
    );

    // These send you to two different places. "Your check failed" points at the code the worker
    // wrote; a check that never executed points at the command or the folder — and reporting the
    // second as the first is how an afternoon gets spent reading a diff that was fine.
    expect(screen.getByText(/could not run here/i)).toBeInTheDocument();
    expect(screen.queryByText(/did not pass/i)).not.toBeInTheDocument();
  });

  it("does not show its own cleanup as a failure", async () => {
    const user = await openCrewForm();
    let captured!: HierarchyStreamHandlers;
    mockCrew.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
      captured = handlers;
      return new Promise<void>(() => {});
    });
    await user.type(screen.getByLabelText(/the check/i), "pytest -q");
    await user.type(screen.getByLabelText(/instruction for worker 1/i), "a");
    await user.type(screen.getByLabelText(/instruction for worker 2/i), "b");
    await user.click(screen.getByRole("button", { name: /run the crew/i }));
    await waitFor(() => expect(mockCrew).toHaveBeenCalled());

    // Leaving the screen aborts the fetch — that is this component's own teardown, and
    // "signal is aborted without reason" was reaching the user as though the run had broken.
    act(() => captured.onError?.("signal is aborted without reason"));

    expect(screen.queryByText(/aborted/i)).not.toBeInTheDocument();
  });

  it("sends the check and the roles the form was given", async () => {
    const user = await openCrewForm();
    await runCrew(user, "npm test");

    expect(mockCrew).toHaveBeenCalledWith(
      expect.objectContaining({
        verify: "npm test",
        workspace: "/repo",
        workers: [
          expect.objectContaining({ name: "conservador" }),
          expect.objectContaining({ name: "direto" }),
        ],
      }),
      expect.anything(),
      expect.anything(),
    );
  });
});
