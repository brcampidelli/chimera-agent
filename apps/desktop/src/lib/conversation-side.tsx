import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import type { Approval, Reach } from "@/lib/api";

/**
 * What the side cards of the Code screen know about the conversation on screen (C5).
 *
 * The cards are dock panels, drawn by the shell's right region (or wherever the person moved them),
 * which sits outside the Code screen. Two places know the facts they show: the Code screen knows the
 * folder and the posture it computed for it, and the conversation knows its own id, the context frame
 * of the turn running and the receipt of the last one. Each publishes its half here, the way both
 * already publish the agent's state to `AgentProvider`.
 *
 * Kept apart from `AgentProvider` on purpose: the agent's state changes on every streamed token, and
 * these facts change a few times a turn. A consumer of one does not redraw for the other.
 */
export interface ContextReading {
  used: number;
  /** The model's window when the route reported one; null means "unknown", never 128,000. */
  window: number | null;
}

export interface ConversationSide {
  /** The folder the conversation works in; "" for a conversation with no folder. */
  workspace: string;
  /** The conversation's id once it has one (a new conversation gets it with its first turn). */
  sessionId: string | null;
  /** The posture the Code screen sends with every turn: the configured reach raised by the
   *  project's shell grant, and the approval mode. */
  posture: { reach: Reach; approval: Approval } | null;
  /** The context frame of the turn running now, or null when no turn is running. */
  live: ContextReading | null;
  /** The peak prompt of the last finished turn's receipt, or null when there is none. */
  peak: ContextReading | null;
  /** Background works of this conversation that have not ended. */
  worksActive: number;
}

const EMPTY: ConversationSide = {
  workspace: "",
  sessionId: null,
  posture: null,
  live: null,
  peak: null,
  worksActive: 0,
};

interface SideApi {
  /** Null while no Code screen is mounted: the cards then have nothing to describe. */
  side: ConversationSide | null;
  publish: (next: Partial<ConversationSide>) => void;
  /** The Code screen left: every fact it and its conversation published goes with it. */
  clear: () => void;
}

const SideContext = createContext<SideApi | null>(null);

export function ConversationSideProvider({ children, value }: { children: ReactNode; value?: Partial<ConversationSide> }) {
  const [side, setSide] = useState<ConversationSide | null>(value ? { ...EMPTY, ...value } : null);
  const publish = useCallback((next: Partial<ConversationSide>) => {
    setSide((prev) => {
      const base = prev ?? EMPTY;
      // Unchanged facts keep the same object, so publishing on every render of the conversation
      // redraws no card.
      const changed = (Object.keys(next) as (keyof ConversationSide)[]).some(
        (key) => JSON.stringify(base[key]) !== JSON.stringify(next[key]),
      );
      return changed || prev === null ? { ...base, ...next } : prev;
    });
  }, []);
  const clear = useCallback(() => setSide(null), []);
  const api = useMemo(() => ({ side, publish, clear }), [side, publish, clear]);
  return <SideContext.Provider value={api}>{children}</SideContext.Provider>;
}

/** The facts, or null outside a provider and while no Code screen is mounted. */
export function useConversationSide(): ConversationSide | null {
  return useContext(SideContext)?.side ?? null;
}

const NOOP: Omit<SideApi, "side"> = { publish: () => {}, clear: () => {} };

/** Publishing, a no-op outside a provider: a conversation in a window of its own has no side cards. */
export function useConversationSidePublisher(): Omit<SideApi, "side"> {
  const api = useContext(SideContext);
  return api ? { publish: api.publish, clear: api.clear } : NOOP;
}

/** The share of the window a prompt fills, as a whole percent clamped to 0..100; null with no window.
 *  Floor, not round: "100%" must mean the window is full, not that it is 99.5% full. */
export function contextPercent(used: number, windowTokens: number | null): number | null {
  if (!windowTokens || windowTokens <= 0) return null;
  return Math.max(0, Math.min(100, Math.floor((used * 100) / windowTokens)));
}

/** The shape of a turn this reads; the conversation's own `Exchange` is wider. */
export interface TurnContextFacts {
  context?: { used: number; window: number | null };
  done: { context_peak_tokens: number | null } | null;
  failed?: boolean;
  abandoned?: boolean;
}

/**
 * What the Context card shows, read from the conversation's turns.
 *
 * Live: the last turn, while it runs, with the context frame it reported. Peak: the newest finished
 * turn whose receipt carries a peak, with the window its own context frame reported (a reopened
 * conversation has no frames, so its window is unknown and the card shows tokens without a percent).
 */
export function readContext(turns: readonly TurnContextFacts[]): { live: ContextReading | null; peak: ContextReading | null } {
  // Indexed rather than `.at(-1)`: the project targets ES2020.
  const last = turns.length > 0 ? turns[turns.length - 1] : undefined;
  const running = last !== undefined && last.done === null && !last.failed && !last.abandoned;
  const live = running && last.context ? { used: last.context.used, window: last.context.window } : null;
  let peak: ContextReading | null = null;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const tokens = turns[i].done?.context_peak_tokens;
    if (typeof tokens === "number" && tokens > 0) {
      peak = { used: tokens, window: turns[i].context?.window ?? null };
      break;
    }
  }
  return { live, peak };
}
