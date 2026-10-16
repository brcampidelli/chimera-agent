import { describe, expect, it } from "vitest";

import { folderName, isInsideFolder, queuePollInterval, taskCounts } from "@/components/code/side/rules";
import { contextPercent, readContext, type TurnContextFacts } from "@/lib/conversation-side";
import type { CodeTurnQueueItem } from "@/lib/types";

function item(state: CodeTurnQueueItem["state"]): CodeTurnQueueItem {
  return { id: state, client_id: state, created_at: 1, position: null, session_id: "s", source: "terminal", state };
}

describe("the side cards' rules (C5)", () => {
  it("names a folder by its last segment, with either separator and a trailing one", () => {
    expect(folderName("C:\\work\\shop\\")).toBe("shop");
    expect(folderName("/home/me/shop")).toBe("shop");
    expect(folderName("shop")).toBe("shop");
  });

  it("puts a job in a folder only when its directory is that folder or inside it", () => {
    expect(isInsideFolder("C:\\Work\\Shop\\api", "c:/work/shop")).toBe(true);
    expect(isInsideFolder("/home/me/shop", "/home/me/shop/")).toBe(true);
    // A sibling whose name starts the same is not inside it.
    expect(isInsideFolder("/home/me/shopping", "/home/me/shop")).toBe(false);
    // POSIX paths keep their case.
    expect(isInsideFolder("/home/me/Shop", "/home/me/shop")).toBe(false);
    // No folder holds no jobs, rather than every job.
    expect(isInsideFolder("/anything", "")).toBe(false);
  });

  it("never reads the queue with the window hidden, often while it holds work, rarely otherwise", () => {
    expect(queuePollInterval([item("pending")], true)).toBe(false);
    expect(queuePollInterval([item("pending")], false)).toBe(10_000);
    expect(queuePollInterval([item("running")], false)).toBe(10_000);
    expect(queuePollInterval([item("done"), item("cancelled")], false)).toBe(60_000);
    expect(queuePollInterval(undefined, false)).toBe(60_000);
  });

  it("counts running work from all three sources and waiting turns apart", () => {
    const counts = taskCounts([], [item("running"), item("pending"), item("failed")], 2, "/w");
    expect(counts).toMatchObject({ queueRunning: 1, queueWaiting: 1, works: 2, running: 3 });
  });

  it("reads a percent only against a known window, floored and clamped", () => {
    expect(contextPercent(99_999, 100_000)).toBe(99);
    expect(contextPercent(150_000, 100_000)).toBe(100);
    expect(contextPercent(50_000, null)).toBeNull();
    expect(contextPercent(50_000, 0)).toBeNull();
  });

  it("takes the live frame only from a turn still running, and the peak from the newest receipt that has one", () => {
    const finished = (peak: number | null, window: number | null = 200_000): TurnContextFacts => ({
      context: { used: 1, window },
      done: { context_peak_tokens: peak },
    });
    const running: TurnContextFacts = { context: { used: 42_000, window: 200_000 }, done: null };
    expect(readContext([finished(80_000), running])).toEqual({
      live: { used: 42_000, window: 200_000 },
      peak: { used: 80_000, window: 200_000 },
    });
    // A failed turn is not running; a receipt with no peak is skipped for the one before it.
    expect(readContext([finished(80_000), finished(null), { ...running, failed: true }])).toEqual({
      live: null,
      peak: { used: 80_000, window: 200_000 },
    });
    expect(readContext([])).toEqual({ live: null, peak: null });
  });
});
