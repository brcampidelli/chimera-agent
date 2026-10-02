import { Dock } from "@/components/shell/Dock";
import { HideRegionButton } from "@/components/shell/RegionToggle";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAgent } from "@/lib/agent-context";

export type Status = "idle" | "thinking" | "streaming" | "done";

/** What the agent is doing, read from the shared state rather than handed down.
 *
 *  It used to take props from App, which meant only the chat could fill it. Reading the context lets
 *  the merged conversation feed the same panel — and stops App from being the one place that knows
 *  what an agent turn looks like. */
export function Activity() {
  const t = useT();
  const { status } = useAgent();
  return (
    <aside className="flex h-full w-full shrink-0 flex-col overflow-y-auto border-l border-hairline bg-card/40">
      {/* The agent's state, which is not a panel: it cannot be moved or closed (dynamic screen, phase 4
          took it out of the movable list), and hiding this whole region leaves it in the status bar. */}
      <div className="flex items-center gap-2 px-4 py-3.5">
        {/* Breathes only while something is happening. The glow is a static box-shadow and the
            pulse animates opacity — animating the shadow itself would repaint a large blurred
            area every frame for no visual gain. */}
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            status === "idle" ? "bg-muted-foreground" : "status-dot bg-accent shadow-status-dot",
          )}
        />
        <span className="text-sm font-medium">{t(`activity.${status}`)}</span>
        {/* Hiding this panel hides nothing about the agent: the status bar keeps its state and Stop. */}
        <HideRegionButton side="right" className="ml-auto" />
      </div>
      {/* Tools, tokens, memory, fusion, background jobs and the machine: each a panel that can be
          minimised, closed, reordered, or moved to the left sidebar or the bottom dock. */}
      <Dock zone="right" />
    </aside>
  );
}
