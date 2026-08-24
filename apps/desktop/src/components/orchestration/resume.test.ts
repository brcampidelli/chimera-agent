import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getOrchestrationFrames } from "@/lib/api";

import { lastRun, rememberRun, resumeFrames } from "./resume";

vi.mock("@/lib/api", async () => {
  const real = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ApiError: real.ApiError, getOrchestrationFrames: vi.fn() };
});

const mockFrames = vi.mocked(getOrchestrationFrames);

/**
 * A remembered run id outlives the run. What the tab does about that is the whole of this file.
 *
 * The id is the only thing kept locally; everything else is replayed from the server's transcript
 * through the same reducer the live stream feeds. So the one decision here is whether to KEEP the
 * id after a failed replay, and the two failures look identical from inside a `catch`:
 *
 *  - the transcript is gone (pruned past `MAX_RUNS`, or a different home) — the id is dead, and
 *    keeping it makes every future mount issue a request that cannot succeed;
 *  - the server could not be reached — the run may be running right now, and forgetting throws
 *    away something the user paid for.
 *
 * They were indistinguishable until the endpoint started answering 404 instead of `200 []`, which
 * is the change that made this file necessary: before it, the empty list did the forgetting.
 */
describe("resuming a run", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("replays the frames of a run it remembers", async () => {
    rememberRun("abc123");
    mockFrames.mockResolvedValue({
      run_id: "abc123",
      frames: [{ kind: "run", seq: 1, task_id: "", text: "", data: {} }],
      seq: 1,
    } as Awaited<ReturnType<typeof getOrchestrationFrames>>);

    const { runId, frames } = await resumeFrames();

    expect(runId).toBe("abc123");
    expect(frames).toHaveLength(1);
    expect(lastRun()).toBe("abc123");
  });

  it("drops an id the server says is gone", async () => {
    rememberRun("pruned");
    mockFrames.mockRejectedValue(new ApiError("no such run", 404));

    const { frames } = await resumeFrames();

    expect(frames).toEqual([]);
    expect(lastRun(), "a dead id was kept, and every mount will retry it").toBe("");
  });

  it("keeps the id when it could not ask", async () => {
    // The control, and the reason this is not just "forget on any failure": the app starting before
    // the local server does is an ordinary morning, and a run in flight is the case worth resuming.
    rememberRun("still-running");
    mockFrames.mockRejectedValue(new TypeError("Failed to fetch"));

    const { frames } = await resumeFrames();

    expect(frames).toEqual([]);
    expect(lastRun(), "a resumable run was forgotten over a network blip").toBe("still-running");
  });

  it("keeps the id when the server is refusing for another reason", async () => {
    // 401 from a token that has not loaded yet, 503 from a server still coming up. Neither says
    // anything about whether the run exists.
    rememberRun("behind-auth");
    mockFrames.mockRejectedValue(new ApiError("unauthorized", 401));

    await resumeFrames();

    expect(lastRun()).toBe("behind-auth");
  });

  it("asks for nothing when there is nothing to resume", async () => {
    const { runId, frames } = await resumeFrames();

    expect(runId).toBe("");
    expect(frames).toEqual([]);
    expect(mockFrames).not.toHaveBeenCalled();
  });
});
