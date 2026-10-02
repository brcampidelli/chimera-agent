import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { applyLayout, hiddenItems, type HiddenItem, type Layout, type LayoutAction } from "@/lib/layout/model";
import { loadLayout, saveLayout } from "@/lib/layout/store";

/** How many steps "undo" can go back. Enough for a burst of closing; not a history of the day. */
const UNDO_DEPTH = 20;

interface LayoutApi {
  layout: Layout;
  /** Apply an action. False when it was refused (a rule said no, or nothing would change). */
  dispatch: (action: LayoutAction) => boolean;
  /** Put back the layout before the last applied action. */
  undo: () => void;
  /** Take a layout read from elsewhere (the server) as the current one, without making it a step to
   *  undo: nobody did anything, the screen just learned what it already was. */
  hydrate: (layout: Layout) => void;
  /** Mark the start of a new gesture, so the next resize is its own step to undo rather than the tail
   *  of the previous drag of the same region. */
  settle: () => void;
  canUndo: boolean;
  /** What the person hid, each with the action that brings it back. */
  hidden: HiddenItem[];
}

const LayoutContext = createContext<LayoutApi | null>(null);

/**
 * The layout, for every screen that draws a part of it.
 *
 * Kept apart from `AgentProvider` on purpose: the agent's state changes on every streamed token, and
 * a panel moving must not redraw the transcript, nor a token redraw the panels.
 */
export function LayoutProvider({
  children,
  initial,
  persist = true,
}: {
  children: ReactNode;
  initial?: Layout;
  /** False for a window that draws one conversation: it reads the person's layout and writes none of
   *  it, because the main window owns it and a change made elsewhere would land there unseen. */
  persist?: boolean;
}) {
  const [layout, setLayout] = useState<Layout>(() => initial ?? loadLayout());
  const past = useRef<Layout[]>([]);
  const [canUndo, setCanUndo] = useState(false);
  // The value `dispatch` compares against, so two actions in one tick see each other.
  const current = useRef(layout);
  // The region the last action resized. A drag is one resize per pointer move; it is ONE step to undo.
  const resizing = useRef<string | null>(null);

  useEffect(() => {
    if (persist) saveLayout(layout);
  }, [layout, persist]);

  const dispatch = useCallback((action: LayoutAction) => {
    const next = applyLayout(current.current, action);
    if (next === current.current) return false;
    const continuing = action.type === "resize" && resizing.current === action.region;
    resizing.current = action.type === "resize" ? action.region : null;
    if (!continuing) past.current = [...past.current, current.current].slice(-UNDO_DEPTH);
    current.current = next;
    setLayout(next);
    setCanUndo(true);
    return true;
  }, []);

  const undo = useCallback(() => {
    resizing.current = null;
    const previous = past.current.pop();
    if (!previous) return;
    current.current = previous;
    setLayout(previous);
    setCanUndo(past.current.length > 0);
  }, []);

  const settle = useCallback(() => {
    resizing.current = null;
  }, []);

  const hydrate = useCallback((next: Layout) => {
    current.current = next;
    setLayout(next);
  }, []);

  const value = useMemo<LayoutApi>(
    () => ({ layout, dispatch, undo, settle, hydrate, canUndo, hidden: hiddenItems(layout) }),
    [layout, dispatch, undo, settle, hydrate, canUndo],
  );
  return <LayoutContext.Provider value={value}>{children}</LayoutContext.Provider>;
}

export function useLayout(): LayoutApi {
  const api = useContext(LayoutContext);
  if (!api) throw new Error("useLayout must be used inside <LayoutProvider>");
  return api;
}
