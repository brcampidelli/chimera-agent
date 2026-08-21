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

  it("offers the undo on a turn that PASSED, because a pass is not consent", async () => {
    // The token used to be minted only for `state === "failed"`, so three of the four verdicts —
    // passed, abstained, and no command at all — took a snapshot and let it die with the request.
    // Meanwhile the Posture note promised outright: "What is guaranteed is the snapshot and the
    // undo, not the limits."
    //
    // The check answers "does this still build". The button answers "do I want this", and only the
    // person reading the diff can.
    vi.mocked(revertCodeTurn).mockResolvedValue({ ok: true, restored: 1 });
    const user = await turnWith({
      command: "python -m pytest -q",
      source: "inferred:tests/",
      state: "passed",
      revert_token: "tok",
    });

    await user.click(await screen.findByRole("button", { name: /Undo these edits/i }));
    await waitFor(() => expect(revertCodeTurn).toHaveBeenCalledWith("tok"));
  });

  it("offers the undo when there was no command to check with at all", async () => {
    // The case where it matters most: a project with no test command never reaches the failed
    // branch, so under the old rule the snapshot was taken and dropped on EVERY editing turn.
    vi.mocked(revertCodeTurn).mockResolvedValue({ ok: true, restored: 2 });
    const user = await turnWith({
      command: null,
      source: "none",
      state: "none",
      revert_token: "tok",
    });

    await screen.findByText(/no verification command/i);
    await user.click(screen.getByRole("button", { name: /Undo these edits/i }));
    await waitFor(() => expect(revertCodeTurn).toHaveBeenCalledWith("tok"));
  });

  it("offers no undo on a turn that wrote nothing", async () => {
    // Or the two tests above would pass against a version that showed the button unconditionally,
    // inviting someone to roll back a snapshot of work they did by hand.
    await turnWith({ command: "python -m pytest -q", source: "inferred:tests/", state: "passed" });

    await screen.findByText(/passed/i);
    expect(screen.queryByRole("button", { name: /Undo these edits/i })).not.toBeInTheDocument();
  });

  it("does not call new files removed when they are still there", async () => {
    // Inside a git repository the delete-new pass is skipped unconditionally — deliberately, after
    // a path bug once let a revert wipe a repo — so a turn that CREATED a file leaves it behind.
    // "Edits undone." over that describes a state the workspace is not in, and it is the one
    // sentence a reader would act on without checking.
    vi.mocked(revertCodeTurn).mockResolvedValue({
      ok: true,
      restored: 1,
      left_new_files: true,
    });
    const user = await turnWith({
      command: "python -m pytest -q",
      source: "inferred:tests/",
      state: "failed",
      revert_token: "tok",
    });

    await user.click(await screen.findByRole("button", { name: /Undo these edits/i }));
    await screen.findByText(/files this turn created are still there/i);
  });

  it("still says plainly undone when it really was", async () => {
    // Or the test above would pass against a version that had started hedging on every undo.
    vi.mocked(revertCodeTurn).mockResolvedValue({
      ok: true,
      restored: 1,
      left_new_files: false,
    });
    const user = await turnWith({
      command: "python -m pytest -q",
      source: "inferred:tests/",
      state: "failed",
      revert_token: "tok",
    });

    await user.click(await screen.findByRole("button", { name: /Undo these edits/i }));
    await screen.findByText(/^Edits undone\.$/i);
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
