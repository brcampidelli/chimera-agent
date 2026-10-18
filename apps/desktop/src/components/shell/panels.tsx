import type { CSSProperties, ReactNode } from "react";
import { Brain, Check, CircleDollarSign, Cpu, Wrench, X } from "lucide-react";

import { AgentBatchBoard, AgentBatchSummary } from "@/components/Agents";
import { Fusion } from "@/components/Fusion";
import { ChangesCard, changesSilent, useChangesFacts } from "@/components/code/side/ChangesCard";
import { ConnectorsCard, connectorsSilent, useConnectorServers } from "@/components/code/side/ConnectorsCard";
import { ContextCard, contextSilent } from "@/components/code/side/ContextCard";
import { EnvironmentCard } from "@/components/code/side/EnvironmentCard";
import { JobsPanel, isBackgroundEmpty, useBackgroundWork } from "@/components/JobsPanel";
import { MachinePanel, useMachineReading } from "@/components/MachinePanel";
import { useOptionalAgentBatch } from "@/lib/agent-batch";
import { useAgent } from "@/lib/agent-context";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
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
 * The panels with nothing to show right now: the one rule "a card with nothing to show does not
 * appear", for every panel a dock can draw.
 *
 * The dock leaves these out entirely, minimised or not and inside a tab group or not. A rule on the
 * rendered body cannot do that (`has-[>[data-panel-body]:empty]` in `PanelFrame`): a minimised panel
 * has no body, and a tab group shows one body at a time. Until 0.69 only the four panels that draw
 * from the last turn were decided here, so a background-jobs panel the person had minimised, or put
 * in a tab group, drew its header over nothing on every screen (the owner's capture of 0.69.0).
 *
 * Each condition is the one its component returns null on, and the panels that read the server use the
 * component's own hook (`useBackgroundWork`, `useConnectorServers`, ...), so the two read the same
 * value. A closed panel asks nothing: it is not drawn whatever this says. "which panels are silent" in
 * `Dock.test.tsx` renders every panel in its states and checks the two agree.
 */
export function useSilentPanels(): ReadonlySet<MovablePanel> {
  const { tools, report } = useAgent();
  const batch = useOptionalAgentBatch();
  const side = useConversationSide();
  const { layout } = useLayout();
  const onScreen = (id: MovablePanel) => layout.panels[id].mode !== "closed";
  const background = useBackgroundWork(onScreen("activity.jobs"));
  const connectors = useConnectorServers(onScreen("conversation.connectors"));
  const changes = useChangesFacts(onScreen("conversation.changes"));
  const machine = useMachineReading({ enabled: onScreen("activity.machine"), poll: false });
  const silent = new Set<MovablePanel>();
  if (tools.length === 0) silent.add("activity.tools");
  if (report == null) silent.add("activity.tokens");
  if (report?.memory_facts_used == null) silent.add("activity.memory");
  if (!report?.route_meta) silent.add("activity.fusion");
  // The batch board draws only a batch: none running and no results kept, it has nothing to say.
  if (!batch?.run) silent.add("agents.batch");
  if (isBackgroundEmpty(background)) silent.add("activity.jobs");
  if (!machine) silent.add("activity.machine");
  // The conversation's cards describe the conversation on screen: with none (another screen, or the
  // Code screen before it published), each of them draws nothing.
  if (side === null) silent.add("conversation.environment");
  if (contextSilent(side)) silent.add("conversation.context");
  if (connectorsSilent(connectors)) silent.add("conversation.connectors");
  if (changesSilent(changes)) silent.add("conversation.changes");
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
  "agents.batch": () => <AgentBatchBoard />,
};

/**
 * The same panels drawn where disappearing is not an answer: maximised over the main area, or in a
 * window of its own. A dock leaves a panel with nothing to show out (`useSilentPanels`); a window the
 * person opened on purpose cannot, so a panel that has words for "nothing here" says them there.
 */
export function drawAlone(id: MovablePanel): ReactNode {
  switch (id) {
    case "activity.jobs":
      return <JobsPanel bare alone />;
    case "conversation.context":
      return <ContextCard alone />;
    case "conversation.connectors":
      return <ConnectorsCard alone />;
    case "conversation.changes":
      return <ChangesCard alone />;
    default:
      return DOCK_PANELS[id]();
  }
}

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
  "agents.batch": "layout.panel.agents.batch.short",
};

export function panelShortTitleKey(id: MovablePanel): string {
  return SHORT_TITLE_KEYS[id] ?? panelTitleKey(id);
}

export function isDockPanel(id: string): id is MovablePanel {
  return id in DOCK_PANELS;
}
