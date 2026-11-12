import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import {
  archiveCodeSession,
  deleteCodeSession,
  flagCodeProject,
  listArchivedCodeSessions,
  listCodeSessions,
  unarchiveCodeSession,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSavedBypass: vi.fn(async () => ({ projects: [], conversations: [] })),
  reactivateSavedBypass: vi.fn(),
  getApprovals: vi.fn(async () => []),
  getTaskCardStatus: vi.fn(async () => ({ broken: false, files: [] })),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
  listCodeProjects: vi.fn(async () => []),
  listArchivedCodeSessions: vi.fn(async () => []),
  registerCodeProject: vi.fn(),
  flagCodeProject: vi.fn(async () => []),
  forgetCodeProject: vi.fn(),
  deleteCodeProject: vi.fn(),
  deleteCodeSession: vi.fn(),
  archiveCodeSession: vi.fn(),
  unarchiveCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

/**
 * Selection mode in the Code sidebar. Every action is the single-row call run once per ticked
 * conversation; what is pinned here is WHICH conversations get a call (the group box, the filter, the
 * busy ones left out of an archive), that delete asks first and says how many, and that a refusal is
 * counted without stopping the rest.
 */

const SESSIONS = [
  { id: "s1", title: "Corrigir o carrinho", workspace: "C:\\loja", turns: 2, updated_at: 4 },
  { id: "s2", title: "Ler o README", workspace: "C:\\loja", turns: 1, updated_at: 3 },
  { id: "s3", title: "Outro projeto", workspace: "C:\\blog", turns: 1, updated_at: 2 },
  { id: "s4", title: "Rodando agora", workspace: "C:\\blog", turns: 1, updated_at: 1, state: "running" },
];

function render() {
  renderWithProviders(
    <SessionSidebar
      workspace="C:\loja"
      activeSession={null}
      onResume={vi.fn()}
      onNew={vi.fn()}
      onProject={vi.fn()}
    />,
  );
}

async function enterSelection(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Corrigir o carrinho");
  await user.click(screen.getByRole("button", { name: "Select" }));
}

describe("SessionSidebar — selecting several", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue(SESSIONS as Awaited<ReturnType<typeof listCodeSessions>>);
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([]);
    vi.mocked(deleteCodeSession).mockResolvedValue({ ok: true });
    vi.mocked(archiveCodeSession).mockResolvedValue({ id: "x", archived_at: 1 });
    vi.mocked(unarchiveCodeSession).mockResolvedValue({ id: "x", archived_at: null });
  });

  it("has no boxes until selection mode is on", async () => {
    const user = userEvent.setup();
    render();
    await screen.findByText("Corrigir o carrinho");
    expect(screen.queryByRole("checkbox")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Select" }));
    expect(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" })).toBeInTheDocument();
  });

  it("a group box ticks its conversations, and deleting them asks first and says how many", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);

    await user.click(screen.getByRole("checkbox", { name: "Select all conversations in loja" }));
    expect(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select Ler o README" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select Outro projeto" })).not.toBeChecked();
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Delete" }));
    // Nothing yet: the click opens a question that names the count.
    expect(deleteCodeSession).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Delete 2 conversations?" })).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteCodeSession).toHaveBeenCalledTimes(2));
    expect(deleteCodeSession).toHaveBeenCalledWith("s1");
    expect(deleteCodeSession).toHaveBeenCalledWith("s2");
  });

  it("cancelling the confirmation deletes nothing", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select all" }));
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Delete" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    expect(deleteCodeSession).not.toHaveBeenCalled();
  });

  it("the header box shows some, and select all follows the search", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);

    await user.click(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" }));
    expect(screen.getByRole("checkbox", { name: "Select all conversations in loja" })).toBePartiallyChecked();
    expect(screen.getByRole("checkbox", { name: "Select all" })).toBePartiallyChecked();

    // Only what the search leaves on screen is "all".
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    await user.type(screen.getByRole("searchbox"), "README");
    await user.click(screen.getByRole("checkbox", { name: "Select all" }));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(archiveCodeSession).toHaveBeenCalledWith("s2"));
    expect(archiveCodeSession).toHaveBeenCalledTimes(1);
  });

  it("archives the ticked ones but leaves a working conversation alone, and says so", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select all conversations in blog" }));
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Archive" }));

    await waitFor(() => expect(archiveCodeSession).toHaveBeenCalledWith("s3"));
    expect(archiveCodeSession).not.toHaveBeenCalledWith("s4");
    expect(await screen.findByText(/1 still working or waiting for you/)).toBeInTheDocument();
  });

  it("warns in the delete dialog about a working conversation", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select all" }));
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Delete" }));

    expect(screen.getByText("1 of them are working or waiting for you right now.")).toBeInTheDocument();
  });

  it("counts a refusal and still handles the rest", async () => {
    vi.mocked(archiveCodeSession).mockImplementation(async (id) => {
      if (id === "s1") throw new Error("409");
      return { id, archived_at: 1 };
    });
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select all conversations in loja" }));
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Archive" }));

    expect(await screen.findByText(/1 of 2 could not be changed/)).toBeInTheDocument();
    expect(archiveCodeSession).toHaveBeenCalledWith("s2");
  });

  it("removes the ticked folders from the lists", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select all conversations in loja" }));
    await user.click(
      within(screen.getByRole("toolbar")).getByRole("button", { name: "Remove folders from the lists" }),
    );

    await waitFor(() => expect(flagCodeProject).toHaveBeenCalledWith("C:\\loja", { hidden: true }));
    expect(flagCodeProject).not.toHaveBeenCalledWith("C:\\blog", expect.anything());
  });

  it("brings archived conversations back in bulk", async () => {
    vi.mocked(listArchivedCodeSessions).mockResolvedValue([
      { id: "a1", title: "Velha um", workspace: "C:\\loja", turns: 1, updated_at: 1 },
      { id: "a2", title: "Velha dois", workspace: "C:\\loja", turns: 1, updated_at: 1 },
    ] as Awaited<ReturnType<typeof listArchivedCodeSessions>>);
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("button", { name: /Archived|Arquivadas/ }));
    await screen.findByText("Velha um");
    await user.click(screen.getByRole("checkbox", { name: "Select Velha um" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Velha dois" }));
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Unarchive" }));

    await waitFor(() => expect(unarchiveCodeSession).toHaveBeenCalledTimes(2));
    expect(unarchiveCodeSession).toHaveBeenCalledWith("a1");
  });

  it("Escape leaves selection mode and drops the selection", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" }));

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("checkbox")).toBeNull();
    // Back in, nothing is still ticked.
    await user.click(screen.getByRole("button", { name: "Select" }));
    expect(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" })).not.toBeChecked();
  });
  it("draws every row in selection mode, so the header box and delete count only what is seen", async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      id: `m${i}`,
      title: `Muitas ${i}`,
      workspace: "C:\\muitas",
      turns: 1,
      updated_at: 100 - i,
    }));
    vi.mocked(listCodeSessions).mockResolvedValue(many as Awaited<ReturnType<typeof listCodeSessions>>);
    const user = userEvent.setup();
    render();
    await screen.findByText("Muitas 0");
    // Out of selection mode only the first five are drawn.
    expect(screen.queryByText("Muitas 7")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Select" }));
    expect(screen.getByText("Muitas 7")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Select all conversations in muitas" }));
    expect(screen.getAllByRole("checkbox", { checked: true }).length).toBeGreaterThanOrEqual(8);
    await user.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Delete" }));

    expect(screen.getByRole("heading", { name: "Delete 8 conversations?" })).toBeInTheDocument();
  });

  it("Escape inside the add-folder field does not leave selection mode", async () => {
    const user = userEvent.setup();
    render();
    await enterSelection(user);
    await user.click(screen.getByRole("button", { name: "Add a project" }));
    await user.keyboard("{Escape}");

    expect(screen.getByRole("checkbox", { name: "Select Corrigir o carrinho" })).toBeInTheDocument();
  });
});
