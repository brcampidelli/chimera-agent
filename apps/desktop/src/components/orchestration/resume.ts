import { ApiError, getOrchestrationFrames } from "@/lib/api";
import type { OrchFrame } from "@/lib/api";

/**
 * Picking a run back up after the tab that started it went away.
 *
 * A fan-out costs a top-model decompose, N workers and a synthesis. Every frame of that lived only
 * in an SSE stream, so closing the app, reloading the page or losing the connection threw the
 * answer away and kept the bill — the cost was recorded and the product was not.
 *
 * The id is the only thing kept here. Everything else comes back from the server's transcript and
 * goes through the SAME reducer the live stream feeds, which ignores a `seq` it has already
 * applied. That is what makes replay-then-live land on the state a client that never disconnected
 * would have, rather than on a second, nearly-identical one.
 */
const KEY = "chimera.orchestration.lastRun";

export function rememberRun(runId: string): void {
  try {
    localStorage.setItem(KEY, runId);
  } catch {
    // Private mode, a full quota, a browser that refuses. Resuming is a convenience; failing to
    // remember must not be the reason a run cannot be started.
  }
}

export function forgetRun(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* see above */
  }
}

export function lastRun(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

/** The frames of the remembered run, or [] when there is none to resume. */
export async function resumeFrames(): Promise<{ runId: string; frames: OrchFrame[] }> {
  const runId = lastRun();
  if (!runId) return { runId: "", frames: [] };
  try {
    const { frames } = await getOrchestrationFrames(runId);
    // An id whose transcript is gone — pruned, or a different home — is not an error worth showing.
    // It is a stale key, and the honest response is to drop it and start clean.
    if (frames.length === 0) forgetRun();
    return { runId, frames };
  } catch (err) {
    // 404 is the server saying the transcript is gone — pruned past `MAX_RUNS`, or a different
    // home directory. That is a dead key, and keeping it makes every mount from here on issue a
    // request that cannot succeed. Anything else — offline, the server still starting, a proxy
    // hiccup — is "could not ask", where forgetting would throw away a run still worth resuming.
    if (err instanceof ApiError && err.status === 404) forgetRun();
    return { runId: "", frames: [] };
  }
}
