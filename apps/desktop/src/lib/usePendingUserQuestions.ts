import { useEffect, useRef } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getUserQuestions, type UserQuestionEvent } from "@/lib/api";
import { APPROVALS_POLL_MS } from "@/lib/usePendingApprovals";

/**
 * The typed questions the owner is asked right now (`ask_user`, a design approval), from any screen.
 *
 * The same files the conversation card answers, read through `GET /api/questions`. It polls on the
 * same cadence as the approvals list, and like that list it has one owner for the timer: the status
 * bar passes `poll: true`, every other caller passes `poll: false` and reads the same cache.
 */
export function usePendingUserQuestions({
  poll,
  background = false,
  enabled = true,
}: {
  poll: boolean;
  background?: boolean;
  /** Off while nothing on screen waits for the list, so a conversation with no card does not fetch it. */
  enabled?: boolean;
}): UseQueryResult<UserQuestionEvent[]> {
  return useQuery({
    queryKey: ["user-questions"],
    // Read when the query runs, not when this renders: a test or screen that mocks the API module
    // without this export must not fail just by mounting a conversation.
    queryFn: () => getUserQuestions(),
    refetchInterval: poll ? APPROVALS_POLL_MS : false,
    refetchIntervalInBackground: poll && background,
    staleTime: 0,
    enabled,
  });
}

/**
 * Takes a question away from a conversation once the owner has answered it somewhere else.
 *
 * A question answered in the approvals modal (or in another window) leaves the server's list, and the
 * conversation card has no other way to learn that. So while the card shows `question`, a poll that
 * ran after the card appeared and no longer lists it calls `onGone`. A question whose wait has already
 * run out is left alone: its card says so and offers to send the words as the next message, and
 * taking it away here would take that offer with it.
 */
export function useRetireWhenNotWaiting(question: UserQuestionEvent | null, onGone: () => void): void {
  const id = question?.id ?? null;
  const { data, dataUpdatedAt } = usePendingUserQuestions({ poll: false, enabled: id !== null });
  const askedAt = question?.asked_at ?? null;
  const waitSeconds = question?.wait_seconds ?? null;
  const shownAt = useRef(0);
  const onGoneRef = useRef(onGone);
  onGoneRef.current = onGone;

  useEffect(() => {
    shownAt.current = Date.now();
  }, [id]);

  useEffect(() => {
    if (id === null || data === undefined) return;
    // Data from before this card appeared says nothing about it: the question may not have been on it.
    if (dataUpdatedAt < shownAt.current) return;
    if (askedAt !== null && waitSeconds !== null && askedAt * 1000 + waitSeconds * 1000 <= Date.now()) return;
    if (!data.some((row) => row.id === id)) onGoneRef.current();
  }, [id, data, dataUpdatedAt, askedAt, waitSeconds]);
}
