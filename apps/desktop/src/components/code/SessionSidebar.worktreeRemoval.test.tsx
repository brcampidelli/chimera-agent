import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { listArchivedCodeSessions, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ApiError: actual.ApiError,
    getApprovals: vi.fn(async () => []),
    listCodeSessions: vi.fn(async () => []),
    listRunningTurns: vi.fn(async () => []),
    listArchivedCodeSessions: vi.fn(async () => []),
    archiveCodeSession: vi.fn(async () => ({ id: "s1", archived_at: 1 })),
    unarchiveCodeSession: vi.fn(async () => ({ id: "s1", archived_at: null })),
    markCodeSessionSeen: vi.fn(async () => ({ changed: true })),
    markCodeSessionDone: vi.fn(async () => ({ id: "s1", done: true })),
    forkCodeSession: vi.fn(),
    getCodeSessionRaw: vi.fn(),
    getSavedBypass: vi.fn(async () => ({ projects: [], conversations: [] })),
    reactivateSavedBypass: vi.fn(),
  };
});

const NOW_SECONDS = Date.now() / 1000;

function archived(id: string, title: string, removal: Record<string, unknown> | null) {
  return {
    id,
    title,
    workspace: "/home/me/.chimera-worktrees/chimera-wt-feat-1",
    turns: 1,
    updated_at: 0,
    running: false,
    archived_at: NOW_SECONDS - 86_400,
    archive_reason: "owner",
    worktree_removal: removal,
  };
}

async function openArchived(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: "Archived" }));
}

function render(): void {
  renderWithProviders(
    <SessionSidebar
      workspace=""
      activeSession={null}
      onResume={vi.fn()}
      onNew={vi.fn()}
      onProject={vi.fn()}
    />,
  );
}

describe("SessionSidebar — an archived conversation's worktree", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([] as never);
  });

  it("says in how many days the worktree is removed", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      archived("a", "old branch", { due_at: NOW_SECONDS + 10 * 86_400, kept: "" }),
    ] as never);
    render();
    await openArchived();

    expect(await screen.findByText("Worktree removed in 10 days")).toBeInTheDocument();
  });

  it("says why the last look kept it, and only then", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      archived("a", "dirty branch", { due_at: NOW_SECONDS - 1, kept: "dirty" }),
    ] as never);
    render();
    await openArchived();

    expect(await screen.findByText("Worktree kept: it has uncommitted changes")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/removed in/)).not.toBeInTheDocument());
  });

  it("says nothing when the row has no removal to show", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      archived("a", "main checkout", null),
    ] as never);
    render();
    await openArchived();

    expect(await screen.findByTitle("main checkout")).toBeInTheDocument();
    expect(screen.queryByText(/Worktree/)).not.toBeInTheDocument();
  });

  it("says 1 day, not 1 days", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      archived("a", "tomorrow", { due_at: NOW_SECONDS + 3600, kept: "" }),
    ] as never);
    render();
    await openArchived();

    expect(await screen.findByText("Worktree removed in 1 day")).toBeInTheDocument();
  });

  it("names the ignored files that kept it", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      archived("a", "has a local db", { due_at: NOW_SECONDS - 1, kept: "ignored_files" }),
    ] as never);
    render();
    await openArchived();

    expect(
      await screen.findByText("Worktree kept: it holds ignored files that were not copied from the project"),
    ).toBeInTheDocument();
  });
});
