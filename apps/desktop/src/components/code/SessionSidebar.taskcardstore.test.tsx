import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { getSavedBypass, getTaskCardStatus, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSavedBypass: vi.fn(async () => ({ projects: [], conversations: [] })),
  getTaskCardStatus: vi.fn(),
  reactivateSavedBypass: vi.fn(),
  getApprovals: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
  renameCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

/** A task card store that failed its integrity check closes every card; the owner is told, and how to clear it. */
describe("SessionSidebar: the task card store warning", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([] as never);
    vi.mocked(getSavedBypass).mockResolvedValue({ projects: [], conversations: [] });
  });

  function render() {
    renderWithProviders(
      <SessionSidebar workspace="" activeSession={null} onResume={vi.fn()} onNew={vi.fn()} onProject={vi.fn()} />,
    );
  }

  it("shows a standing alert naming the files to delete when the store is closed", async () => {
    vi.mocked(getTaskCardStatus).mockResolvedValue({
      broken: true, files: ["task_cards.json", "task_cards.key", "task_cards.seen"],
    });
    render();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("integrity check");
    expect(alert).toHaveTextContent("task_cards.json, task_cards.key, task_cards.seen");
  });

  it("shows nothing when the store is healthy", async () => {
    vi.mocked(getTaskCardStatus).mockResolvedValue({ broken: false, files: [] });
    render();
    await vi.waitFor(() => expect(getTaskCardStatus).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
