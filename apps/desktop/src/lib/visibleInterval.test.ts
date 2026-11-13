import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setVisibleInterval } from "@/lib/visibleInterval";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("setVisibleInterval", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("ticks while visible, stops while hidden, and ticks once at once on return", () => {
    const tick = vi.fn();
    const cancel = setVisibleInterval(tick, 1000);
    vi.advanceTimersByTime(3000);
    expect(tick).toHaveBeenCalledTimes(3);

    setHidden(true);
    tick.mockClear();
    vi.advanceTimersByTime(60_000);
    expect(tick).toHaveBeenCalledTimes(0);

    setHidden(false);
    // The figure on screen is current the moment the window is, not a second later.
    expect(tick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(tick).toHaveBeenCalledTimes(3);
    cancel();
  });

  it("does not start at all in a window that is already hidden, and cancels cleanly", () => {
    setHidden(true);
    const tick = vi.fn();
    const cancel = setVisibleInterval(tick, 1000);
    vi.advanceTimersByTime(5000);
    expect(tick).not.toHaveBeenCalled();
    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(1);
    cancel();
    vi.advanceTimersByTime(5000);
    setHidden(true);
    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(1);
  });
});
