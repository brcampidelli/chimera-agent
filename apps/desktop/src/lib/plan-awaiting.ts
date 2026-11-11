import type { CodeApprovalEvent } from "@/lib/api";

/**
 * Clears the approval card only when it still holds the question that was answered.
 *
 * The Plan card answers by id, but the card on screen may already hold a newer question (the turn can
 * ask again right after the plan is answered). A clear that does not check the id would hide that
 * question without anyone having answered it, which is the failure this surface exists to prevent.
 */
export function clearIfStillAsked(current: CodeApprovalEvent | null, id: string): CodeApprovalEvent | null {
  return current?.id === id ? null : current;
}
