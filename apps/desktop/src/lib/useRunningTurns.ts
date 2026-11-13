import { useQuery } from "@tanstack/react-query";
import { listRunningTurns } from "@/lib/api";

/** The one cache key for "which conversations have a turn running". Invalidate through this. */
export const RUNNING_TURNS_KEY = ["code-turns-running"] as const;

/** How often the list is asked for: it is one small in-memory list on the server. */
const RUNNING_TURNS_POLL_MS = 4000;

/**
 * The turns running now, on any conversation — one hook, one key, one interval.
 *
 * Four places asked for this list, each with its own `useQuery` and its own 4 s interval; react-query
 * shares the fetch by key, but the interval is per observer and the copies could drift apart (one had
 * `enabled`, the others did not). Reading it here keeps them identical. Polling pauses with the window
 * hidden: react-query does not run `refetchInterval` in the background unless told to, and this does
 * not tell it to.
 */
export function useRunningTurns(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: RUNNING_TURNS_KEY,
    queryFn: listRunningTurns,
    refetchInterval: RUNNING_TURNS_POLL_MS,
    enabled: options.enabled ?? true,
  });
}
