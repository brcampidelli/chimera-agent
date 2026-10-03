import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { listCodeSessions, listRunningTurns, type RunningTurn } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // The sidebar reads the waiting questions from the cache the status bar polls; none here.
  getApprovals: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

function session(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "fix the login redirect",
    workspace: "/home/me/chimera-agent",
    turns: 2,
    updated_at: 0,
    running: false,
    ...over,
  };
}

function turn(sessionId: string): RunningTurn {
  return {
    turn_id: `t-${sessionId}`,
    session_id: sessionId,
    workspace: "/home/me/chimera-agent",
    message: "audit the gateway",
    started_at: 1,
    live_since: 0,
    transcript_saved: false,
  };
}

function render() {
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

/**
 * A turn keeps running when the screen that started it goes away, so the sidebar is where a person
 * who moved to another conversation finds out that one is still working. Measured live on
 * 2026-09-29: nothing on this screen said so, and the conversation was not even in the list until
 * its turn ended.
 */
describe("SessionSidebar — which conversations are working", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listRunningTurns).mockResolvedValue([]);
  });

  it("marks the conversation whose turn is running and only that one", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session({ id: "s1", title: "fix the login redirect" }),
      session({ id: "s2", title: "rename the config keys" }),
    ] as never);
    vi.mocked(listRunningTurns).mockResolvedValue([turn("s2")]);
    render();

    const working = await screen.findAllByRole("status", { name: "Running" });

    expect(working).toHaveLength(1);
    const row = screen.getByTitle("rename the config keys");
    expect(row).toContainElement(working[0]);
  });

  it("marks a row the list itself calls running, before the running query has answered", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session({ running: true })] as never);
    render();

    expect(await screen.findByRole("status", { name: "Running" })).toBeInTheDocument();
  });

  it("marks nothing when nothing is running", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([session()] as never);
    render();

    await screen.findByTitle("fix the login redirect");

    expect(screen.queryByRole("status", { name: "Running" })).not.toBeInTheDocument();
  });

  it("asks for the list again when a turn starts, because a new conversation has no file until it ends", async () => {
    vi.mocked(listCodeSessions).mockResolvedValueOnce([session()] as never);
    vi.mocked(listCodeSessions).mockResolvedValue([
      session(),
      session({ id: "s9", title: "a task started elsewhere", turns: 0, running: true }),
    ] as never);
    vi.mocked(listRunningTurns).mockResolvedValueOnce([]);
    vi.mocked(listRunningTurns).mockResolvedValue([turn("s9")]);
    render();
    await screen.findByTitle("fix the login redirect");
    expect(screen.queryByTitle("a task started elsewhere")).not.toBeInTheDocument();

    // The next poll (every few seconds) sees the turn and the list is fetched again.
    expect(
      await screen.findByTitle("a task started elsewhere", {}, { timeout: 8000 }),
    ).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(listCodeSessions).mock.calls.length).toBeGreaterThanOrEqual(2));
  }, 15000);
});
