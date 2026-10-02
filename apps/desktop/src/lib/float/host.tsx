import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { MovablePanel } from "@/components/shell/panels";
import { useAgent } from "@/lib/agent-context";
import {
  floatUrl,
  floatWindowName,
  isFloatMessage,
  openChannel,
  type AgentSnapshot,
  type FloatMessage,
} from "@/lib/float/protocol";
import { useLayout } from "@/lib/layout/context";

/** How often the main window looks for a floating window that closed without saying so (a crash, a
 *  window the system killed). The window says so itself when it can; this is the fallback. */
export const FLOAT_POLL_MS = 1000;

interface FloatApi {
  /** The panels drawn in a window of their own right now. */
  floating: ReadonlySet<MovablePanel>;
  /** False where a window cannot be opened (no `window.open`, or already inside a floating window). */
  canFloat: boolean;
  /** Open the panel in a window. False when the window did not open: the panel stays where it was. */
  popOut: (panel: MovablePanel) => boolean;
  /** Close the panel's window and draw it in its dock again. */
  bringBack: (panel: MovablePanel) => void;
}

const NONE: FloatApi = {
  floating: new Set(),
  canFloat: false,
  popOut: () => false,
  bringBack: () => undefined,
};

const FloatContext = createContext<FloatApi>(NONE);

/**
 * The panels in windows of their own (dynamic screen, phase 7), from the main window's side.
 *
 * **Not part of the layout, on purpose.** Which panels float is state of this run, not a preference to
 * keep: a window cannot be reopened without the person asking, and a stored "floating" that outlived its
 * window (a crash, a reinstall) would leave a panel drawn nowhere. So a relaunch finds every panel in its
 * dock, and undo, "restore the default layout" and the server copy never meet a window.
 *
 * Without this provider (a test, a screen rendered alone) nothing floats and nothing offers to.
 */
export function FloatHost({ children }: { children: ReactNode }) {
  const agent = useAgent();
  const { layout, dispatch } = useLayout();
  const [floating, setFloating] = useState<ReadonlySet<MovablePanel>>(() => new Set());
  const windows = useRef(new Map<MovablePanel, Window>());
  const channel = useRef<BroadcastChannel | null>(null);
  const snapshot = useRef<AgentSnapshot>({ status: agent.status, tools: agent.tools, report: agent.report, busy: agent.busy });
  snapshot.current = { status: agent.status, tools: agent.tools, report: agent.report, busy: agent.busy };

  const forget = useCallback((panel: MovablePanel) => {
    windows.current.delete(panel);
    setFloating((prev) => {
      if (!prev.has(panel)) return prev;
      const next = new Set(prev);
      next.delete(panel);
      return next;
    });
  }, []);

  useEffect(() => {
    const ch = openChannel();
    channel.current = ch;
    if (!ch) return;
    ch.onmessage = (e: MessageEvent<unknown>) => {
      if (!isFloatMessage(e.data)) return;
      const m = e.data;
      if (m.type === "hello") ch.postMessage({ type: "agent", state: snapshot.current } satisfies FloatMessage);
      else if (m.type === "closed") forget(m.panel);
    };
    return () => {
      ch.close();
      channel.current = null;
    };
  }, [forget]);

  // The agent's state, to every floating window, whenever it changes. Nothing is sent while none floats.
  useEffect(() => {
    if (floating.size === 0) return;
    channel.current?.postMessage({
      type: "agent",
      state: { status: agent.status, tools: agent.tools, report: agent.report, busy: agent.busy },
    } satisfies FloatMessage);
  }, [floating, agent.status, agent.tools, agent.report, agent.busy]);

  // A window that closed without a word still gives its panel back.
  useEffect(() => {
    if (floating.size === 0) return;
    const timer = setInterval(() => {
      for (const [panel, w] of windows.current) if (w.closed) forget(panel);
    }, FLOAT_POLL_MS);
    return () => clearInterval(timer);
  }, [floating, forget]);

  // The main page going away (a reload, a navigation) takes its windows with it: after a reload nothing
  // here remembers them, and a panel drawn in a window nobody tracks could never come back. Closing the
  // native window does not reach this — WebView2 tears the page down without `pagehide` — so the
  // desktop's native side closes them then (`close_floats` in `src-tauri/src/main.rs`).
  useEffect(() => {
    const closeAll = () => {
      for (const w of windows.current.values()) w.close();
    };
    window.addEventListener("pagehide", closeAll);
    return () => window.removeEventListener("pagehide", closeAll);
  }, []);

  const canFloat = typeof window !== "undefined" && typeof window.open === "function";

  const popOut = useCallback(
    (panel: MovablePanel) => {
      const w = window.open(floatUrl(panel), floatWindowName(panel), "popup,width=380,height=560");
      // Refused (no native handler, a blocker): the panel stays where it was rather than going nowhere.
      if (!w) return false;
      windows.current.set(panel, w);
      setFloating((prev) => (prev.has(panel) ? prev : new Set(prev).add(panel)));
      // A panel filling the main area and in a window at once would be drawn twice.
      if (layout.maximized === panel) dispatch({ type: "maximize", panel: null });
      return true;
    },
    [layout.maximized, dispatch],
  );

  const bringBack = useCallback(
    (panel: MovablePanel) => {
      channel.current?.postMessage({ type: "return", panel } satisfies FloatMessage);
      windows.current.get(panel)?.close();
      forget(panel);
    },
    [forget],
  );

  const value = useMemo<FloatApi>(
    () => ({ floating, canFloat, popOut, bringBack }),
    [floating, canFloat, popOut, bringBack],
  );
  return <FloatContext.Provider value={value}>{children}</FloatContext.Provider>;
}

export function useFloat(): FloatApi {
  return useContext(FloatContext);
}
