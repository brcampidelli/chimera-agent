import { useEffect, useRef } from "react";

import { getUiLayout, putUiLayout } from "@/lib/api";
import { useLayout } from "@/lib/layout/context";
import { defaultLayout, type Layout } from "@/lib/layout/model";
import { parseLayout } from "@/lib/layout/store";

/** How long after the last change the layout is sent: a drag is one write, not one per pixel. */
export const SYNC_DELAY_MS = 600;

function isDefault(layout: Layout): boolean {
  return JSON.stringify(layout) === JSON.stringify(defaultLayout());
}

/**
 * Keeps the layout on the server as well as in the webview (dynamic screen, phase 6).
 *
 * The owner chose, on 2026-09-29, for the layout to live where the project list already lives, because
 * the webview's storage does not survive a reinstall. The rules, in order:
 *
 * - **The first time, local goes up.** The server has nothing yet and this machine has a layout the
 *   person made: it is sent, the way the project list migrated.
 * - **After that, the server is the source.** What it has is applied at start, through the same parser
 *   as a stored layout, so a bad value falls back to the default and never breaks the screen.
 * - **A change made before the server answers wins.** Someone who hid a panel in the first second of a
 *   launch should not see it come back when a slower answer lands.
 * - **No server, no error.** The screen keeps working on the local copy.
 *
 * Mounted once, in `main.tsx`. Not in the test helper, so suites that mock the API wholesale never meet
 * a request they did not ask for.
 */
export function LayoutServerSync() {
  const { layout, hydrate } = useLayout();
  const ready = useRef(false);
  const initial = useRef(layout);
  const changedEarly = useRef(false);
  // The value the server just gave us: applying it is not a change to send back.
  const fromServer = useRef<Layout | null>(null);
  // The layout now, for the answer that arrives after an early change.
  const current = useRef(layout);
  current.current = layout;

  useEffect(() => {
    let live = true;
    getUiLayout()
      .then(({ layout: stored }) => {
        if (!live) return;
        ready.current = true;
        // A stored value of another version (or not a layout at all) is no answer: it would parse to the
        // default and throw away the layout this machine has, so the local one is kept and sent instead.
        const usable = stored !== null && stored.version === 1;
        if (changedEarly.current) {
          void putUiLayout(current.current).catch(() => undefined);
        } else if (usable) {
          const parsed = parseLayout(stored);
          fromServer.current = parsed;
          hydrate(parsed);
        } else if (!isDefault(initial.current)) {
          void putUiLayout(initial.current).catch(() => undefined);
        }
      })
      .catch(() => {
        // No server: the local copy is the layout, and changes keep being stored locally.
        if (live) ready.current = true;
      });
    return () => {
      live = false;
    };
    // Once, at start: `hydrate` is stable, so this asks the server a single time.
  }, [hydrate]);

  useEffect(() => {
    if (!ready.current) {
      if (layout !== initial.current) changedEarly.current = true;
      return;
    }
    if (layout === fromServer.current) return;
    const timer = setTimeout(() => void putUiLayout(layout).catch(() => undefined), SYNC_DELAY_MS);
    return () => clearTimeout(timer);
  }, [layout]);

  return null;
}
