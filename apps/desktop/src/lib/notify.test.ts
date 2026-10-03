import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APP_FOCUS_KEY,
  CRON_NOTICE_STATE_KEY,
  FOCUS_FRESH_MS,
  NOTIFY_MIN_SECONDS_KEY,
  appIsWatched,
  installFocusBeacon,
  loadCronNoticeState,
  nextCronNotices,
  readFlag,
  readMinSeconds,
  saveCronNoticeState,
  windowIsWatched,
  type CronJobSeen,
  type CronNoticeState,
} from "@/lib/notify";
import type { CronJob } from "@/lib/types";

function job(over: Partial<CronJobSeen> = {}): CronJobSeen {
  return { id: "j1", name: "nightly report", enabled: true, disabled_by: "", last_run: 100, last_status: "ok", ...over };
}

/** Run one look from a state, the way the watcher does, and hand back the next state. */
function look(state: CronNoticeState | null, jobs: CronJobSeen[], watched = false) {
  return nextCronNotices(state, jobs, watched);
}

describe("nextCronNotices — what a schedule's new dispatch is worth saying", () => {
  it("takes the first look as a baseline and announces nothing, not even a job already failing", () => {
    // Switching the option on must not recite last month's failures.
    const { notices, state } = look(null, [job({ last_status: "error" })]);
    expect(notices).toEqual([]);
    expect(state.seen).toEqual({ j1: 100 });
  });

  it("announces one new failure exactly once", () => {
    const base = look(null, [job()]).state;
    const first = look(base, [job({ last_run: 200, last_status: "error" })]);
    expect(first.notices).toEqual([{ kind: "failed", job: "nightly report", status: "error" }]);

    // The same answer read again (the next poll, or a reload with the stored state) is not news.
    const again = look(first.state, [job({ last_run: 200, last_status: "error" })]);
    expect(again.notices).toEqual([]);
  });

  it("does not announce every failure of an outage it already told about", () => {
    let state = look(null, [job()]).state;
    state = look(state, [job({ last_run: 200, last_status: "error" })]).state;
    const next = look(state, [job({ last_run: 300, last_status: "timeout" })]);
    expect(next.notices).toEqual([]);
  });

  it("says nothing while the window has focus, and still tells a later failure seen from behind", () => {
    let state = look(null, [job()]).state;
    const watched = look(state, [job({ last_run: 200, last_status: "error" })], true);
    expect(watched.notices).toEqual([]);
    state = watched.state;
    const away = look(state, [job({ last_run: 300, last_status: "error" })], false);
    expect(away.notices).toHaveLength(1);
  });

  it("tells a recovery once, after two finished runs, and only for an outage it told", () => {
    let state = look(null, [job()]).state;
    state = look(state, [job({ last_run: 200, last_status: "error" })]).state;
    const one = look(state, [job({ last_run: 300, last_status: "ok" })]);
    // One success between two errors is a provider failing every other call, not a recovery.
    expect(one.notices).toEqual([]);
    const two = look(one.state, [job({ last_run: 400, last_status: "rejected" })]);
    expect(two.notices).toEqual([{ kind: "recovered", job: "nightly report" }]);
    const three = look(two.state, [job({ last_run: 500, last_status: "ok" })]);
    expect(three.notices).toEqual([]);
  });

  it("a failure inside an outage resets the count towards its end", () => {
    let state = look(null, [job()]).state;
    state = look(state, [job({ last_run: 200, last_status: "error" })]).state;
    state = look(state, [job({ last_run: 300, last_status: "ok" })]).state;
    state = look(state, [job({ last_run: 400, last_status: "error" })]).state;
    expect(look(state, [job({ last_run: 500, last_status: "ok" })]).notices).toEqual([]);
  });

  it("names the brake as the brake, and ignores a run a person cancelled", () => {
    const base = look(null, [job(), job({ id: "j2", name: "sync" })]).state;
    const { notices } = look(base, [
      job({ last_run: 200, last_status: "error", enabled: false, disabled_by: "brake" }),
      job({ id: "j2", name: "sync", last_run: 200, last_status: "cancelled" }),
    ]);
    expect(notices).toEqual([{ kind: "failed", job: "nightly report", status: "brake" }]);
  });

  it("counts a job created after the baseline, the first time it fails", () => {
    const base = look(null, [job()]).state;
    const { notices } = look(base, [job(), job({ id: "new", name: "fresh", last_run: 150, last_status: "timeout" })]);
    expect(notices).toEqual([{ kind: "failed", job: "fresh", status: "timeout" }]);
  });
});

describe("notification preferences", () => {
  afterEach(() => localStorage.clear());

  it("are off, and the threshold zero, when nothing was ever stored", () => {
    expect(readFlag("chimera.notifyApprovals")).toBe(false);
    expect(readMinSeconds()).toBe(0);
  });

  it("read an unreadable threshold as zero rather than as a number nobody chose", () => {
    localStorage.setItem(NOTIFY_MIN_SECONDS_KEY, "abc");
    expect(readMinSeconds()).toBe(0);
    localStorage.setItem(NOTIFY_MIN_SECONDS_KEY, "-5");
    expect(readMinSeconds()).toBe(0);
    localStorage.setItem(NOTIFY_MIN_SECONDS_KEY, "90");
    expect(readMinSeconds()).toBe(90);
  });

  it("keep the watcher's state across a reload, and drop a corrupt one", () => {
    saveCronNoticeState({ seen: { j1: 5 }, told: { j1: 0 } });
    expect(loadCronNoticeState()).toEqual({ seen: { j1: 5 }, told: { j1: 0 } });
    localStorage.setItem(CRON_NOTICE_STATE_KEY, "{not json");
    expect(loadCronNoticeState()).toBeNull();
  });
});

describe("the brake, as the API reports it", () => {
  it("is a field GET /api/cron sends, so the brake branch is reachable from real data", () => {
    // Compile-time as much as run-time: if `CronJobOut` stops carrying `disabled_by`, the
    // generated type loses it and this file no longer builds.
    const fromApi: Pick<CronJob, "id" | "name" | "enabled" | "disabled_by" | "last_run" | "last_status"> = {
      id: "j1", name: "nightly report", enabled: false, disabled_by: "brake", last_run: 200, last_status: "error",
    };
    const base = look(null, [job()]).state;
    expect(look(base, [fromApi]).notices).toEqual([{ kind: "failed", job: "nightly report", status: "brake" }]);
  });
});

/**
 * "Is anyone looking?" across the app's windows. A conversation can be popped out into a window of
 * its own; with that one focused the main window is unfocused, and an app-wide notice raised there
 * would interrupt a person who is reading Chimera.
 */
describe("the focus beacon", () => {
  let focused: boolean;
  beforeEach(() => {
    localStorage.clear();
    focused = false;
    vi.spyOn(document, "hasFocus").mockImplementation(() => focused);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("counts another Chimera window's fresh focus as the app being watched, but not this window", () => {
    localStorage.setItem(APP_FOCUS_KEY, `other-window:${Date.now()}`);
    expect(windowIsWatched()).toBe(false);
    expect(appIsWatched()).toBe(true);
  });

  it("stops counting a beat that went stale, so a window that crashed while focused stops vouching", () => {
    localStorage.setItem(APP_FOCUS_KEY, `other-window:${Date.now() - FOCUS_FRESH_MS - 1}`);
    expect(appIsWatched()).toBe(false);
    localStorage.setItem(APP_FOCUS_KEY, "garbage");
    expect(appIsWatched()).toBe(false);
  });

  it("is held by the focused window and let go on blur, and never vouches for its own window", () => {
    vi.useFakeTimers();
    const uninstall = installFocusBeacon();
    expect(localStorage.getItem(APP_FOCUS_KEY)).toBeNull(); // not focused: nothing to say

    focused = true;
    window.dispatchEvent(new Event("focus"));
    expect(localStorage.getItem(APP_FOCUS_KEY)).not.toBeNull();
    // The beat is ours: after losing focus, this window does not count its own stale claim.
    focused = false;
    expect(appIsWatched()).toBe(false);

    // Kept fresh while focused...
    focused = true;
    vi.advanceTimersByTime(FOCUS_FRESH_MS * 2);
    const at = Number(localStorage.getItem(APP_FOCUS_KEY)?.split(":")[1]);
    expect(Date.now() - at).toBeLessThan(FOCUS_FRESH_MS);

    // ...and dropped when focus leaves.
    focused = false;
    window.dispatchEvent(new Event("blur"));
    expect(localStorage.getItem(APP_FOCUS_KEY)).toBeNull();
    uninstall();
  });

  it("does not erase another window's claim when this one blurs after it", () => {
    const uninstall = installFocusBeacon();
    localStorage.setItem(APP_FOCUS_KEY, `other-window:${Date.now()}`);
    window.dispatchEvent(new Event("blur"));
    expect(localStorage.getItem(APP_FOCUS_KEY)).toMatch(/^other-window:/);
    uninstall();
  });
});
