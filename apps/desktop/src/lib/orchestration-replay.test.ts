import { describe, expect, it } from "vitest";

import type { OrchFrame } from "@/lib/api";
import { applyFrame, EMPTY_RUN, type OrchestrationState } from "@/lib/orchestration-run";

/**
 * A replayed run has to fold into the same state a live one does.
 *
 * The reducer was tested with live-shaped frames. The endpoint was tested for its `since` filter.
 * Nobody tested the join, and the join was where the two disagreed: the transcript is written flat
 * and keyed `event`, the stream delivers `{seq, kind, task_id, text, data}`, and the raw line went
 * straight into a reducer that switches on `kind`.
 *
 * On screen that produced a run with a stepper — which draws its labels unconditionally, so it
 * looked alive — and **no worker cards and no answer**. It had been that way since the transcripts
 * started being written, invisible because until the run list was wired the only way to reach a
 * replay was to reload the page mid-run.
 *
 * So this test starts from the RAW LINE as `runlog` writes it, applies the same normalisation the
 * API now performs, and asserts the fold. Starting from an already-correct frame is what the
 * existing suite did, and it is why nothing failed.
 */

/** One line of `frames.jsonl`, exactly as `runlog.append` writes it: flat, keyed `event`. */
type PersistedLine = Record<string, unknown> & { event: string };

/** The normalisation `GET /api/orchestration/runs/{id}` performs, mirrored here.
 *
 * Mirrored rather than imported because the two live on opposite sides of an HTTP boundary — the
 * point of the test is that the shapes agree, and a shared helper would make them agree by
 * construction while production still disagreed.
 */
function asStreamFrame(line: PersistedLine): OrchFrame {
  const { event, seq, task_id: taskId, text, ...data } = line;
  return {
    seq: Number(seq ?? 0),
    kind: String(event),
    task_id: String(taskId ?? ""),
    text: String(text ?? ""),
    data,
  };
}

/** What a real two-worker fan-out leaves on disk. Field for field from a live rc14 transcript. */
const ON_DISK: PersistedLine[] = [
  { event: "run", seq: 1, run_id: "779a0f", task: "compare the notes" },
  { event: "classified", seq: 2, task_id: "", text: "parallel_read", shape: "parallel_read", sources: 2 },
  {
    event: "decomposed",
    seq: 3,
    task_id: "",
    text: "2 subtasks",
    specs: [
      { task_id: "sub-1", objective: "read index.html" },
      { task_id: "sub-2", objective: "read style.css" },
    ],
  },
  { event: "worker_started", seq: 4, task_id: "sub-1", text: "read index.html", tier: "mid" },
  { event: "worker_started", seq: 5, task_id: "sub-2", text: "read style.css", tier: "mid" },
  { event: "worker_verified", seq: 6, task_id: "sub-1", text: "verified (accepted)", stage: "accepted", tokens: 11369 },
  { event: "worker_verified", seq: 7, task_id: "sub-2", text: "verified (accepted)", stage: "accepted", tokens: 7384 },
  { event: "synthesizing", seq: 8, task_id: "", text: "2 summaries", envelopes: 2 },
  { event: "done", seq: 9, task_id: "", text: "synthesised", answer: "Both files agree.", shape: "parallel_read" },
];

function fold(frames: OrchFrame[]): OrchestrationState {
  return frames.reduce(applyFrame, EMPTY_RUN);
}

describe("replaying a transcript", () => {
  const state = fold(ON_DISK.map(asStreamFrame));

  it("rebuilds the workers the run actually had", () => {
    // The assertion that was false in production for three release candidates.
    expect(state.workers.map((w) => w.taskId)).toEqual(["sub-1", "sub-2"]);
  });

  it("rebuilds the answer", () => {
    expect(state.answer).toBe("Both files agree.");
  });

  it("knows the run is over", () => {
    expect(state.stage).toBe("done");
  });

  it("would build nothing at all from the raw line", () => {
    // The defect itself, pinned. Without this the test above passes for a reducer that quietly
    // accepts either shape, and the normalisation could be deleted with nothing going red.
    const raw = ON_DISK as unknown as OrchFrame[];
    const wrong = fold(raw);

    // Against the empty state rather than against literals: the claim is that nine frames changed
    // NOTHING, which is stronger and does not go stale when a default does.
    expect(wrong.workers).toEqual(EMPTY_RUN.workers);
    expect(wrong.answer).toBe(EMPTY_RUN.answer);
    expect(wrong.stage).toBe(EMPTY_RUN.stage);
  });
});
