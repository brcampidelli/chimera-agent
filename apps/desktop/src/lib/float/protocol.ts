import type { AgentReport } from "@/lib/agent-context";
import type { Status } from "@/components/Activity";
import { isDockPanel, type MovablePanel } from "@/components/shell/panels";
import type { ToolEvent } from "@/lib/types";

/**
 * How a panel is drawn in a window of its own (dynamic screen, phase 7), and what the two windows say to
 * each other.
 *
 * The window is the same page at the same origin, asked to draw one panel: `/?float=<panel>`. The
 * desktop's native side opens a window for exactly that address and nothing else (`is_float_url` in
 * `src-tauri/src/main.rs`), and grants it no IPC, like the main one. So the two windows talk the way two
 * tabs of one site do: a `BroadcastChannel`, which only a page of this origin can open.
 *
 * What crosses it is the agent's state (the tools it ran, the turn's receipt), because that lives in the
 * main window's memory. The panels that ask the server themselves (background jobs, the machine) need
 * nothing from it.
 */
export const FLOAT_CHANNEL = "chimera.float.v1";

/** The query parameter that makes the page draw one panel instead of the app. */
export const FLOAT_PARAM = "float";

/** The part of the agent's state a floating panel draws. `stop` stays home: a function cannot cross. */
export interface AgentSnapshot {
  status: Status;
  tools: ToolEvent[];
  report: AgentReport | null;
  busy: boolean;
}

export type FloatMessage =
  /** A floating window asks for the current state; sent when it opens. */
  | { type: "hello"; panel: MovablePanel }
  /** The main window's state, sent on every change while any panel floats, and in answer to hello. */
  | { type: "agent"; state: AgentSnapshot }
  /** A floating window is going away; its panel goes back to its dock. */
  | { type: "closed"; panel: MovablePanel }
  /** The main window took the panel back; the window that holds it closes. */
  | { type: "return"; panel: MovablePanel };

/** The address of the window for one panel, at this page's origin. */
export function floatUrl(panel: MovablePanel, origin: string = window.location.origin): string {
  return `${origin}/?${FLOAT_PARAM}=${encodeURIComponent(panel)}`;
}

/** The panel this page was opened to draw, or null for the app. Anything unrecognised is the app. */
export function floatPanelFrom(search: string): MovablePanel | null {
  const value = new URLSearchParams(search).get(FLOAT_PARAM);
  return value !== null && isDockPanel(value) ? value : null;
}

/** The query parameter that makes the page draw one conversation instead of the app. */
export const CONVERSATION_PARAM = "conversation";

/** What a session id can be: the store keeps letters, digits, `-` and `_`, at most 64 of them. The
 *  native side opens a window for this shape and no other new one (`is_float_url`). */
const SESSION_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The address of the window for one conversation, at this page's origin. */
export function conversationUrl(sessionId: string, origin: string = window.location.origin): string {
  return `${origin}/?${CONVERSATION_PARAM}=${encodeURIComponent(sessionId)}`;
}

/** The conversation this page was opened to draw, or null for the app. */
export function conversationFrom(search: string): string | null {
  const value = new URLSearchParams(search).get(CONVERSATION_PARAM);
  return value !== null && SESSION_ID.test(value) ? value : null;
}

/** The size a conversation's window opens at. Without one the shell used the webview's default,
 *  measured live (2026-09-30) as too short for the transcript to get any height under the composer. */
export const CONVERSATION_WINDOW_FEATURES = "popup,width=1040,height=860";

/** The window's name for one conversation. Opening it again focuses the window that has it. */
export function conversationWindowName(sessionId: string): string {
  return `chimera-conversation-${sessionId}`;
}

/** The window's name for one panel. Opening a panel that already floats focuses its window. */
export function floatWindowName(panel: MovablePanel): string {
  return `chimera-float-${panel.replace(/[^a-z]/gi, "-")}`;
}

/** Whether a message from the channel is one of ours. The channel is same-origin only, but a value that
 *  crossed a structured clone is checked like any value that crossed a boundary. */
export function isFloatMessage(value: unknown): value is FloatMessage {
  if (typeof value !== "object" || value === null) return false;
  const m = value as { type?: unknown; panel?: unknown; state?: unknown };
  switch (m.type) {
    case "hello":
    case "closed":
    case "return":
      return typeof m.panel === "string" && isDockPanel(m.panel);
    case "agent":
      return typeof m.state === "object" && m.state !== null && Array.isArray((m.state as AgentSnapshot).tools);
    default:
      return false;
  }
}

/** A channel, or null where there is none (an old webview, a test environment without it). */
export function openChannel(): BroadcastChannel | null {
  try {
    return typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(FLOAT_CHANNEL);
  } catch {
    return null;
  }
}
