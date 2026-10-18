import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useEffect, useRef } from "react";

import { FULL_CONTROL_KEY } from "@/components/BridgeFullControlCard";
import type { View } from "@/components/IconRail";
import { focusRing } from "@/components/ui/focus";
import { getBridgeFullControl, setBridgeFullControl, streamUiEvents } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { formatHash, parseHash } from "@/lib/router";
import { cn } from "@/lib/utils";

/** How often the banner asks whether full control is still on: a switch turned on or off in another
 *  window, or by the owner's terminal, shows here within this. */
const POLL_MS = 10_000;
/** How long to wait before listening again after the stream was cut. */
const RECONNECT_MS = 3_000;

/**
 * Follow the backend's requests to open an address, while full control through the bridge is on.
 *
 * The backend checked the address against the app's screens; this checks it again against the
 * router's own list (`parseHash` lands anything else on the default, so a view that comes back
 * different is refused), and builds the hash itself with `formatHash` — a value is only ever a query
 * value, never a URL, so nothing here can move the window off the app.
 */
function useBridgeNavigation(
  active: boolean,
  navigate: (view: View, params?: Record<string, string>) => void,
): void {
  const go = useRef(navigate);
  go.current = navigate;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let since = -1;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const listen = async () => {
      await streamUiEvents(
        since,
        (nav) => {
          const route = parseHash(formatHash(nav.view as View, nav.params), false);
          if (route.view !== nav.view) return;
          go.current(route.view, nav.params);
        },
        (seq) => {
          since = seq;
        },
        controller.signal,
      );
      if (!controller.signal.aborted) timer = setTimeout(() => void listen(), RECONNECT_MS);
    };
    void listen();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [active]);
}

/**
 * The bar that says Claude holds full control of this app, with the one control that ends it.
 *
 * Persistent on purpose: while the switch is on, an agent the owner connected can approve, delete
 * and change the posture without asking, and the owner must be able to see that from every screen
 * and take it back in one click — not by finding the Security tab. Warn colours, not red: it is a
 * state the owner chose, not an error (the same reasoning as the access card's open doors).
 */
export function FullControlBanner({
  navigate,
}: {
  navigate: (view: View, params?: Record<string, string>) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const state = useQuery({
    queryKey: FULL_CONTROL_KEY,
    queryFn: getBridgeFullControl,
    refetchInterval: POLL_MS,
  });
  const off = useMutation({
    mutationFn: () => setBridgeFullControl(false),
    onSuccess: (next) => qc.setQueryData(FULL_CONTROL_KEY, next),
  });
  const on = Boolean(state.data?.on);
  useBridgeNavigation(on && Boolean(state.data?.active), navigate);
  if (!on) return null;
  return (
    <div
      role="status"
      data-testid="full-control-banner"
      className={cn(
        "flex shrink-0 flex-wrap items-center justify-center gap-x-3 gap-y-1",
        "border-b border-warn/30 bg-warn/10 px-4 py-2 text-sm text-warn-foreground",
      )}
    >
      <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden />
      <span className="font-medium">{t("fullControl.banner")}</span>
      <span className="text-warn-foreground/80">{t("fullControl.bannerHint")}</span>
      <button
        type="button"
        disabled={off.isPending}
        onClick={() => off.mutate()}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-chip bg-surface-2 px-3 py-1 text-xs font-medium",
          "text-foreground ring-1 ring-hairline transition-colors duration-1 ease-out",
          "hover:bg-surface-hover disabled:opacity-50",
          focusRing,
        )}
      >
        {t("fullControl.turnOff")}
      </button>
    </div>
  );
}
