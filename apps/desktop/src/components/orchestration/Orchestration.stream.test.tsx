import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Orchestration } from "@/components/orchestration/Orchestration";
import {
  cancelOrchestration,
  previewHierarchy,
  streamHierarchy,
  type HierarchyStreamHandlers,
  type OrchFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { HierarchyPreview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  previewHierarchy: vi.fn(),
  streamHierarchy: vi.fn(),
  cancelOrchestration: vi.fn(),
}));

const mockPreview = vi.mocked(previewHierarchy);
const mockStream = vi.mocked(streamHierarchy);
const mockCancel = vi.mocked(cancelOrchestration);

const FAN_OUT_PLAN: HierarchyPreview = {
  shape: "parallel_read",
  profitable_estimate: true,
  estimate_margin: 0.4,
  would_fall_back: false,
  fell_back_reason: "",
  subtasks: ["read doc A", "read doc B"],
  workers: 2,
  budget_per_worker: 8000,
  sources: 2,
  decompose_spent: true,
};

function frame(seq: number, kind: string, data: Record<string, unknown> = {}, taskId = ""): OrchFrame {
  return { seq, kind, task_id: taskId, text: "", data };
}

const OPENING: OrchFrame[] = [
  frame(2, "classified", { shape: "parallel_read", sources: 2 }),
  frame(3, "decomposed", {
    specs: [
      { task_id: "a", objective: "read doc A" },
      { task_id: "b", objective: "read doc B" },
    ],
  }),
  frame(4, "worker_started", { tier: "mid" }, "a"),
  frame(5, "worker_started", { tier: "mid" }, "b"),
];

/** Start a run whose stream stays open, and hand back the captured handlers so a test can feed it. */
async function startRun() {
  const user = userEvent.setup();
  let captured!: HierarchyStreamHandlers;
  mockPreview.mockResolvedValue(FAN_OUT_PLAN);
  mockStream.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
    captured = handlers;
    // Exactly what the real dispatcher does with a `run` event: the id is a frame like any
    // other. A mock that only called onRunId would be testing a path production does not take.
    handlers.onRunId?.("run_7");
    handlers.onFrame?.(frame(1, "run", { run_id: "run_7" }));
    for (const f of OPENING) handlers.onFrame?.(f);
    return new Promise<void>(() => {}); // never settles: the run is in flight
  });

  renderWithProviders(<Orchestration workspace="/repo" onOpenCode={vi.fn()} />);
  await user.type(screen.getByLabelText(/task/i), "Compare doc A and doc B and list the risks");
  await user.click(screen.getByRole("button", { name: /see the plan/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /run with 2/i })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /run with 2/i }));
  await waitFor(() => expect(mockStream).toHaveBeenCalled());
  return { user, handlers: () => captured };
}

function send(handlers: HierarchyStreamHandlers, ...frames: OrchFrame[]) {
  act(() => {
    for (const f of frames) handlers.onFrame?.(f);
  });
}

describe("watching a fan-out", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows one card per subtask as soon as the split lands", async () => {
    await startRun();

    expect(screen.getByText("read doc A")).toBeInTheDocument();
    expect(screen.getByText("read doc B")).toBeInTheDocument();
    expect(screen.getAllByText(/running/i).length).toBeGreaterThan(0);
  });

  it("says which stage accepted a worker, and shows the objection when one is refused", async () => {
    const { handlers } = await startRun();

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "criteria", tokens: 900, gaps: [] }, "a"),
      frame(7, "worker_rejected", { reason: "verifier", stage: "spot", detail: "unsupported claim" }, "b"),
    );

    expect(screen.getByText("criteria")).toBeInTheDocument();
    // Verbatim, not summarised: the objection is the only evidence of why an envelope was dropped.
    expect(screen.getByText("unsupported claim")).toBeInTheDocument();
  });

  it("says a rejected worker does not reach the answer", async () => {
    const { handlers } = await startRun();

    send(handlers(), frame(6, "worker_rejected", { reason: "verifier", detail: "no evidence" }, "b"));

    // Without this line a user watches two workers, reads an answer built from one, and has no
    // way to know the other was discarded.
    expect(screen.getByText(/does not reach the final answer/i)).toBeInTheDocument();
  });

  it("distinguishes a worker that returned nothing from one the verifier refused", async () => {
    const { handlers } = await startRun();

    send(handlers(), frame(6, "worker_rejected", { reason: "no_output" }, "a"));

    expect(screen.getByText(/budget ran out or the provider failed/i)).toBeInTheDocument();
  });

  it("admits there is no live text rather than faking a cursor", async () => {
    await startRun();

    // The workers run through a non-streaming backend. An animated caret would be a lie told
    // in motion, which is harder to notice than one told in words.
    expect(screen.getByText(/state changes, not live text/i)).toBeInTheDocument();
  });

  it("stops the run by its id, and says so before the server has confirmed", async () => {
    const { user } = await startRun();
    mockCancel.mockResolvedValue({ ok: true, cancelled: true });

    await user.click(screen.getByRole("button", { name: /^stop$/i }));

    expect(mockCancel).toHaveBeenCalledWith("run_7");
    // Stopping, not stopped: the run is not idle until a terminal frame says it is, and claiming
    // otherwise would report a halt the backend has not made.
    await waitFor(() => expect(screen.getByRole("button", { name: /stopping/i })).toBeInTheDocument());
  });

  it("renders the answer and prices it against the counterfactual", async () => {
    const { handlers } = await startRun();

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "criteria", tokens: 900 }, "a"),
      frame(7, "worker_verified", { stage: "schema", tokens: 800 }, "b"),
      frame(8, "synthesizing", { envelopes: 2, fused: false }),
      frame(9, "done", {
        answer: "Upgrading breaks the config loader.",
        total_tokens: 2400,
        counterfactual_tokens: 7000,
      }),
    );

    expect(screen.getByText(/upgrading breaks the config loader/i)).toBeInTheDocument();
    expect(screen.getByText(/2400 tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/would have cost about 7000/i)).toBeInTheDocument();
    // The run is over: no Stop control left to click.
    expect(screen.queryByRole("button", { name: /^stop$/i })).toBeNull();
  });

  it("prices a run with no counterfactual in tokens alone, inventing no comparison", async () => {
    const { handlers } = await startRun();

    send(handlers(), frame(9, "done", { answer: "done", total_tokens: 500 }));

    expect(screen.getByText(/500 tokens/i)).toBeInTheDocument();
    expect(screen.queryByText(/would have cost/i)).toBeNull();
  });

  it("announces the stage through one polite region, and leaves the answer out of it", async () => {
    const { handlers } = await startRun();
    send(handlers(), frame(9, "done", { answer: "the final answer", total_tokens: 10 }));

    const live = screen.getByRole("status");
    expect(live).toHaveAttribute("aria-live", "polite");
    // Four cards changing at once inside a live region would interrupt on every worker, and
    // streamed prose inside one is read out as it is written.
    expect(live).not.toHaveTextContent(/the final answer/i);
  });
});
