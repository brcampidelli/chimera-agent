import { useCallback, useSyncExternalStore } from "react";

/**
 * Desktop notifications: the preferences, the one function that shows one, and the cron watcher's
 * bookkeeping.
 *
 * Every preference here is OFF by default and lives in this window's storage, which is per install.
 * An app that starts sending desktop notifications without being asked is one people turn off
 * entirely, including for the one that mattered — so each kind is its own opt-in, and the Settings
 * card asks the operating system for permission at the moment one is switched on.
 *
 * The preferences are read through a tiny external store rather than each component's own
 * `useState(localStorage…)`: the end-of-turn switch exists both in the conversation's header and in
 * Settings › General, and two copies of a boolean that each read storage once at mount disagree the
 * moment either is pressed.
 */

/** The end-of-turn notification. The key predates this module (the conversation's header button). */
export const NOTIFY_ON_FINISH_KEY = "chimera.notifyOnFinish";
/** Only for turns that ran at least this many seconds. 0 = every turn, which is what the header
 *  button has always done. */
export const NOTIFY_MIN_SECONDS_KEY = "chimera.notifyMinSeconds";
export const NOTIFY_APPROVALS_KEY = "chimera.notifyApprovals";
export const NOTIFY_CRON_KEY = "chimera.notifyCronFailures";
/** What the cron watcher has already seen, so a reload never repeats a notification. */
export const CRON_NOTICE_STATE_KEY = "chimera.notifyCronState";

/** Fired on this window when a preference is written here; `storage` covers the other windows. */
const CHANGED = "chimera:notify-pref";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    // Private mode, disabled storage, a sandboxed webview. A preference is never worth throwing:
    // the answer is the default, which for every key here is "off".
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // The preference just won't survive a restart.
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function readFlag(key: string): boolean {
  return read(key) === "1";
}

export function writeFlag(key: string, on: boolean): void {
  write(key, on ? "1" : "0");
}

/** The threshold in whole seconds. Anything unreadable is 0, the behaviour before the threshold. */
export function readMinSeconds(): number {
  const n = Number(read(NOTIFY_MIN_SECONDS_KEY));
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

export function writeMinSeconds(seconds: number): void {
  write(NOTIFY_MIN_SECONDS_KEY, String(Math.max(0, Math.floor(seconds))));
}

/** One boolean preference, kept in step with every other reader of the same key. */
export function useNotifyFlag(key: string): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, () => readFlag(key), () => false);
  const set = useCallback((next: boolean) => writeFlag(key, next), [key]);
  return [on, set];
}

export function useNotifyMinSeconds(): [number, (seconds: number) => void] {
  const n = useSyncExternalStore(subscribe, readMinSeconds, () => 0);
  return [n, writeMinSeconds];
}

/** Whether the person is looking at the window right now. */
export function windowIsWatched(): boolean {
  return document.visibilityState === "visible" && document.hasFocus();
}

/** Ask the operating system, from a click. Says what it answered, or "unsupported". */
export async function requestNotifyPermission(): Promise<NotificationPermission | "unsupported"> {
  try {
    if (typeof Notification === "undefined") return "unsupported";
    if (Notification.permission !== "default") return Notification.permission;
    return await Notification.requestPermission();
  } catch {
    return "unsupported";
  }
}

/** Show a notification, when the user is not looking at the window.
 *
 *  A three-minute run is a reason to go and do something else, and coming back to find it finished
 *  four minutes ago is the whole complaint. No IPC and no plugin: the app is served from 127.0.0.1,
 *  which is a secure context by specification, so the Web Notification API is available on the page.
 *
 *  Only when the window is NOT focused. A notification for something the user is already watching
 *  is the fastest way to have every notification muted, including the one that mattered.
 *
 *  Never actionable: no buttons, no click handler. A notification is an OS surface outside the app's
 *  governance, and the only thing it may do is tell someone to come back and look. For the same
 *  reason every caller builds its text from fields the app wrote or the user typed — never a tool's
 *  command, an error message or anything a model or a web page said.
 *
 *  ⚠️ macOS IS UNVERIFIED. WKWebView has historically not implemented `Notification`, and there was
 *  no Mac to test on. Every call is guarded by a capability check and every failure is swallowed, so
 *  the worst case is silence rather than a crash — but "it works on macOS" is NOT a claim being made
 *  here. If it turns out not to, the fix is a native plugin, which is a bigger job than this item.
 */
export async function notifyIfAway(title: string, body: string): Promise<void> {
  try {
    if (typeof Notification === "undefined") return;
    if (windowIsWatched()) return;
    // Asked at the moment it is first needed when nobody asked earlier (the header button never
    // did): a permission prompt that appears before the user has done anything is the one people
    // deny reflexively.
    const permission =
      Notification.permission === "default"
        ? await Notification.requestPermission()
        : Notification.permission;
    if (permission !== "granted") return;
    new Notification(title, { body });
  } catch {
    // A denied permission, a webview without the API, a platform quirk — none of them are worth
    // interrupting anything over.
  }
}

// ---- Schedules -----------------------------------------------------------------------------------

/** The fields of a job the watcher reads. A subset of `CronJobOut`, so a test can build one. */
export interface CronJobSeen {
  id: string;
  name: string;
  enabled: boolean;
  disabled_by?: string;
  last_run: number | null;
  last_status?: string | null;
}

/** The statuses the daemon's own failure notice treats as an outage (`UNFINISHED` in
 *  `chimera/scheduler/delivery.py`). Not `rejected`: that run finished and its gate spoke. Not
 *  `cancelled`: the person who stopped it knows. */
const FAILED: readonly string[] = ["error", "timeout", "budget"];
const FINISHED: readonly string[] = ["ok", "rejected"];
/** Finished runs in a row that end an outage — the daemon's `RECOVERY_RUNS`, for its reason: with
 *  one, a job alternating ok/error announced "failing" and "recovered" on every tick. */
export const CRON_RECOVERY_RUNS = 2;

export interface CronNoticeState {
  /** The `last_run` already looked at, per job. A dispatch is new when its `last_run` moved. */
  seen: Record<string, number | null>;
  /** Jobs in an outage the person was told about, with the finished runs counted since. */
  told: Record<string, number>;
}

export type CronNotice =
  | { kind: "failed"; job: string; status: string }
  | { kind: "recovered"; job: string };

export function loadCronNoticeState(): CronNoticeState | null {
  const raw = read(CRON_NOTICE_STATE_KEY);
  if (!raw) return null;
  try {
    const d: unknown = JSON.parse(raw);
    if (!d || typeof d !== "object") return null;
    const { seen, told } = d as Partial<CronNoticeState>;
    if (!seen || typeof seen !== "object" || !told || typeof told !== "object") return null;
    return { seen, told };
  } catch {
    return null;
  }
}

export function saveCronNoticeState(state: CronNoticeState): void {
  try {
    localStorage.setItem(CRON_NOTICE_STATE_KEY, JSON.stringify(state));
  } catch {
    // Without storage a reload may repeat one notice; there is nowhere else to keep it.
  }
}

export function clearCronNoticeState(): void {
  try {
    localStorage.removeItem(CRON_NOTICE_STATE_KEY);
  } catch {
    // Nothing stored, nothing to forget.
  }
}

function statusOf(job: CronJobSeen): string {
  // The brake is said as itself: "switched off after N failures" is a different fact from the
  // last error, and the one that needs a person to switch it back on.
  if (!job.enabled && job.disabled_by === "brake") return "brake";
  return job.last_status ?? "";
}

/**
 * What changed since the last look, and what to say about it. Pure: the caller stores the state.
 *
 * - `previous === null` is the first look (the option was just switched on, or storage was lost):
 *   everything there is becomes the baseline and nothing is announced, or switching it on would
 *   recite last month's failures.
 * - A dispatch is new when its `last_run` moved. One new failed dispatch of a job that is not
 *   already in an outage the person was told about is one notice; the failures after it, in the
 *   same outage, are not news.
 * - `watched` is whether the window has focus. A failure seen while the person is looking at the
 *   app is not announced and does not open an outage either, so a later unwatched failure of the
 *   same job still is.
 * - Recovery is told once, after `CRON_RECOVERY_RUNS` finished runs in a row, and only for an outage
 *   that was told. Runs that happened while the app was closed are seen as one, which errs quiet.
 */
export function nextCronNotices(
  previous: CronNoticeState | null,
  jobs: readonly CronJobSeen[],
  watched: boolean,
): { state: CronNoticeState; notices: CronNotice[] } {
  const seen: Record<string, number | null> = {};
  for (const job of jobs) seen[job.id] = job.last_run;
  if (previous === null) return { state: { seen, told: {} }, notices: [] };

  const told: Record<string, number> = {};
  for (const job of jobs) {
    if (job.id in previous.told) told[job.id] = previous.told[job.id];
  }
  const notices: CronNotice[] = [];
  for (const job of jobs) {
    if (job.last_run === null) continue;
    const before = previous.seen[job.id];
    if (before !== undefined && before !== null && job.last_run <= before) continue;
    const status = statusOf(job);
    if (FAILED.includes(status) || status === "brake") {
      if (job.id in told) {
        told[job.id] = 0;
      } else if (!watched) {
        told[job.id] = 0;
        notices.push({ kind: "failed", job: job.name, status });
      }
    } else if (FINISHED.includes(status) && job.id in told) {
      const healthy = told[job.id] + 1;
      if (healthy >= CRON_RECOVERY_RUNS) {
        delete told[job.id];
        if (!watched) notices.push({ kind: "recovered", job: job.name });
      } else {
        told[job.id] = healthy;
      }
    }
  }
  return { state: { seen, told }, notices };
}
