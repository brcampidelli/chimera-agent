import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  gitInit,
  streamAgents,
  streamCodeTurn,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const TWO_JOBS = "1. add a test for the parser\n2. fix the lint errors";

/**
 * Running several agents at once used to be a destination: a tab you chose before knowing whether
 * the work was parallel, with eight task boxes, a worker count, a model field and three fusion
 * modes. Asking for several things IS the request now.
 *
 * The claim that mattered most is the last one here. The old screen reported "this batch ran WITHOUT
 * isolation" in its results banner — after N agents had already edited the same directory, which is
 * the moment nothing can be done about it.
 */
describe("Code — several jobs become a batch, once", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(streamAgents).mockImplementation(async () => {});
  });

  async function type(message: string) {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), message);
    await user.click(screen.getByRole("button", { name: "Send" }));
    return user;
  }

  it("proposes the split instead of creating worktrees behind your back", async () => {
    await type(TWO_JOBS);

    await screen.findByText(/2 separate jobs/i);
    expect(screen.getByText("add a test for the parser")).toBeInTheDocument();
    expect(streamAgents).not.toHaveBeenCalled();
    expect(streamCodeTurn).not.toHaveBeenCalled();
  });

  it("starts the batch on one confirmation, with the tasks as proposed", async () => {
    const user = await type(TWO_JOBS);
    await user.click(await screen.findByRole("button", { name: /Run 2 in parallel/i }));

    await waitFor(() => expect(streamAgents).toHaveBeenCalledOnce());
    expect(vi.mocked(streamAgents).mock.calls[0][0].tasks).toEqual([
      { task: "add a test for the parser", verify: null },
      { task: "fix the lint errors", verify: null },
    ]);
  });

  it("sends it as one message when the split is declined, and does not ask twice", async () => {
    const user = await type(TWO_JOBS);
    await user.click(await screen.findByRole("button", { name: /one message/i }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    expect(streamAgents).not.toHaveBeenCalled();
    expect(screen.queryByText(/2 separate jobs/i)).not.toBeInTheDocument();
  });

  it("leaves an ordinary message alone", async () => {
    // The false positive that would matter: interrupting a normal request with a card about git
    // worktrees is worse than missing a split the user can make explicit with a numbered list.
    await type("rename the module, update the imports and fix the docs");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    expect(screen.queryByText(/separate jobs/i)).not.toBeInTheDocument();
  });

  it("warns that isolation is not real BEFORE the worktrees would exist", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false }));
    await type(TWO_JOBS);

    await screen.findByText(/not a git repository/i);
    expect(streamAgents).not.toHaveBeenCalled(); // still only a proposal
  });

  it("offers the repair beside the warning, while it can still be taken", async () => {
    // Naming a problem the user cannot act on from where they are reading about it leaves them the
    // choice between running unprotected and going to find a terminal. And the moment matters: a
    // snapshot commit is only a point of return if it is taken before the agents start writing.
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false }));
    vi.mocked(gitInit).mockResolvedValue({ ok: true, commit: "abc1234", output: "", error: null });
    const user = await type(TWO_JOBS);

    await user.click(await screen.findByRole("button", { name: /Initialise git here/ }));

    expect(gitInit).toHaveBeenCalledOnce();
    expect(streamAgents).not.toHaveBeenCalled(); // the repair is not a confirmation
  });
});
