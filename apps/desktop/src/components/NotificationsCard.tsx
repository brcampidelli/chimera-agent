import { useId, useState, type ReactNode } from "react";

import { Switch } from "@/components/ui/switch";
import { useT } from "@/lib/i18n";
import {
  NOTIFY_APPROVALS_KEY,
  NOTIFY_CRON_KEY,
  NOTIFY_ON_FINISH_KEY,
  requestNotifyPermission,
  useNotifyFlag,
  useNotifyMinSeconds,
} from "@/lib/notify";

/** What the operating system said, read once on mount and again after each ask. */
function currentPermission(): NotificationPermission | "unsupported" {
  try {
    return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
  } catch {
    return "unsupported";
  }
}

/**
 * Settings › General › Notifications: the three things worth a desktop notification, each off
 * until asked for.
 *
 * The end-of-turn one already existed, as a button in the conversation's header that appears only
 * after the first exchange — so the one person who most wanted it (about to start a long turn in a
 * new conversation) could not find it before starting. It is the same preference here
 * (`chimera.notifyOnFinish`); the header button stays and the two move together.
 *
 * Switching any of them on asks the operating system for permission right then, from the click.
 * A prompt that appears out of nowhere later is the one people deny by reflex; and an answer of
 * "blocked" is said here, where it can be fixed, rather than discovered as silence the day a turn
 * finishes.
 */
export function NotificationsCard() {
  const t = useT();
  const headingId = useId();
  const secondsId = useId();
  const [finish, setFinish] = useNotifyFlag(NOTIFY_ON_FINISH_KEY);
  const [approvals, setApprovals] = useNotifyFlag(NOTIFY_APPROVALS_KEY);
  const [cron, setCron] = useNotifyFlag(NOTIFY_CRON_KEY);
  const [minSeconds, setMinSeconds] = useNotifyMinSeconds();
  const [seconds, setSeconds] = useState(String(minSeconds));
  const [permission, setPermission] = useState(currentPermission);

  function enable(set: (on: boolean) => void) {
    return (on: boolean) => {
      set(on);
      if (on) void requestNotifyPermission().then(setPermission);
    };
  }

  const anyOn = finish || approvals || cron;
  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("notify.card.title")}
      </h2>
      <div className="divide-y divide-hairline">
        <NoticeRow label={t("notify.row.finish")} hint={t("code.chat.notify.hint")}>
          <Switch checked={finish} onChange={enable(setFinish)} label={t("notify.row.finish")} />
        </NoticeRow>
        <NoticeRow label={t("notify.row.minSeconds")} hint={t("notify.hint.minSeconds")} labelFor={secondsId}>
          <input
            id={secondsId}
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            className="field h-8 w-20 px-2.5 text-sm"
            value={seconds}
            disabled={!finish}
            onChange={(e) => {
              setSeconds(e.target.value);
              const n = Number(e.target.value);
              // Written as it is typed when it is a number; anything else waits for one.
              if (e.target.value !== "" && Number.isFinite(n) && n >= 0) setMinSeconds(n);
            }}
            onBlur={() => setSeconds(String(Math.max(0, Math.floor(Number(seconds) || 0))))}
          />
        </NoticeRow>
        <NoticeRow label={t("notify.row.approvals")} hint={t("notify.hint.approvals")}>
          <Switch checked={approvals} onChange={enable(setApprovals)} label={t("notify.row.approvals")} />
        </NoticeRow>
        <NoticeRow label={t("notify.row.cron")} hint={t("notify.hint.cron")}>
          <Switch checked={cron} onChange={enable(setCron)} label={t("notify.row.cron")} />
        </NoticeRow>
        {anyOn && permission !== "granted" && permission !== "default" ? (
          <p className="px-4 py-3 text-xs text-warn-foreground">
            {permission === "unsupported" ? t("notify.permission.unsupported") : t("notify.permission.denied")}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function NoticeRow({
  label,
  hint,
  labelFor,
  children,
}: {
  label: string;
  hint: string;
  /** The control's id, when it is a field that takes its name from this label. */
  labelFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        {labelFor ? (
          <label htmlFor={labelFor} className="text-sm font-medium">
            {label}
          </label>
        ) : (
          <div className="text-sm font-medium">{label}</div>
        )}
        <div className="text-xs text-muted-foreground">{hint}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}
