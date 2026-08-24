import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { deleteCodeProject, deleteCodeSession, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  deleteCodeProject: vi.fn(),
  deleteCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
  listCodeSessions: vi.fn(),
}));

/**
 * Removing things from the sidebar, which until now it could not do.
 *
 * A conversation could be deleted only by being the one you had OPEN — "Clear" calls the delete
 * route, and every other row in the list was permanent. A project could only be RENAMED, so a
 * folder you were finished with stayed on screen for good.
 *
 * The dangerous half is the project, and not for a technical reason: "delete the project" has an
 * obvious wrong reading — delete the folder — and a user who assumes it is about to lose their
 * source code. So the dialog says the folder is untouched, and a test pins that sentence in place.
 */

const SESSIONS = [
  { id: "s1", title: "Corrigir o carrinho", workspace: "C:\\loja", turns: 2, updated_at: 3 },
  { id: "s2", title: "Ler o README", workspace: "C:\\loja", turns: 1, updated_at: 2 },
  { id: "s3", title: "Outro projeto", workspace: "C:\\blog", turns: 1, updated_at: 1 },
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

describe("SessionSidebar — deleting", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCodeSessions).mockResolvedValue(
      SESSIONS as Awaited<ReturnType<typeof listCodeSessions>>,
    );
    vi.mocked(deleteCodeSession).mockResolvedValue({ ok: true });
    vi.mocked(deleteCodeProject).mockResolvedValue({ deleted: 2 });
  });

  it("deletes one conversation, by name, after asking", async () => {
    const user = userEvent.setup();
    render();

    // The row's own delete control, named for the row — a bare icon on every line is how somebody
    // removes the one above the one they meant. Two projects are on screen and each has one, so an
    // unnamed match would be ambiguous, which is exactly the property being asserted.
    await user.click(
      await screen.findByRole("button", { name: /(Apagar|Delete) Corrigir o carrinho/i }),
    );

    // Nothing has happened yet: the click opens a question.
    expect(deleteCodeSession).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /^Apagar$|^Delete$/i }));

    await waitFor(() => expect(deleteCodeSession).toHaveBeenCalledWith("s1"));
  });

  it("cancels without deleting", async () => {
    // The control that makes the confirmation a confirmation. Without it the dialog is a delay.
    const user = userEvent.setup();
    render();

    await user.click(
      await screen.findByRole("button", { name: /Apagar Corrigir o carrinho|Delete Corrigir o carrinho/i }),
    );
    await user.click(screen.getByRole("button", { name: /^Cancelar$|^Cancel$/i }));

    expect(deleteCodeSession).not.toHaveBeenCalled();
  });

  it("says the folder is not touched before deleting a project", async () => {
    const user = userEvent.setup();
    render();

    await user.click(
      await screen.findByRole("button", { name: /(Apagar o projeto|Delete the project) loja/i }),
    );

    expect(
      screen.getByText(/pasta no disco não é tocada|folder on disk is not touched/i),
      "the one sentence that stops this reading as 'delete my source code'",
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Apagar$|^Delete$/i }));

    await waitFor(() => expect(deleteCodeProject).toHaveBeenCalledWith("C:\\loja"));
    expect(deleteCodeSession).not.toHaveBeenCalled();
  });

  it("counts what is about to go", async () => {
    // Two conversations under C:\loja, one under C:\blog. A confirmation that cannot say how much
    // is at stake is asking the user to guess.
    const user = userEvent.setup();
    render();

    await user.click(
      await screen.findByRole("button", { name: /(Apagar o projeto|Delete the project) loja/i }),
    );

    // In the dialog's own heading, not merely somewhere on the page — the sidebar behind it is
    // full of text, and "a 2 exists" would pass with the count missing from the question.
    expect(
      screen.getByRole("heading", { name: /2/ }),
      "the confirmation does not say how many conversations are at stake",
    ).toBeInTheDocument();
  });
});
