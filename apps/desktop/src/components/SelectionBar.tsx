import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface BarAction {
  id: string;
  label: string;
  onClick: () => void;
  /** Drawn as the destructive one. */
  danger?: boolean;
}

/**
 * What can be done to a selection, and how many it covers.
 *
 * Shared by the folders card and the Code sidebar so the two read the same: a count, the actions,
 * "clear". While a run is going the actions are disabled and the progress line takes their place in
 * the live region; when it ends, anything that failed is said in words rather than left to be
 * noticed as a row that did not change.
 */
export function SelectionBar({
  label,
  actions,
  onClear,
  busy = false,
  progress,
  notice,
  compact = false,
}: {
  /** "3 selected". */
  label: string;
  actions: readonly BarAction[];
  onClear: () => void;
  busy?: boolean;
  /** "Working… 2 of 5" while a run is going. */
  progress?: string;
  /** What the last run left to say (failures, what was skipped). */
  notice?: ReactNode;
  /** The narrow sidebar: smaller padding, actions wrap. */
  compact?: boolean;
}) {
  const t = useT();
  return (
    <div
      role="toolbar"
      aria-label={label}
      className={cn("flex flex-col gap-1.5 bg-surface-2", compact ? "px-2 py-1.5" : "px-4 py-2")}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium tabular-nums" aria-live="polite">
          {progress ?? label}
        </span>
        {actions.map((action) => (
          <Button
            key={action.id}
            size="sm"
            variant="outline"
            disabled={busy}
            className={cn("h-7 px-2.5 text-xs", action.danger && "hover:text-bad-foreground")}
            onClick={action.onClick}
          >
            {action.label}
          </Button>
        ))}
        <Button size="sm" variant="ghost" disabled={busy} className="h-7 px-2.5 text-xs" onClick={onClear}>
          {t("bulk.clear")}
        </Button>
      </div>
      {notice ? (
        <p role="alert" className="text-xs text-bad-foreground">
          {notice}
        </p>
      ) : null}
    </div>
  );
}
