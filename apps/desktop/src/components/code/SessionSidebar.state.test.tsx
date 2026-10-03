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
    forkCodeSession: vi.fn(),
    getCodeSessionRaw: vi.fn(),
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
 * Each conversation says what it needs (study 29, P4.1): a question waiting for you, a turn
 * running, a last turn that failed, edits you have not looked at — or nothing. The server derives the
 * state from facts; the row draws it, and the "Waiting for you" filter narrows the list to the
 * conversations that cannot go on without you.
 */
describe("SessionSidebar — what each conversation needs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("draws one labelled mark per state and nothing for an idle conversation", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "waiting" }),
      session("b", "broke the build", { state: "failed" }),
      session("c", "changed the router", { state: "review" }),
      session("d", "does nothing now", { state: "idle" }),
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
    expect(within(screen.getByTitle("does nothing now")).queryByRole("status")).toBeNull();
  });

  it("reads a question from the approvals cache as waiting before the list catches up", async () => {
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

  it("offers the waiting filter only when something waits, and narrows the list to it", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "waiting" }),
      session("b", "broke the build", { state: "failed" }),
    ] as never);
    render();

    const filter = await screen.findByRole("button", { name: /Waiting for you \(1\)/ });
    await userEvent.click(filter);

    expect(filter).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTitle("asks first")).toBeInTheDocument();
    expect(screen.queryByTitle("broke the build")).toBeNull();

    await userEvent.click(filter);
    expect(screen.getByTitle("broke the build")).toBeInTheDocument();
  });

  it("shows no filter when nothing is waiting", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session("b", "broke the build", { state: "failed" })] as never);
    render();

    await screen.findByTitle("broke the build");
    expect(screen.queryByRole("button", { name: /Waiting for you/ })).toBeNull();
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

  it("does not offer to archive a conversation that is working or waiting", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("a", "asks first", { state: "waiting" }),
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
