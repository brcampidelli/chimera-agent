import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  revertCodeTurn,
  streamCodeTurn,
  type CodeVerified,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The screen used to offer two buttons that were not two ways of doing one thing: Send edited your
 * files and kept whatever it wrote, while "Run with verification" planned, verified, and reverted on
 * failure. Nothing said so, which made pressing Enter silently the weaker choice.
 *
 * There is one button now, and these are the claims that let it be one: the turn is judged, a
 * project that cannot judge says so, and a failure offers an undo instead of taking it.
 */
describe("Code — the verdict on what a turn wrote", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  async function turnWith(verified: CodeVerified | undefined) {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ verified }));
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "rename it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    return user;
  }

  it("offers no second button that would quietly be the stronger one", async () => {
    await turnWith(undefined);
    expect(screen.queryByRole("button", { name: /Run with verification/i })).not.toBeInTheDocument();
  });

  it("says out loud when nothing checked the edits", async () => {
    // The absence a user reads as approval, because the verified path always checked.
    await turnWith({ command: null, source: "none", state: "none" });
    await screen.findByText(/no verification command/i);
  });

  it("names the command and where it came from when it passes", async () => {
    await turnWith({ command: "python -m pytest -q", source: "inferred:tests/", state: "passed" });
    await screen.findByText(/python -m pytest -q.*inferred:tests\/.*passed/i);
    expect(screen.queryByRole("button", { name: /Undo these edits/i })).not.toBeInTheDocument();
  });

  it("offers the undo on failure and only applies it when pressed", async () => {
    vi.mocked(revertCodeTurn).mockResolvedValue({ ok: true, restored: 1 });
    const user = await turnWith({
      command: "python -m pytest -q",
      source: "inferred:tests/",
      state: "failed",
      output: "E   assert False",
      revert_token: "tok",
    });

    await screen.findByText(/assert False/);
    expect(revertCodeTurn).not.toHaveBeenCalled(); // offered, not taken

    await user.click(screen.getByRole("button", { name: /Undo these edits/i }));
    await waitFor(() => expect(revertCodeTurn).toHaveBeenCalledWith("tok"));
    await screen.findByText(/Edits undone/i);
  });

  it("reports a refused undo as edits still present, not as success", async () => {
    // The failure mode this guards is the worst kind of lie the screen could tell: saying the files
    // were restored when they were not.
    vi.mocked(revertCodeTurn).mockResolvedValue({ ok: false, restored: 0 });
    const user = await turnWith({
      command: "make test",
      source: "inferred:Makefile",
      state: "failed",
      revert_token: "stale",
    });
    await user.click(await screen.findByRole("button", { name: /Undo these edits/i }));
    await screen.findByText(/that snapshot is gone/i);
  });
});
