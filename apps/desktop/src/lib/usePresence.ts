import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Keep a component mounted long enough for its exit animation to play.
 *
 * CSS can animate an element in but not out: by the time React unmounts it, there is nothing left to
 * animate. This is the one gap that usually justifies pulling in an animation library — ~110KB to
 * get `AnimatePresence`. It is about forty lines instead.
 *
 * ```tsx
 * const { mounted, state, onAnimationEnd } = usePresence(open);
 * if (!mounted) return null;
 * return <div data-state={state} onAnimationEnd={onAnimationEnd} className="…" />;
 * ```
 *
 * CSS keys off `[data-state="entering"]` and `[data-state="exiting"]`.
 */
export type PresenceState = "entering" | "open" | "exiting";

export function usePresence(
  open: boolean,
  /**
   * Safety net, in milliseconds. `animationend` is the real signal, but it never fires when the
   * animation was suppressed — reduced motion collapsing durations, a browser that skips animation
   * on a hidden tab, or jsdom, which does not run animations at all. Without this the child stays
   * mounted forever and the overlay never truly closes.
   */
  fallbackMs = 400,
): { mounted: boolean; state: PresenceState; onAnimationEnd: () => void } {
  const [mounted, setMounted] = useState(open);
  const [state, setState] = useState<PresenceState>(open ? "open" : "exiting");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const finishExit = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    setMounted(false);
  }, []);

  useEffect(() => {
    if (open) {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      setMounted(true);
      setState("entering");
      // Settle to "open" on the next frame so the entering styles get one frame to apply. Without
      // the double rAF the browser may coalesce mount and state change into a single style
      // recalculation, and the animation never starts.
      const raf = requestAnimationFrame(() => requestAnimationFrame(() => setState("open")));
      return () => cancelAnimationFrame(raf);
    }

    setState("exiting");
    timer.current = setTimeout(finishExit, fallbackMs);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [open, fallbackMs, finishExit]);

  // Only the exit is interesting: the enter animation ending changes nothing, and reacting to it
  // would also fire for animations bubbling up from descendants.
  const onAnimationEnd = useCallback(() => {
    if (!open) finishExit();
  }, [open, finishExit]);

  return { mounted, state, onAnimationEnd };
}
