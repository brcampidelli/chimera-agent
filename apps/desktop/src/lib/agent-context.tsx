import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { Status } from "@/components/Activity";
import type { ToolEvent, TurnReport } from "@/lib/types";

/**
 * What the agent is doing, available from every screen.
 *
 * This state already existed — it just lived inside App and rendered only when the chat view was
 * showing. So navigating to Settings mid-response made the agent disappear, along with the only
 * button that could stop it. For an app whose whole subject is an agent doing work, that is the
 * most anti-agentic thing in it.
 *
 * A context rather than prop-drilling because the consumer is the status bar in the shell, which
 * sits outside the view switch entirely.
 */
export interface AgentState {
  status: Status;
  tools: ToolEvent[];
  report: TurnReport | null;
  busy: boolean;
  stop: () => void;
}

const AgentContext = createContext<AgentState | null>(null);

export function AgentProvider({ value, children }: { value: AgentState; children: ReactNode }) {
  const { status, tools, report, busy, stop } = value;
  // Memoised on the fields rather than the object: App rebuilds the object every render, and
  // without this every consumer would re-render on every keystroke in the composer.
  const memo = useMemo(
    () => ({ status, tools, report, busy, stop }),
    [status, tools, report, busy, stop],
  );
  return <AgentContext.Provider value={memo}>{children}</AgentContext.Provider>;
}

/**
 * Read the agent's state.
 *
 * Returns an idle state outside a provider rather than throwing: a component test that renders one
 * screen in isolation should not have to stand up the whole shell, and "no agent is running" is a
 * truthful answer in that situation.
 */
export function useAgent(): AgentState {
  return (
    useContext(AgentContext) ?? {
      status: "idle",
      tools: [],
      report: null,
      busy: false,
      stop: () => {},
    }
  );
}
