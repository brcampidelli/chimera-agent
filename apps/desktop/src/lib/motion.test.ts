/**
 * Motion state machines.
 *
 * jsdom does not run animations, so there is nothing here about pixels. What IS testable — and what
 * actually breaks — is the state machinery around them: does the presence hook release its child,
 * does the launch flag clear, does the scroll follower respect the reader. Every bug this layer can
 * produce is a stuck state, and stuck states are exactly what these assert.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useIgnition } from "@/lib/useIgnition";
import { usePresence } from "@/lib/usePresence";
import { useStickToBottom } from "@/lib/useStickToBottom";

/** Run queued rAF callbacks. jsdom's rAF is a timer, so fake timers drive it. */
function flushFrames(): void {
  act(() => {
    vi.advanceTimersByTime(32); // two frames at 60Hz
  });
}

describe("usePresence", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("mounts immediately when opening and settles into open", () => {
    const { result, rerender } = renderHook(({ open }) => usePresence(open), {
      initialProps: { open: false },
    });
    expect(result.current.mounted).toBe(false);

    rerender({ open: true });
    expect(result.current.mounted).toBe(true);
    expect(result.current.state).toBe("entering");

    flushFrames();
    expect(result.current.state).toBe("open");
  });

  it("keeps the child mounted through the exit, then releases it on animationend", () => {
    const { result, rerender } = renderHook(({ open }) => usePresence(open), {
      initialProps: { open: true },
    });
    rerender({ open: false });

    // This is the whole reason the hook exists: React would have unmounted here, and there would be
    // nothing left on screen to animate out.
    expect(result.current.mounted).toBe(true);
    expect(result.current.state).toBe("exiting");

    act(() => result.current.onAnimationEnd());
    expect(result.current.mounted).toBe(false);
  });

  it("releases the child even when the animation never fires", () => {
    // The failure mode that matters: under reduced motion, on a hidden tab, or in a test
    // environment, `animationend` may never arrive. Without the fallback the overlay would stay
    // mounted forever and the app would look frozen.
    const { result, rerender } = renderHook(({ open }) => usePresence(open, 400), {
      initialProps: { open: true },
    });
    rerender({ open: false });
    expect(result.current.mounted).toBe(true);

    act(() => vi.advanceTimersByTime(400));
    expect(result.current.mounted).toBe(false);
  });

  it("cancels a pending exit if it is reopened mid-flight", () => {
    const { result, rerender } = renderHook(({ open }) => usePresence(open, 400), {
      initialProps: { open: true },
    });
    rerender({ open: false });
    act(() => vi.advanceTimersByTime(200)); // halfway through the exit
    rerender({ open: true });
    flushFrames();
    expect(result.current.mounted).toBe(true);

    // The stale timer must not fire and unmount a component that is open again.
    act(() => vi.advanceTimersByTime(400));
    expect(result.current.mounted).toBe(true);
  });
});

describe("useIgnition", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    sessionStorage.clear();
  });

  it("runs once and then clears itself", () => {
    const { result } = renderHook(() => useIgnition());
    expect(result.current).toBe(true);

    act(() => vi.advanceTimersByTime(980));
    // Clearing matters beyond aesthetics: the ignite class carries `will-change`, and leaving it on
    // would keep every column promoted to its own compositor layer for the whole session.
    expect(result.current).toBe(false);
  });

  it("does not replay for the rest of the session", () => {
    const first = renderHook(() => useIgnition());
    act(() => vi.advanceTimersByTime(980));
    first.unmount();

    // A remount — in dev, this is every hot reload.
    const second = renderHook(() => useIgnition());
    expect(second.result.current).toBe(false);
  });

  it("can be switched off entirely", () => {
    const { result } = renderHook(() => useIgnition(false));
    expect(result.current).toBe(false);
  });
});

describe("useStickToBottom", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  /** A scrollable element whose geometry we control. */
  function makeScroller({ scrollTop = 900 } = {}) {
    const el = document.createElement("div");
    Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(el, "clientHeight", { value: 100, configurable: true });
    let top = scrollTop;
    Object.defineProperty(el, "scrollTop", {
      get: () => top,
      set: (v: number) => {
        top = v;
      },
      configurable: true,
    });
    el.scrollTo = vi.fn();
    document.body.appendChild(el);
    return el;
  }

  it("follows new content by writing scrollTop, never by animating", () => {
    const el = makeScroller({ scrollTop: 900 }); // pinned to the bottom
    const ref = { current: el };
    const { rerender } = renderHook(({ tick }) => useStickToBottom(ref, [tick]), {
      initialProps: { tick: 0 },
    });

    el.scrollTop = 0; // simulate content growing above the viewport
    rerender({ tick: 1 });
    flushFrames();

    // An instant write, not scrollIntoView({behavior:"smooth"}). A smooth scroll restarted on every
    // token never completes, so the viewport falls permanently behind the text.
    expect(el.scrollTop).toBe(1000);
    expect(el.scrollTo).not.toHaveBeenCalled();
  });

  it("stops following once the reader scrolls up", () => {
    const el = makeScroller({ scrollTop: 900 });
    const ref = { current: el };
    const { result, rerender } = renderHook(({ tick }) => useStickToBottom(ref, [tick]), {
      initialProps: { tick: 0 },
    });
    expect(result.current.stuck).toBe(true);

    act(() => {
      el.scrollTop = 100; // well outside the 80px stick zone
      el.dispatchEvent(new Event("scroll"));
    });
    expect(result.current.stuck).toBe(false);

    rerender({ tick: 1 });
    flushFrames();
    // The reader is re-reading something. Yanking them back down is the bug.
    expect(el.scrollTop).toBe(100);
  });

  it("resumes following when the reader scrolls back down", () => {
    const el = makeScroller({ scrollTop: 100 });
    const ref = { current: el };
    const { result } = renderHook(() => useStickToBottom(ref, []));

    act(() => {
      el.scrollTop = 100;
      el.dispatchEvent(new Event("scroll"));
    });
    expect(result.current.stuck).toBe(false);

    act(() => {
      el.scrollTop = 950; // back inside the stick zone
      el.dispatchEvent(new Event("scroll"));
    });
    // No button press needed — scrolling back down is itself the intent.
    expect(result.current.stuck).toBe(true);
  });

  it("uses a smooth scroll only for the explicit jump back", () => {
    const el = makeScroller({ scrollTop: 100 });
    const ref = { current: el };
    const { result } = renderHook(() => useStickToBottom(ref, []));

    act(() => result.current.scrollToBottom());

    // One deliberate gesture the reader asked for — the one place an animated scroll is right.
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
    expect(result.current.stuck).toBe(true);
  });
});
