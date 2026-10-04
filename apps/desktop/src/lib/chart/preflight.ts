/**
 * Whether a chart may be drawn on the app's thread, decided by running it somewhere it can be stopped.
 *
 * A chart draws in the app's own document, on the thread that also runs the composer, the approval
 * card and Settings. `withheldOf` reads the generators a spec states (a sequence of four million rows
 * in 125 bytes held Vega for 29.6 s), but a spec's cost can also come from its data: a bin step over
 * the data's extent, an impute across many groups. Those cannot be sized without running the spec.
 * So before a chart is drawn, its dataflow runs in a worker (`preflight.worker.ts`) with no renderer:
 * past {@link CHART_BUDGET_MS} the worker is terminated and the chart is not drawn, and a chart that
 * makes more than {@link CHART_MAX_ITEMS} items is not drawn either.
 *
 * Fails closed. With no worker (or one that does not load) the chart is not drawn and the card says
 * the check did not run: drawing unchecked is exactly what this exists to stop.
 */

/** The most scene items a chart may make and still be drawn here: one SVG node each. */
export const CHART_MAX_ITEMS = 50_000;

/** How long a chart's dataflow may run off the app's thread before it is called too heavy. */
export const CHART_BUDGET_MS = 3_000;

// How long the worker may take to load Vega before the check counts as not having run. Separate from
// the budget so a slow first load is not mistaken for a heavy chart.
const LOAD_BUDGET_MS = 20_000;

export type Preflight =
  | { verdict: "draw" }
  | { verdict: "heavy" }
  /** The spec does not run (Vega refused it): drawing it would fail the same way, so say so. */
  | { verdict: "failed"; error: string }
  /** The check itself did not run. */
  | { verdict: "unchecked"; error: string };

type Reply = { ready: true } | { items: number } | { error: string };

// A verdict about a spec does not change, and a chart is checked again on every redraw (a theme or
// font switch), so what was learnt is kept for as long as the spec object lives.
const known = new WeakMap<object, Preflight>();

/** Check `spec` off the app's thread. `signal` stops the check (the chart left the screen). */
export function preflight(spec: Record<string, unknown>, signal?: AbortSignal): Promise<Preflight> {
  const seen = known.get(spec);
  if (seen) return Promise.resolve(seen);
  if (typeof Worker === "undefined") return Promise.resolve({ verdict: "unchecked", error: "no worker" });
  return new Promise((resolve) => {
    let worker: Worker;
    try {
      worker = new Worker(new URL("./preflight.worker.ts", import.meta.url), { type: "module" });
    } catch (err) {
      resolve({ verdict: "unchecked", error: err instanceof Error ? err.message : String(err) });
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const settle = (result: Preflight, keep: boolean) => {
      clearTimeout(timer);
      worker.terminate();
      signal?.removeEventListener("abort", stop);
      if (keep) known.set(spec, result);
      resolve(result);
    };
    const stop = () => settle({ verdict: "unchecked", error: "stopped" }, false);
    if (signal?.aborted) {
      stop();
      return;
    }
    signal?.addEventListener("abort", stop);
    timer = setTimeout(() => settle({ verdict: "unchecked", error: "the check did not start" }, false), LOAD_BUDGET_MS);
    worker.onerror = (event) => {
      event.preventDefault();
      settle({ verdict: "unchecked", error: event.message || "the check did not load" }, false);
    };
    worker.onmessage = (event: MessageEvent<Reply>) => {
      const reply = event.data;
      if ("ready" in reply) {
        clearTimeout(timer);
        timer = setTimeout(() => settle({ verdict: "heavy" }, true), CHART_BUDGET_MS);
        worker.postMessage(spec);
      } else if ("items" in reply) {
        settle(reply.items > CHART_MAX_ITEMS ? { verdict: "heavy" } : { verdict: "draw" }, true);
      } else {
        settle({ verdict: "failed", error: reply.error }, true);
      }
    };
  });
}
