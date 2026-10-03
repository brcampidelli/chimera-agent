import { afterEach, describe, expect, it } from "vitest";

import {
  CRON_NOTICE_STATE_KEY,
  NOTIFY_MIN_SECONDS_KEY,
  loadCronNoticeState,
  nextCronNotices,
  readFlag,
  readMinSeconds,
  saveCronNoticeState,
  type CronJobSeen,
  type CronNoticeState,
} from "@/lib/notify";

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
