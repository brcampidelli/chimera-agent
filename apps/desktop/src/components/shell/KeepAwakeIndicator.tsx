import { useQuery } from "@tanstack/react-query";
import { Coffee } from "lucide-react";

import { getConfig, getKeepAwake } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";

/** One word per reason the server sends. Literal keys, so the i18n reachability test can see them. */
function reasonText(t: TFunc, reason: string): string {
  if (reason === "turn") return t("keepAwake.reason.turn");
  if (reason === "work") return t("keepAwake.reason.work");
  if (reason === "run") return t("keepAwake.reason.run");
  if (reason === "cron") return t("keepAwake.reason.cron");
  if (reason === "always") return t("keepAwake.reason.always");
  return reason;
}

/**
 * "Keeping awake: a coding turn" — in the status bar, only while the app is actually holding the
 * machine up (`GET /api/keep-awake`, `chimera/core/keep_awake.py`).
 *
 * The honesty half of a switch that changes what the computer does. A laptop that will not sleep
 * and says nothing about why is the version of this feature people uninstall; one that names the
 * turn or the schedule holding it is one they can decide about. `active` is the keeper's account of
 * the operating system, not the setting, so the line cannot claim a hold that is not there: with the
 * mode on and nothing running, or on battery, it renders nothing.
 */
export function KeepAwakeIndicator() {
  const t = useT();
  // Asked only while the owner has the switch on. The feature ships off, and a status bar polling a
  // route every ten seconds for a line it will never show is traffic for nothing — against a server
  // older than the route, a 404 every poll, each one retried. The shared ["config"] query is the
  // one Settings already reads, so this adds no request of its own. A closure rather than the bare
  // function, so a test that mocks the API without `getConfig` still renders the bar.
  const config = useQuery({ queryKey: ["config"], queryFn: () => getConfig() });
  const on = (config.data?.keep_awake?.mode ?? "off") !== "off";
  const state = useQuery({
    queryKey: ["keep-awake"],
    queryFn: () => getKeepAwake(),
    refetchInterval: 10000,
    enabled: on,
    retry: false,
  });
  const data = state.data;
  if (!on || !data?.active) return null;
  const why = (data.reasons ?? []).map((reason) => reasonText(t, reason)).join(", ");
  const text = t("keepAwake.status", { reason: why });
  return (
    <span className="flex shrink-0 items-center gap-1">
      <Coffee aria-hidden className="h-3 w-3" />
      {text}
    </span>
  );
}
