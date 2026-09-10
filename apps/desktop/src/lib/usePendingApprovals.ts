import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getApprovals } from "@/lib/api";
import type { ApprovalQuestion } from "@/lib/types";

/**
 * How often the app asks which questions are still parked.
 *
 * Two seconds, which is what the Security screen has always used. It is not a latency target — the
 * number that matters is how long a person takes to notice and read — it is how quickly a question
 * that was answered somewhere else (another window, `chimera approve`, or the silence that refuses
 * it) stops being offered here. That is the whole staleness story: nothing computes an expiry, the
 * backend simply deletes the request file when the waiting thread resolves it
 * (`chimera/governance/pending.py`), and the next poll returns a shorter list.
 */
export const APPROVALS_POLL_MS = 2000;

/**
 * The questions a turn is parked on right now, from wherever you are standing.
 *
 * One query key, one `queryFn`, one timer. Governance polled this endpoint from its own screen and
 * the status bar now shows the same list from every screen; two components each declaring
 * `refetchInterval` would be two timers on one endpoint, and React Query only coalesces the fetches
 * that happen to overlap in flight. So the timer is a parameter, and exactly one caller owns it:
 * the status bar, which is mounted on every screen including the one Governance renders inside.
 * Every other caller passes `poll: false` and reads the same cache — `refetch()` from either side
 * updates both, because it is the same query.
 *
 * `poll` has no default on purpose. A default would let a third caller quietly become a second
 * timer, which is the exact defect this parameter exists to make impossible to introduce by
 * accident.
 */
export function usePendingApprovals({
  poll,
}: {
  poll: boolean;
}): UseQueryResult<ApprovalQuestion[]> {
  return useQuery({
    queryKey: ["approvals"],
    queryFn: getApprovals,
    refetchInterval: poll ? APPROVALS_POLL_MS : false,
    // A question that resolved elsewhere has to disappear on the next read, never be served from a
    // cache — the same rule the sandbox probe follows on the Security screen.
    staleTime: 0,
  });
}
