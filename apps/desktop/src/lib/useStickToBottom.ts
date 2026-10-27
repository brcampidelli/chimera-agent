import { useCallback, useEffect, useRef, useState } from "react";

/** How close to the bottom still counts as "following along". One short line of text. */
const STICK_THRESHOLD_PX = 80;

/**
 * Follow streaming content without fighting the reader.
 *
 * The transcript previously called `scrollIntoView({ behavior: "smooth" })` in an effect that
 * depended on the live token buffer — so it fired roughly thirty times a second. Three things go
 * wrong with that:
 *
 *   1. A smooth scroll is a browser-driven animation with its own duration. Restarting it every
 *      token means it never completes, so the viewport lags permanently behind the text.
 *   2. It runs on the main thread, during the one moment the app is already busy.
 *   3. It ignores intent. Scroll up to re-read something mid-response and you are yanked back down
 *      on the next token.
 *
 * Instead: track whether the reader is still at the bottom, and if so write `scrollTop` directly —
 * instant, no animation to restart — coalesced into one write per frame. If they have scrolled up,
 * stop following and let the caller offer a way back.
 */
export function useStickToBottom<T extends HTMLElement>(
  ref: React.RefObject<T | null>,
  /** Anything whose change means new content arrived (the message list, the live buffer). */
  deps: readonly unknown[],
): { stuck: boolean; scrollToBottom: () => void } {
  const [stuck, setStuck] = useState(true);
  // The follow decision is read inside a rAF callback, so it must not go stale between renders.
  const stuckRef = useRef(true);
  const frame = useRef<number | null>(null);

  const atBottom = useCallback((el: T) => {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD_PX;
  }, []);

  // Watch the reader. Any scroll that leaves the bottom zone detaches; returning re-attaches, so
  // scrolling back down resumes following without needing the button.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const next = atBottom(el);
      stuckRef.current = next;
      setStuck((prev) => (prev === next ? prev : next));
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [ref, atBottom]);

  // Follow new content, at most once per frame however many tokens landed in it.
  useEffect(() => {
    const el = ref.current;
    if (!el || !stuckRef.current) return;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const node = ref.current;
      if (node && stuckRef.current) node.scrollTop = node.scrollHeight;
    });
    return () => {
      if (frame.current !== null) {
        cancelAnimationFrame(frame.current);
        frame.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `deps` is the caller's content signal
  }, [ref, ...deps]);

  /**
   * Jump back down, and stay there until the content stops growing.
   *
   * The jump is the one smooth scroll in this app (DESIGN.md › Motion): a discrete gesture the
   * reader asked for. But a smooth `scrollTo` fixes its target from the height at the moment of the
   * click, and a transcript with a large diff or an image keeps settling after that — a card
   * measures, a picture arrives. The animation then stopped short of the real end, and the reader
   * pressed again, and again. So once the animation has stopped moving and the bottom is still out
   * of reach, the position is written per frame, to the height of THAT frame. While the animation
   * is still moving it is left alone. The loop ends when the bottom has held for a few frames, when
   * the reader scrolls away (the scroll watcher clears `stuckRef`), or at a cap for content that
   * never stops growing.
   */
  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    stuckRef.current = true;
    setStuck(true);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    let lastTop = el.scrollTop;
    let held = 0;
    let frames = 0;
    const step = () => {
      const node = ref.current;
      if (!node || !stuckRef.current) return;
      frames += 1;
      if (atBottom(node)) {
        held += 1;
      } else {
        held = 0;
        // Two frames are the smooth scroll starting; after that, a position that has not moved is an
        // animation that stopped short, and only then is it corrected.
        if (frames > 2 && node.scrollTop === lastTop) node.scrollTop = node.scrollHeight;
      }
      lastTop = node.scrollTop;
      if (held < 4 && frames < 300) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [ref, atBottom]);

  return { stuck, scrollToBottom };
}
