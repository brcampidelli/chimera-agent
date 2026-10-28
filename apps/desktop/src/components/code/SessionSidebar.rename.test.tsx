import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { listCodeSessions, renameCodeSession } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSavedBypass: vi.fn(async () => ({ projects: 0, conversations: 0 })),
  reactivateSavedBypass: vi.fn(),
  getApprovals: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
  renameCodeSession: vi.fn(async () => ({ id: "s1", title: "x", title_source: "owner" })),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

function session(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Leia `TAREFA.md` com read_file e execute",
    title_source: "message",
    workspace: "/home/me/chimera-agent",
    turns: 2,
    updated_at: 0,
    ...over,
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
 * Every task an orchestrator hands over opens with the same sentence, so a list titled by first
 * messages reads the same on every row (study 03, A2). The owner can name a row: from its pencil or
 * with F2 on the focused row, Enter saves, Escape leaves it as it was, and an empty name gives the
 * conversation back its automatic one.
 */
describe("SessionSidebar — naming a conversation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([session()] as never);
  });

  it("saves the typed name from the row's pencil", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: /^Name conversation Leia/ }));
    const field = screen.getByRole("textbox", { name: "Conversation name" });
    // Empty, not the automatic title: saving untouched must not freeze it as the owner's.
    expect(field).toHaveValue("");
    await user.type(field, "Abas (C-tabs){Enter}");

    await waitFor(() => expect(renameCodeSession).toHaveBeenCalled());
    expect(vi.mocked(renameCodeSession).mock.calls[0].slice(0, 2)).toEqual(["s1", "Abas (C-tabs)"]);
    expect(screen.queryByRole("textbox", { name: "Conversation name" })).not.toBeInTheDocument();
  });

  it("opens with F2 from the keyboard and Escape changes nothing", async () => {
    const user = userEvent.setup();
    render();

    const row = await screen.findByRole("button", { name: /^Leia `TAREFA.md`/ });
    row.focus();
    await user.keyboard("{F2}");
    const field = screen.getByRole("textbox", { name: "Conversation name" });
    expect(field).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(renameCodeSession).not.toHaveBeenCalled();
    // Focus goes back to the row, not to the page.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Leia `TAREFA.md`/ })).toHaveFocus());
  });

  it("starts from the owner's own name and saves empty to restore the automatic one", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      session({ title: "Mine", title_source: "owner" }),
    ] as never);
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: /^Name conversation Mine/ }));
    const field = screen.getByRole("textbox", { name: "Conversation name" });
    expect(field).toHaveValue("Mine");
    await user.clear(field);
    await user.keyboard("{Enter}");

    await waitFor(() => expect(renameCodeSession).toHaveBeenCalled());
    expect(vi.mocked(renameCodeSession).mock.calls[0].slice(0, 2)).toEqual(["s1", ""]);
  });
});
