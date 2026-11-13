import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listRunningTurns } from "@/lib/api";
import { useRunningTurns } from "@/lib/useRunningTurns";

vi.mock("@/lib/api", () => ({ listRunningTurns: vi.fn(async () => []) }));

/**
 * The poll for running turns, and whether a window nobody can see keeps asking.
 *
 * Written before the polls were touched, to find out whether the `refetchInterval`s already stop
 * with the window hidden. They do — react-query skips an interval tick while the page is hidden
 * unless told otherwise, and nothing here tells it — so the work on polls is this proof and the
 * manual `setInterval` clocks (see `visibleInterval.test.ts`), not a change to every query.
 */
function Consumer() {
  useRunningTurns();
  return null;
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: hidden ? "hidden" : "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

function mountConsumers(n: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {Array.from({ length: n }, (_, k) => (
        <Consumer key={k} />
      ))}
    </QueryClientProvider>,
  );
}

describe("useRunningTurns", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(listRunningTurns).mockClear();
  });

  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("asks nothing while the window is hidden and goes on when it is shown again", async () => {
    mountConsumers(1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.mocked(listRunningTurns)).toHaveBeenCalledTimes(1);

    setHidden(true);
    vi.mocked(listRunningTurns).mockClear();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(vi.mocked(listRunningTurns)).toHaveBeenCalledTimes(0);

    setHidden(false);
    await act(() => vi.advanceTimersByTimeAsync(8_000));
    expect(vi.mocked(listRunningTurns).mock.calls.length).toBeGreaterThanOrEqual(1);
  });

  it("is one request per tick however many places read the list", async () => {
    mountConsumers(4);
    await act(() => vi.advanceTimersByTimeAsync(0));
    vi.mocked(listRunningTurns).mockClear();
    await act(() => vi.advanceTimersByTimeAsync(12_000));
    // Three ticks of 4 s. Four readers share one key and one in-flight request.
    expect(vi.mocked(listRunningTurns)).toHaveBeenCalledTimes(3);
  });
});
