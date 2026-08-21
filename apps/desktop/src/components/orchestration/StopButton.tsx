import { Loader2, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

import type { useStop } from "./use-stop";

/** Stop, with a way back out of "Stopping…".
 *
 *  The button used to latch: `stopping` was set on click and never cleared, so a cancel that could
 *  not be delivered — a dropped request, a run id the server no longer has — left a disabled
 *  spinner beside a run that was still going. Nothing else cleared it either, since the run stays
 *  running until a terminal frame arrives and no such frame is coming. Reloading was the only exit.
 *
 *  Now a failed ask says so and re-arms. Being able to press it again is the only honest offer
 *  available when we could not find out what happened.
 */
export function StopButton({
  stop,
  disabled = false,
  hint,
}: {
  stop: ReturnType<typeof useStop>;
  disabled?: boolean;
  hint: string;
}) {
  const t = useT();
  const busy = stop.state === "stopping";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="ghost"
        onClick={() => void stop.stop()}
        disabled={busy || disabled}
        title={hint}
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Square className="h-4 w-4" />
        )}
        {busy ? t("orch.stopping") : t("orch.stop")}
      </Button>
      {stop.state === "unknown" ? (
        <span className="text-xs text-warn-foreground">{t("orch.stopUnknown")}</span>
      ) : null}
    </div>
  );
}
