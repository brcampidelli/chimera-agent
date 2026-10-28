import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { listCodeProjects, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ApiError: actual.ApiError,
    getApprovals: vi.fn(async () => []),
    getSavedBypass: vi.fn(async () => ({ projects: 0, conversations: 0 })),
    reactivateSavedBypass: vi.fn(),
    listCodeSessions: vi.fn(),
    listRunningTurns: vi.fn(async () => []),
    listCodeProjects: vi.fn(),
    registerCodeProject: vi.fn(),
    migrateShellGrants: vi.fn(async () => ({})),
    listArchivedCodeSessions: vi.fn(async () => []),
    archiveCodeSession: vi.fn(),
    unarchiveCodeSession: vi.fn(),
    markCodeSessionSeen: vi.fn(async () => ({ changed: true })),
    forkCodeSession: vi.fn(),
    getCodeSessionRaw: vi.fn(),
  };
});

function session(id: string, workspace: string, updated_at: number, state: string) {
  return { id, title: id, workspace, turns: 1, updated_at, running: false, state };
}

/** The project headers on screen, top to bottom, by the path each one carries as its title. */
function projectOrder(): string[] {
  return screen
    .getAllByRole("button")
    .map((button) => button.getAttribute("title") ?? "")
    .filter((title) => title.startsWith("/home/me/"));
}

/**
 * The C3 blocked filter and the project registry meet here: the filter narrows the SAME list the
 * sidebar shows — pinned first, a hidden folder still hidden, empty projects left out — rather than
 * building an order of its own.
 */
describe("SessionSidebar — the blocked filter keeps the project order", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([
      session("idle newest", "/home/me/idle-one", 300, "idle"),
      session("recent asks", "/home/me/recent", 200, "blocked"),
      session("pinned asks", "/home/me/pinned-one", 100, "blocked"),
      session("hidden asks", "/home/me/hidden", 400, "blocked"),
    ] as never);
    vi.mocked(listCodeProjects).mockResolvedValue([
      { path: "/home/me/pinned-one", alias: "", pinned: true },
      { path: "/home/me/hidden", alias: "", hidden: true },
      { path: "/home/me/empty", alias: "" },
    ] as never);
  });

  it("lists pinned first, then recent, and leaves the hidden folder out", async () => {
    renderWithProviders(
      <SessionSidebar workspace="" activeSession={null} onResume={vi.fn()} onNew={vi.fn()} onProject={vi.fn()} />,
    );

    await screen.findByTitle("/home/me/empty");
    expect(projectOrder()).toEqual([
      "/home/me/pinned-one",
      "/home/me/idle-one",
      "/home/me/recent",
      "/home/me/empty",
    ]);
  });

  it("narrows to blocked conversations in the same order, without empty or hidden folders", async () => {
    renderWithProviders(
      <SessionSidebar workspace="" activeSession={null} onResume={vi.fn()} onNew={vi.fn()} onProject={vi.fn()} />,
    );

    // Two, not three: the blocked conversation in the hidden folder is not shown by the filter.
    await screen.findByTitle("/home/me/empty");
    await userEvent.click(await screen.findByRole("button", { name: /Waiting for you \(2\)/ }));

    expect(projectOrder()).toEqual(["/home/me/pinned-one", "/home/me/recent"]);
    expect(screen.getByTitle("pinned asks")).toBeInTheDocument();
    expect(screen.queryByTitle("idle newest")).toBeNull();
    expect(screen.queryByTitle("hidden asks")).toBeNull();
  });
});
