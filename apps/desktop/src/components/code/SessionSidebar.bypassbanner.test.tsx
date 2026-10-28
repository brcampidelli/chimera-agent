import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { getSavedBypass, listCodeSessions, reactivateSavedBypass } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSavedBypass: vi.fn(),
  reactivateSavedBypass: vi.fn(async () => ({
    reactivated_projects: 0, reactivated_conversations: 0, approved_waiting: 0, left_waiting: 0,
  })),
  getApprovals: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
  renameCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

/**
 * The banner after a restart lists exactly what the file saved (folder and title), and its one click
 * names exactly those entries. In the browser's page the click is refused by the server for a switch
 * on, so the button is disabled there and the reason is written beside it.
 */
describe("SessionSidebar — the saved Ignore permissions banner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([] as never);
    vi.mocked(getSavedBypass).mockResolvedValue({
      projects: ["/home/me/shop"],
      conversations: [{ id: "c1", title: "Corrigir o login" }],
    });
  });

  afterEach(() => {
    document.head.innerHTML = "";
  });

  function render() {
    renderWithProviders(
      <SessionSidebar workspace="" activeSession={null} onResume={vi.fn()} onNew={vi.fn()} onProject={vi.fn()} />,
    );
  }

  it("lists the saved folder and conversation before the one click", async () => {
    render();

    expect(await screen.findByText("/home/me/shop")).toBeInTheDocument();
    expect(screen.getByText("Corrigir o login")).toBeInTheDocument();
  });

  it("reactivates exactly the entries it listed", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: "Reactivate" }));

    await waitFor(() => expect(reactivateSavedBypass).toHaveBeenCalledTimes(1));
    expect(vi.mocked(reactivateSavedBypass).mock.calls[0]?.[0]).toEqual({
      projects: ["/home/me/shop"],
      conversations: ["c1"],
    });
  });

  it("dismisses with two empty lists, which turns nothing on", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(reactivateSavedBypass).toHaveBeenCalledTimes(1));
    expect(vi.mocked(reactivateSavedBypass).mock.calls[0]?.[0]).toEqual({ projects: [], conversations: [] });
  });

  it("in the browser's page, the reactivation is disabled and the reason is written beside it", async () => {
    document.head.innerHTML = '<meta name="chimera-screen" content="browser-page-credential">';
    render();

    const reactivate = await screen.findByRole("button", { name: "Reactivate" });
    expect(reactivate).toBeDisabled();
    expect(screen.getByText(/can only be turned on in the desktop app/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });
});
