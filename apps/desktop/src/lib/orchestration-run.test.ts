import { describe, expect, it } from "vitest";

import type { OrchFrame } from "@/lib/api";
import { applyFrame, EMPTY_RUN, isRunning, type OrchestrationState } from "@/lib/orchestration-run";

function frame(seq: number, kind: string, data: Record<string, unknown> = {}, taskId = ""): OrchFrame {
  return { seq, kind, task_id: taskId, text: "", data };
}

function fold(frames: OrchFrame[], from: OrchestrationState = EMPTY_RUN): OrchestrationState {
  return frames.reduce(applyFrame, from);
}

const FAN_OUT: OrchFrame[] = [
  frame(1, "run", { run_id: "r1" }),
  frame(2, "classified", { shape: "parallel_read", sources: 3 }),
  frame(3, "decomposed", {
    specs: [
      { task_id: "a", objective: "read doc A" },
      { task_id: "b", objective: "read doc B" },
    ],
  }),
  frame(4, "worker_started", { tier: "mid" }, "a"),
  frame(5, "worker_started", { tier: "mid" }, "b"),
  frame(6, "worker_verified", { stage: "criteria", tokens: 900, gaps: [] }, "a"),
  frame(7, "worker_rejected", { reason: "verifier", stage: "spot", detail: "unsupported claim" }, "b"),
  frame(8, "synthesizing", { envelopes: 1, fused: false }),
  frame(9, "done", { answer: "the answer", total_tokens: 2400, counterfactual_tokens: 7000 }),
];

describe("the hierarchy reducer", () => {
  it("creates every card the moment the split is announced, not as workers start", () => {
    const state = fold(FAN_OUT.slice(0, 3));

    // The alternative — a card per `worker_started` — shows the fan-out finishing rather than
    // running, because with a worker cap the queued subtasks would be invisible until they begin.
    expect(state.workers).toHaveLength(2);
    expect(state.workers.map((w) => w.status)).toEqual(["queued", "queued"]);
    expect(state.workers.map((w) => w.objective)).toEqual(["read doc A", "read doc B"]);
  });

  it("keeps the decomposition order however the frames arrive", () => {
    const shuffled = [FAN_OUT[0], FAN_OUT[1], FAN_OUT[2], FAN_OUT[4], FAN_OUT[3]];

    // Workers run in parallel and the server offers no order between them. Sorting by arrival
    // would reshuffle the grid on every reload.
    expect(fold(shuffled).workers.map((w) => w.taskId)).toEqual(["a", "b"]);
  });

  it("ignores a frame it has already applied", () => {
    const once = fold(FAN_OUT);
    const twice = fold(FAN_OUT, once);

    // This is the whole basis of reload: a client replays from the last seq it saw, and any
    // overlap must be a no-op rather than a second set of cards.
    expect(twice).toEqual(once);
    expect(twice.workers).toHaveLength(2);
  });

  it("reaches the same state whether frames come live, replayed, or both", () => {
    const live = fold(FAN_OUT);
    const split = fold(FAN_OUT.slice(4), fold(FAN_OUT.slice(0, 6)));

    // Replay-then-live overlaps by two frames on purpose: mixing the two paths must converge.
    expect(split).toEqual(live);
  });

  it("does not let a stale frame undo a newer one", () => {
    const state = fold([...FAN_OUT, frame(4, "worker_started", { tier: "mid" }, "a")]);

    expect(state.workers[0].status).toBe("verified");
    expect(state.stage).toBe("done");
  });

  it("keeps a rejected worker rejected and out of the count that fed the answer", () => {
    const state = fold(FAN_OUT);

    const rejected = state.workers.find((w) => w.taskId === "b");
    expect(rejected?.status).toBe("rejected");
    expect(rejected?.reason).toBe("verifier");
    expect(rejected?.detail).toBe("unsupported claim");
    expect(state.workers.filter((w) => w.status === "verified")).toHaveLength(1);
  });

  it("tells a worker that returned nothing apart from one the verifier refused", () => {
    const state = fold([
      ...FAN_OUT.slice(0, 5),
      frame(6, "worker_rejected", { reason: "no_output" }, "a"),
    ]);

    // A provider fault and a judgement about the model are different facts. One number for both
    // is how an outage gets read as a model that cannot follow a contract.
    expect(state.workers[0].reason).toBe("no_output");
    expect(state.workers[0].detail).toBe("");
  });

  it("clears the grid when the run falls back, rather than leaving empty cards", () => {
    const state = fold([
      frame(1, "run", { run_id: "r1" }),
      frame(2, "classified", { shape: "sequential_write", sources: 0 }),
      frame(3, "fell_back", { shape: "sequential_write", reason: "shape" }),
      frame(4, "done", { answer: "done by one agent", total_tokens: 500 }),
    ]);

    // An empty grid would suggest workers that failed. There were none: the run is one agent.
    expect(state.workers).toEqual([]);
    expect(state.fellBack).toEqual({ shape: "sequential_write", reason: "shape" });
    expect(state.answer).toBe("done by one agent");
  });

  it("reports a cancelled run as stopped, with no answer invented for it", () => {
    const state = fold([
      ...FAN_OUT.slice(0, 8),
      frame(9, "done", { answer: "", cancelled: true, total_tokens: 900 }),
    ]);

    expect(state.cancelled).toBe(true);
    expect(state.answer).toBe("");
    expect(state.totals?.tokens).toBe(900);
  });

  it("leaves the counterfactual null when there is none, never zero", () => {
    const state = fold([...FAN_OUT.slice(0, 8), frame(9, "done", { answer: "x", total_tokens: 10 })]);

    // Null means "nothing to compare against". Zero would claim the hierarchy saved nothing.
    expect(state.totals).toEqual({ tokens: 10, counterfactual: null });
  });

  it("does not strand an older client on a frame kind it has never heard of", () => {
    const state = fold([...FAN_OUT.slice(0, 3), frame(4, "heartbeat", { elapsed: 12 })]);

    // The sequence still advances, or every later frame would be discarded as already-seen.
    expect(state.seq).toBe(4);
    expect(state.workers).toHaveLength(2);
  });

  it("stops considering the run live once it is done or has failed", () => {
    expect(isRunning(fold(FAN_OUT.slice(0, 5)))).toBe(true);
    expect(isRunning(fold(FAN_OUT))).toBe(false);
    expect(isRunning(fold([...FAN_OUT.slice(0, 5), frame(6, "error", { message: "boom" })]))).toBe(
      false,
    );
    // Nothing has started: there is no run to stop.
    expect(isRunning(EMPTY_RUN)).toBe(false);
  });
});
