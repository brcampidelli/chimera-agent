import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { WorkerCard } from "@/components/orchestration/WorkerCard";
import {
  previewHierarchy,
  streamHierarchy,
  type HierarchyStreamHandlers,
  type OrchFrame,
} from "@/lib/api";
import { applyFrame, EMPTY_RUN, type WorkerState } from "@/lib/orchestration-run";
import type { HierarchyPreview } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

/**
 * Five sites at once: the workers can read the web now, so the screen has to say two things it
 * never had to before.
 *
 * One: a worker that fetched a page read content nobody here wrote, and its summary can carry
 * whatever the page put there. The backend reads that off the worker's own taint ledger and sends
 * it on `worker_verified` / `worker_rejected` and, for the synthesised answer, on `done` — because
 * once five summaries become one paragraph, no sentence carries its page. The card and the answer
 * repeat the ledger's verdict, and say nothing when the ledger said nothing.
 *
 * Two: what this project measured about splitting a task at all. `bench/hierarchy_equal_calls`
 * found that at the same number of model calls one agent that re-reads the documents did as well
 * or better. The plan says so before the run, where it can change a decision — a preview that
 * implied the split buys a better answer would be the screen claiming what the measurement
 * refused.
 */

vi.mock("@/lib/api", () => ({
  getOrchestrationRuns: vi.fn(async () => ({ runs: [] })),
  getOrchestrationFrames: vi.fn(async () => ({ run_id: "", frames: [], seq: 0 })),
  previewHierarchy: vi.fn(),
  streamHierarchy: vi.fn(),
  cancelOrchestration: vi.fn(),
}));

const mockPreview = vi.mocked(previewHierarchy);
const mockStream = vi.mocked(streamHierarchy);

const PLAN: HierarchyPreview = {
  shape: "parallel_read",
  profitable_estimate: true,
  estimate_margin: 0.4,
  would_fall_back: false,
  fell_back_reason: "",
  subtasks: ["read site A", "read site B"],
  workers: 2,
  budget_per_worker: 8000,
  sources: 2,
  plan_id: "plan_1",
  decompose_spent: true,
};

function frame(seq: number, kind: string, data: Record<string, unknown> = {}, taskId = ""): OrchFrame {
  return { seq, kind, task_id: taskId, text: "", data };
}

const OPENING: OrchFrame[] = [
  frame(2, "classified", { shape: "parallel_read", sources: 2 }),
  frame(3, "decomposed", {
    specs: [
      { task_id: "a", objective: "read site A" },
      { task_id: "b", objective: "read site B" },
    ],
  }),
  frame(4, "worker_started", { tier: "mid" }, "a"),
  frame(5, "worker_started", { tier: "mid" }, "b"),
];

async function startRun() {
  const user = userEvent.setup();
  let captured!: HierarchyStreamHandlers;
  mockPreview.mockResolvedValue(PLAN);
  mockStream.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
    captured = handlers;
    handlers.onRunId?.("run_7");
    handlers.onFrame?.(frame(1, "run", { run_id: "run_7" }));
    for (const f of OPENING) handlers.onFrame?.(f);
    return new Promise<void>(() => {});
  });
  renderWithProviders(<TaskConsole workspace="/repo" initialMode="hierarchy" onOpenCode={vi.fn()} />);
  await user.type(screen.getByLabelText(/task/i), "Compare the pricing on site A and site B");
  await user.click(screen.getByRole("button", { name: /see the plan/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /run the plan/i })).toBeInTheDocument());
  return { user, handlers: () => captured };
}

function send(handlers: HierarchyStreamHandlers, ...frames: OrchFrame[]) {
  act(() => {
    for (const f of frames) handlers.onFrame?.(f);
  });
}

function worker(over: Partial<WorkerState> = {}): WorkerState {
  return {
    taskId: "a",
    objective: "read site A",
    status: "verified",
    tier: "mid",
    stage: "accepted",
    checksRun: ["schema"],
    detail: "",
    reason: "",
    reasked: false,
    tokens: 900,
    summaryChars: 400,
    gaps: [],
    evidenceRefs: [],
    tainted: false,
    ...over,
  };
}

describe("the reducer carries the ledger's verdict", () => {
  it("marks the worker the frame marks, and the run when done says so", () => {
    const state = [
      ...[frame(1, "run", { run_id: "r" }), ...OPENING],
      frame(6, "worker_verified", { stage: "accepted", tokens: 900, tainted: true }, "a"),
      frame(7, "worker_verified", { stage: "accepted", tokens: 900 }, "b"),
      frame(8, "done", { answer: "A is cheaper", total_tokens: 2000, tainted: true }),
    ].reduce(applyFrame, EMPTY_RUN);
    expect(state.workers.find((w) => w.taskId === "a")?.tainted).toBe(true);
    expect(state.workers.find((w) => w.taskId === "b")?.tainted).toBe(false);
    expect(state.tainted).toBe(true);
  });

  it("reads absence as false, never as unknown", () => {
    const state = [
      ...[frame(1, "run", { run_id: "r" }), ...OPENING],
      frame(6, "worker_rejected", { reason: "verifier", detail: "x" }, "a"),
      frame(7, "done", { answer: "done", total_tokens: 10 }),
    ].reduce(applyFrame, EMPTY_RUN);
    expect(state.workers[0].tainted).toBe(false);
    expect(state.tainted).toBe(false);
    expect(EMPTY_RUN.tainted).toBe(false);
  });
});

describe("the worker card", () => {
  it("says a worker read content nobody here wrote, and says nothing otherwise", () => {
    const { unmount } = renderWithProviders(<WorkerCard worker={worker({ tainted: true })} />);
    expect(screen.getByTestId("worker-tainted")).toBeInTheDocument();
    expect(screen.getByText(/nobody here wrote/i)).toBeInTheDocument();
    unmount();
    renderWithProviders(<WorkerCard worker={worker()} />);
    expect(screen.queryByTestId("worker-tainted")).not.toBeInTheDocument();
  });
});

describe("the plan and the answer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows what was measured about splitting, before the run", async () => {
    await startRun();
    const note = screen.getByTestId("plan-measured");
    expect(note).toHaveTextContent(/hierarchy_equal_calls/);
    expect(note).toHaveTextContent(/not a better answer/i);
  });

  it("warns when the split left a named source without a worker, and not otherwise", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue({ ...PLAN, sources: 5, subtasks: ["read site A", "read site B", "read site C", "read site D"] });
    renderWithProviders(<TaskConsole workspace="/repo" initialMode="hierarchy" onOpenCode={vi.fn()} />);
    await user.type(screen.getByLabelText(/task/i), "Read five sites");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    await waitFor(() => expect(screen.getByTestId("plan-uncovered")).toBeInTheDocument());
    expect(screen.getByTestId("plan-uncovered")).toHaveTextContent(/5/);
    expect(screen.getByTestId("plan-uncovered")).toHaveTextContent(/4/);
  });

  it("says nothing about coverage when every named source has a subtask", async () => {
    await startRun();
    expect(screen.queryByTestId("plan-uncovered")).not.toBeInTheDocument();
  });

  it("labels an answer synthesised from something a worker fetched, above the answer", async () => {
    const { user, handlers } = await startRun();
    await user.click(screen.getByRole("button", { name: /run the plan/i }));
    await waitFor(() => expect(mockStream).toHaveBeenCalled());

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "accepted", tokens: 900, tainted: true }, "a"),
      frame(7, "worker_verified", { stage: "accepted", tokens: 900 }, "b"),
      frame(8, "synthesizing", { envelopes: 2, fused: false }),
      frame(9, "done", { answer: "Site A is cheaper.", total_tokens: 2400, tainted: true }),
    );

    expect(screen.getByText("Site A is cheaper.")).toBeInTheDocument();
    const label = screen.getByTestId("answer-tainted");
    expect(label).toHaveTextContent(/nobody here wrote/i);
    // Above, not below: the label precedes the answer in document order.
    const answer = screen.getByText("Site A is cheaper.");
    expect(label.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByTestId("worker-tainted")).toHaveLength(1);
  });

  it("puts no label on an answer whose workers fetched nothing", async () => {
    const { user, handlers } = await startRun();
    await user.click(screen.getByRole("button", { name: /run the plan/i }));
    await waitFor(() => expect(mockStream).toHaveBeenCalled());

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "accepted", tokens: 900 }, "a"),
      frame(7, "worker_verified", { stage: "accepted", tokens: 900 }, "b"),
      frame(8, "done", { answer: "Both cost the same.", total_tokens: 2400, tainted: false }),
    );

    expect(screen.getByText("Both cost the same.")).toBeInTheDocument();
    expect(screen.queryByTestId("answer-tainted")).not.toBeInTheDocument();
    expect(screen.queryByTestId("worker-tainted")).not.toBeInTheDocument();
  });
});
