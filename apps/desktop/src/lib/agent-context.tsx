import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import type { Status } from "@/components/Activity";
import type { RouteMeta, ToolEvent } from "@/lib/types";

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
/** The bit of a turn's receipt the status bar actually reads.
 *
 *  Deliberately narrower than `TurnReport`: the chat and the coding turn report different shapes,
 *  and asking the conversation to fabricate the fields it does not have — a session id, a cache
 *  count — would be inventing data to satisfy a type. */
export interface AgentReport {
  prompt_tokens: number;
  completion_tokens: number;
  usd: number | null;
  /** Optional because the two surfaces measure different things, and an absent number is not zero:
   *  rendering "0 facts recalled" for a surface that never looked is a measurement nobody took. */
  cache_read_tokens?: number;
  memory_facts_used?: number;
  memory_layer?: string | null;
  route_meta?: RouteMeta | null;
}

export interface AgentState {
  status: Status;
  tools: ToolEvent[];
  report: AgentReport | null;
  busy: boolean;
  stop: () => void;
  /** Publish what this screen's agent is doing, so the shell can show it from anywhere.
   *
   *  The provider owns the state now rather than receiving it from App. It used to be fed by the
   *  chat screen alone, so the merged conversation would have left the footer mute — and there is a
   *  test that exists precisely to say the agent must stay visible when you navigate away. */
  publish: (next: Partial<Omit<AgentState, "publish">>) => void;
}

const AgentContext = createContext<AgentState | null>(null);

const IDLE: Omit<AgentState, "publish"> = {
  status: "idle",
  tools: [],
  report: null,
  busy: false,
  stop: () => {},
};

export function AgentProvider({
  value,
  children,
}: {
  /** Seed state. Only tests use it — the real app publishes from whichever screen is working. */
  value?: Partial<Omit<AgentState, "publish">>;
  children: ReactNode;
}) {
  const [state, setState] = useState({ ...IDLE, ...value });
  const publish = useCallback(
    (next: Partial<Omit<AgentState, "publish">>) => setState((prev) => ({ ...prev, ...next })),
    [],
  );
  // Memoised on the fields rather than the object, so a keystroke in the composer does not re-render
  // every consumer of this context.
  const memo = useMemo(
    () => ({ ...state, publish }),
    [state, publish],
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
      publish: () => {},
    }
  );
}
