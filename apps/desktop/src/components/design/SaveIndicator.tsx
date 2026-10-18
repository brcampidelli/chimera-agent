import { AlertTriangle, Check, CloudOff, Loader2 } from "lucide-react";

import type { SaveNote, SaveState } from "@/components/design/useDesignSession";
import { Button } from "@/components/ui/button";
import { useNum, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Keys written out, so the i18n reachability check can see each one. */
const STATE_KEY: Record<Exclude<SaveState, "saved">, string> = {
  saving: "design.save.saving",
  conflict: "design.save.conflict",
  offline: "design.save.offline",
  error: "design.save.error",
};

const NOTE_KEY: Record<Exclude<SaveNote, null>, string> = {
  merged: "design.save.note.merged",
  dropped: "design.save.note.dropped",
  refused: "design.save.note.refused",
  stale: "design.save.note.stale",
  versionGone: "design.save.note.versionGone",
};

/**
 * Whether the canvas is on disk: saving, saved (with the revision), a conflict, offline. Polite live
 * region, because a person drawing should hear "Offline" without being interrupted mid-gesture.
 */
export function SaveIndicator({
  state,
  note,
  revision,
  onRetry,
}: {
  state: SaveState;
  note: SaveNote;
  revision: number;
  onRetry: () => void;
}) {
  const t = useT();
  const num = useNum();
  const Icon = state === "saving" ? Loader2 : state === "saved" ? Check : state === "offline" ? CloudOff : AlertTriangle;
  const tone =
    state === "saved" || state === "saving"
      ? "text-muted-foreground"
      : state === "offline"
        ? "text-warn-foreground"
        : "text-bad-foreground";
  return (
    <div className="flex min-w-0 items-center gap-1.5" data-save-state={state}>
      <span role="status" aria-live="polite" className={cn("inline-flex min-w-0 items-center gap-1 text-xs", tone)}>
        <Icon className={cn("h-3.5 w-3.5 shrink-0", state === "saving" && "animate-spin")} aria-hidden="true" />
        <span className="truncate">
          {state === "saved" ? t("design.save.savedAt", { n: num(revision) }) : t(STATE_KEY[state])}
          {note ? ` · ${t(NOTE_KEY[note])}` : ""}
        </span>
      </span>
      {state === "error" || state === "offline" || state === "conflict" ? (
        <Button type="button" size="sm" variant="ghost" onClick={onRetry}>
          {t(state === "conflict" ? "design.save.reload" : "design.save.retry")}
        </Button>
      ) : null}
    </div>
  );
}
