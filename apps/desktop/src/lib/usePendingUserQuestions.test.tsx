import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getUserQuestions, type UserQuestionEvent } from "@/lib/api";
import { useRetireWhenNotWaiting } from "@/lib/usePendingUserQuestions";

vi.mock("@/lib/api", () => ({ getUserQuestions: vi.fn(async () => []) }));

function question(over: Partial<UserQuestionEvent> = {}): UserQuestionEvent {
  return {
    id: "q1",
    kind: "user_question",
    question: "Which approach?",
    options: ["Blue"],
    allow_free_text: true,
    asked_at: Date.now() / 1000,
    wait_seconds: 300,
    ...over,
  };
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Lets a query that was started settle, so a missing call cannot pass for "not yet". */
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

describe("useRetireWhenNotWaiting — a question answered in the modal leaves the conversation", () => {
  beforeEach(() => {
    vi.mocked(getUserQuestions).mockReset();
  });

  it("takes the card away once a read after it appeared no longer lists the question", async () => {
    vi.mocked(getUserQuestions).mockResolvedValue([]);
    const onGone = vi.fn();
    renderHook(() => useRetireWhenNotWaiting(question(), onGone), { wrapper: wrapper() });

    await waitFor(() => expect(onGone).toHaveBeenCalledTimes(1));
  });

  it("keeps the card while the server still lists its question", async () => {
    vi.mocked(getUserQuestions).mockResolvedValue([question()]);
    const onGone = vi.fn();
    renderHook(() => useRetireWhenNotWaiting(question(), onGone), { wrapper: wrapper() });

    await waitFor(() => expect(getUserQuestions).toHaveBeenCalled());
    await settle();
    expect(onGone).not.toHaveBeenCalled();
  });

  it("leaves a question whose wait has run out to its own card, which keeps the owner's words", async () => {
    vi.mocked(getUserQuestions).mockResolvedValue([]);
    const onGone = vi.fn();
    const expired = question({ asked_at: Date.now() / 1000 - 400, wait_seconds: 300 });
    renderHook(() => useRetireWhenNotWaiting(expired, onGone), { wrapper: wrapper() });

    await waitFor(() => expect(getUserQuestions).toHaveBeenCalled());
    await settle();
    expect(onGone).not.toHaveBeenCalled();
  });

  it("does not read the list at all while no card is on screen", async () => {
    const onGone = vi.fn();
    renderHook(() => useRetireWhenNotWaiting(null, onGone), { wrapper: wrapper() });

    await settle();
    expect(getUserQuestions).not.toHaveBeenCalled();
    expect(onGone).not.toHaveBeenCalled();
  });
});
