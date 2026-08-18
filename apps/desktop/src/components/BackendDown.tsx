import { useEffect, useState } from "react";
import { Loader2, PlugZap, RefreshCw } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * How long the app keeps saying "starting it again" before it says "close me and open me".
 *
 * The native shell restarts the backend up to five times in two minutes — fewer if it cannot come
 * up at all — and then stops (see `main.rs`), and the user cannot see that fuse from in here — there is no IPC on this origin, only HTTP to a server
 * that is currently not answering. So this is a wait, not a report: long enough that a normal
 * restart (a second or two) never reaches it, short enough that nobody sits in front of a dead app
 * wondering whether waiting is the plan.
 */
export const PATIENCE_MS = 30_000;

/**
 * The app admits the backend is gone.
 *
 * Before this, a backend that died mid-session turned the window into a mosaic of panels each
 * offering a "Try again" that could only ever fail, and nothing anywhere said why or what to do.
 * The user's only route out was to guess that closing and reopening the app would fix it.
 *
 * A bar above the app rather than a screen replacing it, deliberately: the screens underneath are
 * stale, not wrong, and unmounting them would throw away a composed message and every open editor
 * tab over an outage that usually lasts about two seconds.
 *
 * It takes its own row rather than floating over the app. `position: fixed` was the first version
 * and it was worse in the way that only shows up on screen: the app's top strip is a toolbar on
 * most views, so a floating bar hid controls for exactly as long as the outage lasted. In flow it
 * costs a ~40px reflow when it appears and hides nothing — App wraps it and the shell in a flex
 * column so the shell shrinks to fit instead of overflowing the window.
 */
export function BackendDown({ onRetry }: { onRetry: () => void }) {
  const t = useT();
  // The timer is scoped to the mount, and the mount IS the outage: this renders when the heartbeat
  // starts failing and unmounts the moment it succeeds again. So "how long has it been down" needs
  // no clock threaded in from above and no state that can be left behind between outages.
  const [patienceSpent, setPatienceSpent] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setPatienceSpent(true), PATIENCE_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div
      // Assertive, not polite: everything the user does next will silently fail, and an
      // announcement that waits its turn arrives after they have already tried.
      role="alert"
      className={cn(
        "flex shrink-0 flex-wrap items-center justify-center gap-x-3 gap-y-1",
        "border-b border-warn/30 bg-warn/10 px-4 py-2 text-sm text-warn-foreground",
      )}
    >
      {patienceSpent ? (
        <PlugZap className="h-4 w-4 shrink-0" aria-hidden />
      ) : (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
      )}
      <span className="font-medium">{t("app.backendDown")}</span>
      <span className="text-warn-foreground/80">
        {patienceSpent ? t("app.backendStillDown") : t("app.backendRestarting")}
      </span>
      <button
        type="button"
        onClick={onRetry}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-chip bg-surface-2 px-3 py-1 text-xs font-medium",
          "text-foreground ring-1 ring-hairline transition-colors duration-1 ease-out",
          "hover:bg-surface-hover",
          focusRing,
        )}
      >
        <RefreshCw className="h-3.5 w-3.5" aria-hidden />
        {t("common.retry")}
      </button>
    </div>
  );
}
