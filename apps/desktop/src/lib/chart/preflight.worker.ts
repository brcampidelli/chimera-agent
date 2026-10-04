/**
 * Runs a chart's dataflow off the app's thread, so a spec too heavy to draw can be stopped.
 *
 * `preflight.ts` starts this, sends one spec, and terminates it when it answers or when its time
 * budget runs out. Termination is the point: a dataflow is one synchronous run that nothing on the
 * app's own thread could interrupt, and here it can be thrown away whole.
 */
import { measureChart } from "@/lib/chart/render";

// The worker's global, typed as the little of it used here (the DOM lib types `self` as a Window).
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<Record<string, unknown>>) => void) | null;
  postMessage(message: unknown): void;
};

scope.onmessage = (event) => {
  measureChart(event.data).then(
    (items) => scope.postMessage({ items }),
    (err: unknown) => scope.postMessage({ error: err instanceof Error ? err.message : String(err) }),
  );
};

// Said once Vega is loaded, so the budget measures the chart and not the download.
scope.postMessage({ ready: true });
