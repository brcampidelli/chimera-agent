import type { CSSProperties, ReactNode } from "react";
import { Brain, Check, CircleDollarSign, Cpu, Wrench, X } from "lucide-react";

import { AgentBatchBoard, AgentBatchSummary } from "@/components/Agents";
import { Fusion } from "@/components/Fusion";
import { ChangesCard } from "@/components/code/side/ChangesCard";
import { ConnectorsCard } from "@/components/code/side/ConnectorsCard";
import { ContextCard } from "@/components/code/side/ContextCard";
import { EnvironmentCard } from "@/components/code/side/EnvironmentCard";
import { TasksCard } from "@/components/code/side/TasksCard";
import { JobsPanel } from "@/components/JobsPanel";
import { MachinePanel } from "@/components/MachinePanel";
import { useOptionalAgentBatch } from "@/lib/agent-batch";
import { useAgent } from "@/lib/agent-context";
import { useT } from "@/lib/i18n";
import type { PanelId } from "@/lib/layout/model";

/**
 * What each movable panel draws, whichever zone it is in (dynamic screen, phase 4).
 *
 * These were the sections of the right panel. Each reads the shared agent state itself rather than
 * being handed it, which is what lets it be drawn in the left sidebar or the bottom dock unchanged.
 * A panel with nothing to say renders nothing, and its frame goes with it (see `PanelFrame`), so a
 * moved panel never leaves an empty box behind. The tool list used to say "No tools" and the
 * token and memory panels drew "—" before the first turn: three boxes on the right saying nothing.
 */
function Tools() {
  const { tools } = useAgent();
  if (tools.length === 0) return null;
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
  if (report == null) return null;
  const cost = report.usd == null ? t("activity.costUnavailable") : `~ $${report.usd.toFixed(4)}`;
  return (
    <>
      <div className="flex items-center gap-2 text-sm">
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono">
          in {report.prompt_tokens} · out {report.completion_tokens}
          {(report.cache_read_tokens ?? 0) > 0 && ` · cache ${report.cache_read_tokens}`}
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-sm">
        <CircleDollarSign className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono">{cost}</span>
        {report.usd != null && <span className="text-xs text-muted-foreground">{t("activity.exclCache")}</span>}
      </div>
    </>
  );
}

function Memory() {
  const t = useT();
  const { report } = useAgent();
  // An absent count is NOT zero: a surface that does not report recall would otherwise render
  // "0 facts recalled", which is a measurement nobody took. The card goes, not the number.
  if (report?.memory_facts_used == null) return null;
  return (
    <div className="flex items-center gap-2 text-sm">
      <Brain className="h-3.5 w-3.5 text-muted-foreground" />
      <span>
        {t("activity.factsRecalled", { n: report.memory_facts_used })}
        {report.memory_layer && <span className="text-muted-foreground"> ({report.memory_layer})</span>}
      </span>
    </div>
  );
}

function FusionPanel() {
  const { report } = useAgent();
  return <Fusion report={report} />;
}

export type MovablePanel = Exclude<PanelId, "sessions" | "viewer" | "composer.config">;

/**
 * The panels with nothing to say right now, decided from the agent's state alone.
 *
 * These four draw only from the last turn, so whether they are empty is known before anything renders.
 * The dock leaves them out entirely, minimised or not and inside a tab group or not, which a rule on the
 * rendered body cannot do: a minimised panel has no body, and a tab group shows one body at a time.
 * Each condition is the one its component returns null on; the test keeps the two in step.
 */
export function useSilentPanels(): ReadonlySet<MovablePanel> {
  const { tools, report } = useAgent();
  const batch = useOptionalAgentBatch();
  const silent = new Set<MovablePanel>();
  if (tools.length === 0) silent.add("activity.tools");
  if (report == null) silent.add("activity.tokens");
  if (report?.memory_facts_used == null) silent.add("activity.memory");
  if (!report?.route_meta) silent.add("activity.fusion");
  // The batch board draws only a batch: none running and no results kept, it has nothing to say.
  if (!batch?.run) silent.add("agents.batch");
  return silent;
}

/** The panels a dock can draw, in the order the right panel had them. */
export const DOCK_PANELS: Record<MovablePanel, () => ReactNode> = {
  "activity.tools": () => <Tools />,
  "activity.tokens": () => <Tokens />,
  "activity.memory": () => <Memory />,
  "activity.fusion": () => <FusionPanel />,
  "activity.jobs": () => <JobsPanel bare />,
  "activity.machine": () => <MachinePanel />,
  "conversation.environment": () => <EnvironmentCard />,
  "conversation.context": () => <ContextCard />,
  "conversation.connectors": () => <ConnectorsCard />,
  "conversation.changes": () => <ChangesCard />,
  "conversation.tasks": () => <TasksCard />,
  "agents.batch": () => <AgentBatchBoard />,
};

/**
 * A line a panel's header carries beside its title, so a panel minimised to its header still says
 * where it is. Only the batch board has one: "6 running", then "4/6 passed".
 */
export const PANEL_SUMMARIES: Partial<Record<MovablePanel, () => ReactNode>> = {
  "agents.batch": () => <AgentBatchSummary />,
};

/**
 * The conversation's side cards (C5) describe the conversation open in THIS window: the Code screen
 * publishes it to a provider a window of its own does not have, so a card popped out would show
 * nothing. They are not offered "To a window of its own"; every other way of moving them works.
 */
export function canFloatPanel(id: MovablePanel): boolean {
  // The batch board likewise: the batch streams into THIS window's provider, and a window of its own
  // would open on an empty board while the batch ran on unseen.
  return !id.startsWith("conversation.") && id !== "agents.batch";
}

/** The title a panel shows. The machine keeps the words its section always had ("machine.title"); the
 *  others are named by the layout, the same names the hidden-panels tray lists them under. */
export function panelTitleKey(id: MovablePanel): string {
  if (id === "activity.machine") return "machine.title";
  // The batch board keeps the title its screen always had ("Agent Manager").
  if (id === "agents.batch") return "agents.title";
  return `layout.panel.${id}`;
}

/** The words a panel's header shows, where the full title does not fit beside its buttons in a 288px
 *  panel ("TAREFAS EM …", "ESTA MÁQUI…"). The full title stays the panel's accessible name and its
 *  tooltip; these are new keys, because renaming the old ones breaks every language at once. */
const SHORT_TITLE_KEYS: Partial<Record<MovablePanel, string>> = {
  "activity.jobs": "layout.panel.activity.jobs.short",
  "activity.machine": "machine.titleShort",
  "conversation.tasks": "layout.panel.conversation.tasks.short",
  "agents.batch": "layout.panel.agents.batch.short",
};

export function panelShortTitleKey(id: MovablePanel): string {
  return SHORT_TITLE_KEYS[id] ?? panelTitleKey(id);
}

export function isDockPanel(id: string): id is MovablePanel {
  return id in DOCK_PANELS;
}
