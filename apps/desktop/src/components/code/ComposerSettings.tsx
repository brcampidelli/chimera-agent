import type { ReactNode } from "react";
import { ChevronDown, Minus } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { cn } from "@/lib/utils";

/**
 * Who runs the turn, at what cost profile, on which model: minimised to one line of chips (dynamic
 * screen, phase 4). The pickers take four rows above the composer, and they are set far less often than
 * they are looked past.
 *
 * It never closes, and the posture note beside it is not part of it: the warning that commands would
 * run on this machine with no OS sandbox stays on screen whatever this is set to, which is why the
 * layout refuses to close `composer.config` at all.
 */
export function ComposerSettings({ summary, children }: { summary: string[]; children: ReactNode }) {
  const t = useT();
  const { layout, dispatch } = useLayout();
  const minimized = layout.panels["composer.config"].mode === "minimized";
  const name = t("layout.panel.composer.config");
  const label = t(minimized ? "layout.card.expand" : "layout.card.minimize", { name });

  const toggle = (
    <Tooltip label={label}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={!minimized}
        onClick={() => dispatch({ type: "set-mode", panel: "composer.config", mode: minimized ? "open" : "minimized" })}
        className={cn(
          "shrink-0 rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
          "hover:bg-surface-hover hover:text-foreground",
          focusRing,
        )}
      >
        {minimized ? <ChevronDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
      </button>
    </Tooltip>
  );

  if (minimized) {
    return (
      <div className="flex min-w-0 items-center gap-1.5" data-testid="composer-settings-line">
        <div className="flex min-w-0 flex-1 flex-wrap gap-1">
          {summary.map((chip) => (
            <span key={chip} className="rounded-chip border border-hairline px-2 py-0.5 text-xs text-muted-foreground">
              {chip}
            </span>
          ))}
        </div>
        {toggle}
      </div>
    );
  }
  return (
    <div className="flex min-w-0 items-start gap-1.5">
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">{children}</div>
      {toggle}
    </div>
  );
}
