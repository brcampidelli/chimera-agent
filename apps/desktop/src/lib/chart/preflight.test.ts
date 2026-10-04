import { afterEach, describe, expect, it, vi } from "vitest";

import { CHART_BUDGET_MS, CHART_MAX_ITEMS, preflight } from "@/lib/chart/preflight";

/**
 * The check a chart passes before it is drawn on the app's thread (study 29, P6.1, review finding).
 *
 * jsdom has no Worker, so the worker is a fake that the test answers by hand: what is pinned is the
 * protocol around it. A chart that outlasts the budget is called heavy and its worker is terminated
 * (the termination is what makes an uninterruptible dataflow stoppable); loading Vega does not count
 * against the budget; and every way the check can fail to run ends in "not drawn", never in "drawn
 * unchecked". What the worker runs, `measureChart`, is tested in render.test.ts.
 */
class FakeWorker {
  static made: FakeWorker[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  posted: unknown[] = [];
  terminated = false;

  constructor(readonly url: URL | string) {
    FakeWorker.made.push(this);
  }
  postMessage(message: unknown) {
    this.posted.push(message);
  }
  terminate() {
    this.terminated = true;
  }
  reply(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

function worker(): FakeWorker {
  const made = FakeWorker.made[FakeWorker.made.length - 1];
  if (!made) throw new Error("no worker was started");
  return made;
}

// A fresh object each time: verdicts are remembered per spec object.
const spec = () => ({ data: { values: [{ a: 1 }] }, mark: "point", encoding: { x: { field: "a", type: "quantitative" } } });

function withFakeWorker() {
  FakeWorker.made = [];
  vi.stubGlobal("Worker", FakeWorker);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("preflight", () => {
  it("lets a chart be drawn when its dataflow finishes small, and sends the worker the spec", async () => {
    withFakeWorker();
    const s = spec();
    const verdict = preflight(s);
    worker().reply({ ready: true });
    expect(worker().posted).toEqual([s]);
    worker().reply({ items: 12 });
    await expect(verdict).resolves.toEqual({ verdict: "draw" });
    expect(worker().terminated).toBe(true);
  });

  it("calls a chart heavy when its dataflow outlasts the budget, and terminates the worker", async () => {
    vi.useFakeTimers();
    withFakeWorker();
    const verdict = preflight(spec());
    worker().reply({ ready: true });
    vi.advanceTimersByTime(CHART_BUDGET_MS - 1);
    expect(worker().terminated).toBe(false);
    vi.advanceTimersByTime(1);
    await expect(verdict).resolves.toEqual({ verdict: "heavy" });
    expect(worker().terminated).toBe(true);
  });

  it("calls a chart heavy when it makes more items than are drawn here", async () => {
    withFakeWorker();
    const verdict = preflight(spec());
    worker().reply({ ready: true });
    worker().reply({ items: CHART_MAX_ITEMS + 1 });
    await expect(verdict).resolves.toEqual({ verdict: "heavy" });
  });

  it("does not count loading Vega against the chart's budget", async () => {
    vi.useFakeTimers();
    withFakeWorker();
    const verdict = preflight(spec());
    vi.advanceTimersByTime(CHART_BUDGET_MS * 2);
    worker().reply({ ready: true });
    worker().reply({ items: 3 });
    await expect(verdict).resolves.toEqual({ verdict: "draw" });
  });

  it("says a spec Vega refuses failed, with Vega's reason", async () => {
    withFakeWorker();
    const verdict = preflight(spec());
    worker().reply({ ready: true });
    worker().reply({ error: "Unrecognized mark type" });
    await expect(verdict).resolves.toEqual({ verdict: "failed", error: "Unrecognized mark type" });
  });

  it("does not draw unchecked when there is no worker", async () => {
    vi.stubGlobal("Worker", undefined);
    await expect(preflight(spec())).resolves.toMatchObject({ verdict: "unchecked" });
  });

  it("does not draw unchecked when the worker fails to load", async () => {
    withFakeWorker();
    const verdict = preflight(spec());
    worker().onerror?.({ message: "blocked by policy", preventDefault: () => {} } as ErrorEvent);
    await expect(verdict).resolves.toEqual({ verdict: "unchecked", error: "blocked by policy" });
    expect(worker().terminated).toBe(true);
  });

  it("stops the worker when the chart leaves the screen", async () => {
    withFakeWorker();
    const stop = new AbortController();
    const verdict = preflight(spec(), stop.signal);
    worker().reply({ ready: true });
    stop.abort();
    await expect(verdict).resolves.toMatchObject({ verdict: "unchecked" });
    expect(worker().terminated).toBe(true);
  });

  it("checks a spec once, however many times it is redrawn", async () => {
    withFakeWorker();
    const s = spec();
    const first = preflight(s);
    worker().reply({ ready: true });
    worker().reply({ items: 3 });
    await first;
    await expect(preflight(s)).resolves.toEqual({ verdict: "draw" });
    expect(FakeWorker.made).toHaveLength(1);
  });
});
