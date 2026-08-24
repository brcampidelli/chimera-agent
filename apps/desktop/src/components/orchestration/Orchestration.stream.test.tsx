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
  // The history list mounts on this screen, so its endpoint has to exist in the mock.
  getOrchestrationRuns: vi.fn(async () => ({ runs: [] })),
  getOrchestrationFrames: vi.fn(async () => ({ run_id: "", frames: [], seq: 0 })),
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
  await waitFor(() => expect(screen.getByRole("button", { name: /run the plan/i })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /run the plan/i }));
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

  it("runs the plan it showed, by id", async () => {
    await startRun();

    // Without the id the backend decomposes again, at a non-zero temperature — which is how a
    // preview promising one worker delivered three. Approving a plan has to mean something.
    expect(mockStream).toHaveBeenCalledWith(
      expect.objectContaining({ plan_id: "plan_1" }),
      expect.anything(),
      expect.anything(),
    );
  });

  it("shows one card per subtask as soon as the split lands", async () => {
    await startRun();

    expect(screen.getByText("read doc A")).toBeInTheDocument();
    expect(screen.getByText("read doc B")).toBeInTheDocument();
    expect(screen.getAllByText(/running/i).length).toBeGreaterThan(0);
  });

  it("names the check that ran, and shows the objection when one is refused", async () => {
    // This asserted `stage` verbatim — "criteria", "accepted" — which was the defect rather than
    // the feature. `stage` is the backend's enum for "which gate decided", and `accepted` means
    // "none rejected"; for ordinary output that is ONE gate, because criteria needs `regex:` lines
    // in a prose `output_format` and the spot check needs evidence refs that only exist above the
    // 8000-char cap. The card now names the gates that actually ran.
    const { handlers } = await startRun();

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "accepted", checks_run: ["schema", "criteria"], tokens: 900, gaps: [] }, "a"),
      frame(7, "worker_rejected", { reason: "verifier", stage: "spot", detail: "unsupported claim" }, "b"),
    );

    expect(screen.getByText(/criteria|critérios/i)).toBeInTheDocument();
    expect(screen.queryByText("accepted")).not.toBeInTheDocument();
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

  it("comes back out of Stopping when the ask could not be delivered", async () => {
    // `stopping` was set on click and never cleared. A cancel that could not land — a dropped
    // request, or a run id the server no longer has — left a disabled spinner beside a run that was
    // still going, and nothing else cleared it either: the run stays running until a terminal frame
    // arrives and no such frame was coming. Reloading was the only way out.
    const { user } = await startRun();
    mockCancel.mockRejectedValue(new Error("network"));

    await user.click(screen.getByRole("button", { name: /^stop$/i }));

    await screen.findByText(/could not tell/i);
    // Re-armed, because being able to ask again is the only honest offer left.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^stop$/i })).not.toBeDisabled(),
    );
  });

  it("treats a run the server no longer knows as unknown, not as stopped", async () => {
    const { user } = await startRun();
    mockCancel.mockResolvedValue({ ok: false, cancelled: false });

    await user.click(screen.getByRole("button", { name: /^stop$/i }));

    await screen.findByText(/could not tell/i);
  });

  it("shows what a stopped run cost, instead of saying nothing was spent", async () => {
    const { handlers } = await startRun();

    // The server sends the real spend on cancel and the reducer stores it. The totals used to be
    // rendered only inside the `state.answer` branch — which is empty on a cancel — so the number
    // arrived and was thrown away, under a sentence claiming nothing had been spent. The Stop
    // button's own tooltip, two lines above it in the same component, said the opposite.
    send(
      handlers(),
      frame(6, "worker_verified", { stage: "criteria", tokens: 900 }, "a"),
      frame(7, "done", { answer: "", cancelled: true, total_tokens: 1700 }),
    );

    // "1,700" in English, "1.700" in Portuguese: the digits are grouped by the CHOSEN language.
    expect(screen.getByText(/1,700 tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/were charged/i)).toBeInTheDocument();
  });

  it("does not claim a stopped run produced an answer", async () => {
    const { handlers } = await startRun();

    send(handlers(), frame(6, "done", { answer: "", cancelled: true, total_tokens: 0 }));

    // Zero is a real number here: it means the stop landed before anything ran. Rendering it is
    // not the same as rendering the answer section, which must stay absent.
    expect(screen.getByText(/0 tokens/i)).toBeInTheDocument();
    expect(screen.queryByText(/cost gate predicted/i)).toBeNull();
  });

  it("says which side of the estimate the run landed on", async () => {
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
    expect(screen.getByText(/2,400 tokens/i)).toBeInTheDocument();
    // Named for what it is — the gate's own arithmetic — and placed relative to what was spent.
    expect(
      screen.getByText(/cost gate predicted about 7,000, and it came in under/i),
    ).toBeInTheDocument();
    // Never as a saving. No second run happened, so there is no measured difference to claim.
    expect(screen.queryByText(/saved|saving/i)).toBeNull();
    // The run is over: no Stop control left to click.
    expect(screen.queryByRole("button", { name: /^stop$/i })).toBeNull();
  });

  it("does not print a loss in the grammar of a saving", async () => {
    // Measured live, before this: **"8721 tokens · um agente só teria custado cerca de 8000"** —
    // the two numbers joined by a neutral dot, no comparison drawn, on a run that cost 721 tokens
    // MORE than the estimate it was being shown against. Both readings were available to a reader
    // and the sentence drew neither, so the one it got was the flattering one.
    const { handlers } = await startRun();

    send(
      handlers(),
      frame(6, "worker_verified", { stage: "criteria", tokens: 900 }, "a"),
      frame(7, "done", { answer: "Done.", total_tokens: 8721, counterfactual_tokens: 8000 }),
    );

    expect(screen.getByText(/8,721 tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/predicted about 8,000, and it went over/i)).toBeInTheDocument();
    expect(screen.queryByText(/came in under/i)).toBeNull();
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
