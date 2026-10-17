import type { AgentState } from "@/lib/agent-context";
import { defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";

/**
 * An agent that has finished one turn: a tool was called, the tokens and the recalled facts were
 * reported, and the turn went through fusion.
 *
 * The tool list, tokens, memory and fusion panels draw nothing before a turn, frame included
 * (ESTUDO-03 §4.3). A test about what a panel does when moved, grouped or closed needs the panel on
 * screen, the way it is after the first turn; what they do before one is asserted in `Dock.test.tsx`.
 */
export const AGENT_AFTER_A_TURN: Partial<Omit<AgentState, "publish">> = {
  tools: [{ name: "read_file", ok: true }],
  report: {
    prompt_tokens: 120,
    completion_tokens: 30,
    usd: 0.0012,
    cache_read_tokens: null,
    memory_facts_used: 2,
    memory_layer: "json",
    route_meta: {
      kind: "fusion",
      aggregation: "synth",
      early_stopped: false,
      diversity: null,
      panel: [],
      judge_analysis: "",
      stages: [],
    },
  },
};

/**
 * Store the default layout without its "last turn" tab group.
 *
 * The default starts with tokens, memory and fusion as tabs of one frame. Tests about grouping panels
 * from scratch, or about a lone panel's own buttons, start from the panels side by side.
 */
export function withoutDefaultTabs(): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...defaultLayout(), tabGroups: [], activeTabs: {} }));
}
