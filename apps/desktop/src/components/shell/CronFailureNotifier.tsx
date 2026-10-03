import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { getCron } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import {
  NOTIFY_CRON_KEY,
  appIsWatched,
  clearCronNoticeState,
  loadCronNoticeState,
  nextCronNotices,
  notifyIfAway,
  saveCronNoticeState,
  useNotifyFlag,
  type CronNotice,
} from "@/lib/notify";

/** How often the schedules are read while the option is on. A schedule's finest grain is a minute. */
export const CRON_NOTICE_POLL_MS = 60_000;

/**
 * A desktop notification when a schedule starts failing, or comes back — opt-in, in
 * Settings › General › Notifications. Renders nothing.
 *
 * The Schedule screen shows a failing job only to someone who opens it, and on the desktop the
 * daemon IS the app, so the person most likely to miss a failure is the one with the window in the
 * background. Mounted in the status bar, which is under every screen.
 *
 * **Read from `GET /api/cron`, not `/api/cron/results`.** The results route returns only answers,
 * without a status field (`CronResultOut`), and a dispatch that raised writes a result line only for
 * a job whose `notify` is `always` (`chimera/scheduler/daemon.py`). The job itself is where the
 * engine records every dispatch: `last_run` moves on each attempt and `last_status` says how it
 * ended. A failure is new when `last_run` moved and the status is one the daemon's own notice calls
 * an outage; see `nextCronNotices` for the rest of the rule.
 *
 * The text is the job's name and a status this app translates. Never `last_error`: it is the
 * exception's text, which can carry whatever a provider, a tool or a web page said — the same rule
 * the daemon's channel notice follows.
 */
export function CronFailureNotifier() {
  const t = useT();
  const [on] = useNotifyFlag(NOTIFY_CRON_KEY);
  const jobs = useQuery({
    queryKey: ["cron"],
    // An arrow, so `getCron` is read when the query runs: with the option off it never is.
    queryFn: () => getCron(),
    enabled: on,
    refetchInterval: on ? CRON_NOTICE_POLL_MS : false,
    // React Query skips an interval fetch while the page is hidden, and a minimised window IS
    // hidden — so without this the watcher stopped exactly when the person walked away, and on
    // restore the next poll ran with the window focused, filed the failure as seen and told no one.
    refetchIntervalInBackground: on,
  });

  // Off forgets what was seen, so switching it back on takes a new baseline instead of reciting
  // every failure that happened while it was off.
  useEffect(() => {
    if (!on) clearCronNoticeState();
  }, [on]);

  useEffect(() => {
    if (!on || !jobs.data) return;
    const { state, notices } = nextCronNotices(loadCronNoticeState(), jobs.data, appIsWatched());
    // Saved BEFORE anything is shown: a reload between the two must not show it twice.
    saveCronNoticeState(state);
    for (const notice of notices) {
      const { title, body } = cronNoticeText(notice, t);
      void notifyIfAway(title, body, { appWide: true });
    }
  }, [on, jobs.data, t]);

  return null;
}

/** Spelled out rather than built from the status, so every key is greppable. */
const STATUS_KEYS: Record<string, string> = {
  error: "notify.cron.status.error",
  timeout: "notify.cron.status.timeout",
  budget: "notify.cron.status.budget",
  brake: "notify.cron.status.brake",
};

/** A job's name is the person's (or the agent that proposed the job's) label, so it is cut short
 *  rather than trusted to be short. */
function cronNoticeText(notice: CronNotice, t: TFunc): { title: string; body: string } {
  const job = notice.job.slice(0, 80);
  if (notice.kind === "recovered") {
    return { title: t("notify.cron.recoveredTitle"), body: t("notify.cron.recovered", { job }) };
  }
  const key = STATUS_KEYS[notice.status];
  const status = key ? t(key) : notice.status;
  return { title: t("notify.cron.failedTitle"), body: t("notify.cron.failed", { job, status }) };
}
