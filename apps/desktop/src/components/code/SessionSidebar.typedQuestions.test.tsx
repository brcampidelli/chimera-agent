import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { getApprovals, getUserQuestions, listCodeSessions, type UserQuestionEvent } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
  getUserQuestions: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
}));

const SESSIONS = [
  { id: "s-blog", title: "Write the blog post", workspace: "C:\\p", turns: 2, updated_at: 2 },
];

/** A typed question (`ask_user`, a design approval) is a question the conversation waits on too. */
function typedFor(sessionId: string): UserQuestionEvent {
  return {
    id: "t1",
    kind: "user_question",
    question: "Which headline?",
    options: ["A", "B"],
    allow_free_text: true,
    asked_at: Math.floor(Date.now() / 1000) - 5,
    wait_seconds: 300,
    session_id: sessionId,
  };
}

describe("the sidebar marks a conversation that waits on a typed question", () => {
  beforeEach(() => {
    vi.mocked(listCodeSessions).mockResolvedValue(
      SESSIONS as Awaited<ReturnType<typeof listCodeSessions>>,
    );
    vi.mocked(getApprovals).mockResolvedValue([]);
    vi.mocked(getUserQuestions).mockResolvedValue([]);
  });

  function mount() {
    renderWithProviders(
      <SessionSidebar workspace="C:\p" activeSession={null} onResume={vi.fn()} onNew={vi.fn()} onProject={vi.fn()} />,
    );
  }

  it("shows the conversation as waiting for the owner when its question is typed", async () => {
    vi.mocked(getUserQuestions).mockResolvedValue([typedFor("s-blog")]);
    mount();

    // The row's state is its dot, named by what it means: a screen reader and the tooltip read this.
    expect(await screen.findByRole("status", { name: "Waiting for you" })).toBeInTheDocument();
  });

  it("does not mark a conversation that asked nothing", async () => {
    vi.mocked(getUserQuestions).mockResolvedValue([typedFor("some-other-conversation")]);
    mount();

    await screen.findByText("Write the blog post");
    expect(screen.queryByRole("status", { name: "Waiting for you" })).not.toBeInTheDocument();
  });
});
