import { useEffect } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

import { DOCK_PANELS, isDockPanel, panelTitleKey } from "@/components/shell/panels";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useFloat } from "@/lib/float/host";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import type { PanelId } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * Maximising a panel: it fills the main area until it is restored (dynamic screen, phase 5).
 *
 * One panel at a time, and the layout says which (`layout.maximized`). Escape always restores, from
 * anywhere, so a maximised panel is never a place a person cannot get out of; a double click is not
 * needed and not offered, because a double click on a title is also how text gets selected.
 */
export function MaximizeButton({ panel, name, className }: { panel: PanelId; name: string; className?: string }) {
  const t = useT();
  const { layout, dispatch } = useLayout();
  const on = layout.maximized === panel;
  const label = t(on ? "layout.restore" : "layout.maximize", { name });
  return (
    <Tooltip label={label}>
      <button
        type="button"
        aria-label={label}
        aria-pressed={on}
        onClick={() => dispatch({ type: "maximize", panel: on ? null : panel })}
        className={cn(
          "rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
          "hover:bg-surface-hover hover:text-foreground",
          focusRing,
          className,
        )}
      >
        {on ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
      </button>
    </Tooltip>
  );
}

/** Escape restores a maximised panel, wherever focus is. An open menu or dialog handles its own Escape
 *  first and marks the event, so this does not take a keystroke meant for it. */
export function useEscapeRestores() {
  const { layout, dispatch } = useLayout();
  const maximized = layout.maximized;
  useEffect(() => {
    if (!maximized) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      dispatch({ type: "maximize", panel: null });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized, dispatch]);
}

/** A dock panel, maximised over the main area. The viewer maximises in place on the Code screen. */
export function MaximizedPanel() {
  const t = useT();
  const { layout } = useLayout();
  const { floating } = useFloat();
  const id = layout.maximized;
  if (!id || !isDockPanel(id) || floating.has(id)) return null;
  const name = t(panelTitleKey(id));
  return (
    <section
      aria-label={name}
      className="overlay floating absolute inset-3 z-30 flex min-h-0 flex-col overflow-hidden"
    >
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <span className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {name}
        </span>
        <MaximizeButton panel={id} name={name} />
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-3">{DOCK_PANELS[id]()}</div>
    </section>
  );
}
