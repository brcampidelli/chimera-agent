import type { CSSProperties, ReactNode } from "react";
import { Brain, Check, CircleDollarSign, Cpu, Wrench, X } from "lucide-react";

import { Fusion } from "@/components/Fusion";
import { JobsPanel } from "@/components/JobsPanel";
import { MachinePanel } from "@/components/MachinePanel";
import { useAgent } from "@/lib/agent-context";
import { useT } from "@/lib/i18n";
import type { PanelId } from "@/lib/layout/model";

/**
 * What each movable panel draws, whichever zone it is in (dynamic screen, phase 4).
 *
 * These were the sections of the right panel. Each reads the shared agent state itself rather than
 * being handed it, which is what lets it be drawn in the left sidebar or the bottom dock unchanged.
 * Fusion and the background jobs render nothing while they have nothing to say; the frame around them
 * hides itself then (see `PanelFrame`), so a moved panel never leaves an empty box behind.
 */
function Tools() {
  const t = useT();
  const { tools } = useAgent();
  if (tools.length === 0) return <div className="text-sm text-muted-foreground">{t("activity.noTools")}</div>;
  return (
    <ul className="space-y-1.5">
      {tools.map((tool, i) => (
        // Each row rises 4px into place 40ms after the one before it, so a burst of tool calls reads as
        // the agent working through them rather than as a list appearing all at once.
        <li key={i} className="event-enter flex items-center gap-2 text-sm" style={{ "--i": i } as CSSProperties}>
          {tool.ok ? <Check className="h-3.5 w-3.5 text-ok" /> : <X className="h-3.5 w-3.5 text-bad" />}
          <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-mono text-sm">{tool.name}</span>
        </li>
      ))}
    </ul>
  );
}

function Tokens() {
  const t = useT();
  const { report } = useAgent();
  const cost =
    report == null ? null : report.usd == null ? t("activity.costUnavailable") : `~ $${report.usd.toFixed(4)}`;
  return (
    <>
      <div className="flex items-center gap-2 text-sm">
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
        {report ? (
          <span className="font-mono">
            in {report.prompt_tokens} · out {report.completion_tokens}
            {(report.cache_read_tokens ?? 0) > 0 && ` · cache ${report.cache_read_tokens}`}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-sm">
        <CircleDollarSign className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono">{cost ?? "—"}</span>
        {report && report.usd != null && <span className="text-xs text-muted-foreground">{t("activity.exclCache")}</span>}
      </div>
    </>
  );
}

function Memory() {
  const t = useT();
  const { report } = useAgent();
  return (
    <div className="flex items-center gap-2 text-sm">
      <Brain className="h-3.5 w-3.5 text-muted-foreground" />
      {/* An absent count is NOT zero: a surface that does not report recall would otherwise render
          "0 facts recalled", which is a measurement nobody took. */}
      {report && report.memory_facts_used != null ? (
        <span>
          {t("activity.factsRecalled", { n: report.memory_facts_used })}
          {report.memory_layer && <span className="text-muted-foreground"> ({report.memory_layer})</span>}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      )}
    </div>
  );
}

function FusionPanel() {
  const { report } = useAgent();
  return <Fusion report={report} />;
}

export type MovablePanel = Exclude<PanelId, "sessions" | "viewer" | "composer.config">;

/** The panels a dock can draw, in the order the right panel had them. */
export const DOCK_PANELS: Record<MovablePanel, () => ReactNode> = {
  "activity.tools": () => <Tools />,
  "activity.tokens": () => <Tokens />,
  "activity.memory": () => <Memory />,
  "activity.fusion": () => <FusionPanel />,
  "activity.jobs": () => <JobsPanel bare />,
  "activity.machine": () => <MachinePanel />,
};

/** The title a panel shows. The machine keeps the words its section always had ("machine.title"); the
 *  others are named by the layout, the same names the hidden-panels tray lists them under. */
export function panelTitleKey(id: MovablePanel): string {
  return id === "activity.machine" ? "machine.title" : `layout.panel.${id}`;
}

export function isDockPanel(id: string): id is MovablePanel {
  return id in DOCK_PANELS;
}
