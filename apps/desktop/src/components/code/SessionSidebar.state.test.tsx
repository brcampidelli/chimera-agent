import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import {
  ApiError,
  archiveCodeSession,
  getApprovals,
  listArchivedCodeSessions,
  listCodeSessions,
  markCodeSessionDone,
  markCodeSessionSeen,
  unarchiveCodeSession,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ApiError: actual.ApiError,
    getApprovals: vi.fn(async () => []),
    listCodeSessions: vi.fn(),
    listRunningTurns: vi.fn(async () => []),
    listArchivedCodeSessions: vi.fn(async () => []),
    archiveCodeSession: vi.fn(async () => ({ id: "s1", archived_at: 1 })),
    unarchiveCodeSession: vi.fn(async () => ({ id: "s1", archived_at: null })),
    markCodeSessionSeen: vi.fn(async () => ({ changed: true })),
    markCodeSessionDone: vi.fn(async () => ({ id: "s1", done: true })),
    forkCodeSession: vi.fn(),
    getCodeSessionRaw: vi.fn(),
    getSavedBypass: vi.fn(async () => ({ projects: 0, conversations: 0 })),
    reactivateSavedBypass: vi.fn(),
  };
});

function session(id: string, title: string, over: Record<string, unknown> = {}) {
  return { id, title, workspace: "/home/me/chimera-agent", turns: 1, updated_at: 0, running: false, ...over };
}

function render(activeSession: string | null = null) {
  renderWithProviders(
    <SessionSidebar
      workspace=""
      activeSession={activeSession}
      onResume={vi.fn()}
      onNew={vi.fn()}
      onProject={vi.fn()}
    />,
  );
}

/**
 * C3 surfaces deterministic server facts: blocked, review, completed, and pull requests. The row
 * renders those facts and the filters narrow the same grouped list without changing project order.
 */
describe("SessionSidebar — what each conversation needs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("draws blocked, review, completed and failed state labels, but no mark for idle", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "blocked" }),
      session("b", "broke the build", { state: "failed" }),
      session("c", "changed the router", { state: "review" }),
      session("d", "finished work", { state: "done" }),
      session("e", "does nothing now", { state: "idle" }),
    ] as never);
    render();

    expect(
      within(await screen.findByTitle("asks first")).getByRole("status", { name: "Waiting for you" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTitle("broke the build")).getByRole("status", { name: "Last turn failed" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTitle("changed the router")).getByRole("status", { name: "Changes to review" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTitle("finished work")).getByRole("status", { name: "Completed" }),
    ).toBeInTheDocument();
    expect(within(screen.getByTitle("does nothing now")).queryByRole("status")).toBeNull();
  });

  it("reads a question from the approvals cache as blocked before the list catches up", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "audit the gateway", { state: "running", running: true }),
    ] as never);
    vi.mocked(getApprovals).mockResolvedValue([{ id: "q1", session_id: "a" }] as never);
    render();

    expect(
      await within(await screen.findByTitle("audit the gateway")).findByRole("status", {
        name: "Waiting for you",
      }),
    ).toBeInTheDocument();
  });

  it("filters blocked and review conversations and shows their counts", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "blocked" }),
      session("b", "broke the build", { state: "failed" }),
      session("c", "changed the router", { state: "review" }),
    ] as never);
    render();

    const blocked = await screen.findByRole("button", { name: "Waiting for you (1)" });
    expect(screen.getByRole("button", { name: "For review (1)" })).toBeInTheDocument();
    await userEvent.click(blocked);

    expect(blocked).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTitle("asks first")).toBeInTheDocument();
    expect(screen.queryByTitle("changed the router")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "For review (1)" }));
    expect(screen.getByTitle("changed the router")).toBeInTheDocument();
    expect(screen.queryByTitle("asks first")).toBeNull();
  });

  it("shows no filter when nothing is blocked or waiting for review", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session("b", "broke the build", { state: "failed" })] as never);
    render();

    await screen.findByTitle("broke the build");
    expect(screen.queryByRole("button", { name: /Waiting for you \(/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /For review \(/ })).toBeNull();
  });

  it("shows the visible PR count on its row and links to the latest PR", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("c", "changed the router", {
        state: "review",
        pull_request: { number: 43, url: "https://github.com/o/r/pull/43" },
        pull_request_count: 2,
      }),
      session("d", "no pr here"),
    ] as never);
    render();

    const link = await screen.findByRole("link", { name: "2 pull request(s)" });
    expect(link).toHaveAttribute("href", "https://github.com/o/r/pull/43");
    expect(link).toHaveTextContent("2");
    expect(within(link).getByText("2")).not.toHaveClass("sr-only");
    expect(screen.getAllByRole("link", { name: /pull request/ })).toHaveLength(1);
  });

  it("lets the owner mark a conversation done and take it back, never while it works", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("c", "changed the router", { state: "review" }),
      session("d", "finished work", { state: "done" }),
      session("r", "runs now", { state: "running", running: true }),
    ] as never);
    render();

    await userEvent.click(await screen.findByRole("button", { name: "Mark changed the router as done" }));
    expect(markCodeSessionDone).toHaveBeenCalledWith("c", true);
    const reopen = screen.getByRole("button", { name: "Reopen finished work" });
    expect(reopen).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(reopen);
    expect(markCodeSessionDone).toHaveBeenCalledWith("d", false);
    expect(screen.queryByRole("button", { name: "Mark runs now as done" })).toBeNull();
  });

  it("tells the server the open conversation's edits have been seen, and only then", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("c", "changed the router", { state: "review" }),
      session("d", "does nothing now", { state: "idle" }),
    ] as never);
    render("c");

    await waitFor(() => expect(markCodeSessionSeen).toHaveBeenCalledWith("c"));
    expect(markCodeSessionSeen).toHaveBeenCalledTimes(1);
  });

  it("does not report a conversation that is open but has nothing unseen", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session("d", "does nothing now", { state: "idle" })] as never);
    render("d");

    await screen.findByTitle("does nothing now");
    expect(markCodeSessionSeen).not.toHaveBeenCalled();
  });
});

/**
 * Archiving (study 29, P4.2) moves a conversation into a collapsed section and touches nothing on
 * disk. A conversation that is working or waiting is not offered for it, and a refusal from the
 * server says why instead of looking like an ignored click.
 */
describe("SessionSidebar — the archive", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("archives a conversation from its row", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session("b", "broke the build", { state: "failed" })] as never);
    render();

    await userEvent.click(await screen.findByRole("button", { name: "Archive broke the build" }));

    expect(archiveCodeSession).toHaveBeenCalledWith("b");
  });

  it("does not offer to archive a conversation that is working or blocked", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "blocked" }),
      session("r", "runs now", { state: "running", running: true }),
    ] as never);
    render();

    await screen.findByTitle("runs now");
    expect(screen.queryByRole("button", { name: "Archive asks first" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive runs now" })).toBeNull();
  });

  it("says why when the server refuses", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session("b", "broke the build", { state: "idle" })] as never);
    vi.mocked(archiveCodeSession).mockRejectedValueOnce(new ApiError("a turn is running in it", 409));
    render();

    await userEvent.click(await screen.findByRole("button", { name: "Archive broke the build" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Not archived: it is still working or waiting for you.",
    );
  });

  it("keeps the archive collapsed and unasked until it is opened, then brings one back", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([] as never);
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      session("old", "the old migration", { archived_at: 5 }),
    ] as never);
    render();

    const toggle = await screen.findByRole("button", { name: "Archived" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(listArchivedCodeSessions).not.toHaveBeenCalled();

    await userEvent.click(toggle);
    expect(await screen.findByTitle("the old migration")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Bring back the old migration" }));
    expect(unarchiveCodeSession).toHaveBeenCalledWith("old");
  });

  it("says so when nothing is archived", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([] as never);
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([] as never);
    render();

    await userEvent.click(await screen.findByRole("button", { name: "Archived" }));

    expect(await screen.findByText(/No archived conversations/)).toBeInTheDocument();
  });
});
