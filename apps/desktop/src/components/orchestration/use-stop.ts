import { useEffect, useState } from "react";

import { cancelOrchestration } from "@/lib/api";

/** The Stop button's state, shared by the hierarchy and the crew.
 *
 *  Both set `stopping` to true and never set it back. A cancel that failed — the network dropped,
 *  the server no longer knows the run id, the process restarted — left a permanently disabled
 *  button spinning next to a run that was still going, because nothing else clears it either: the
 *  run stays `running` until a terminal frame arrives, and if the cancel never landed, one never
 *  will. The user's only way out was to reload.
 *
 *  Three states, because they mean three different things to whoever is watching:
 *  - `idle` — press it.
 *  - `stopping` — the request landed; the server stops before its next model call.
 *  - `unknown` — we asked and could not tell. The run may already be finished, or the request may
 *    have been lost. Either way the button comes back, because being able to ask again is the only
 *    honest offer we have.
 */
export type StopState = "idle" | "stopping" | "unknown";

export function useStop(runId: string, running: boolean) {
  const [state, setState] = useState<StopState>("idle");

  // A run that ended for any reason — stopped, finished, failed — leaves nothing to stop. Without
  // this the label would still read "Stopping…" over a finished run whose Stop button is gone.
  useEffect(() => {
    if (!running) setState("idle");
  }, [running]);

  async function stop() {
    if (!runId) return;
    setState("stopping");
    try {
      // `ok: false` is the server saying it has no such run in flight — a stale click on a run that
      // already ended, which is not an error and is also not a stop we performed.
      setState((await cancelOrchestration(runId)).ok ? "stopping" : "unknown");
    } catch {
      setState("unknown");
    }
  }

  return { state, stop };
}
