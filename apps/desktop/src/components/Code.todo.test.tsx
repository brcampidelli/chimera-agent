import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The agent's own task list, streamed as it records one.
 *
 * The assertion that matters here is not that the list renders — it is that the screen says whose
 * claim it is. Everything else structured on this surface reports something that was checked: the
 * diff is read off disk before and after the write, the verification badge is a command's exit
 * code. A row of ticks looks like a third member of that family and is not one; nothing verified
 * it. So the caption is tested as hard as the content, and the last test here is the one that
 * fails if somebody later "tidies it away" as noise.
 */
async function turnWithTodos(todos: { task: string; status: string }[][]) {
  const user = userEvent.setup();
  vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ todos }));
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Ask about this code/), "do the work{Enter}");
  return user;
}

const THREE = [
  { task: "read the schema", status: "done" },
  { task: "write the catalogue", status: "doing" },
  { task: "check the format", status: "pending" },
];

describe("Code — the task list the agent keeps", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("renders every item with its status", async () => {
    await turnWithTodos([THREE]);

    expect(await screen.findByText("read the schema")).toBeInTheDocument();
    expect(screen.getByText("write the catalogue")).toBeInTheDocument();
    expect(screen.getByText("check the format")).toBeInTheDocument();
    expect(screen.getByText("doing")).toBeInTheDocument();
  });

  it("replaces the list rather than merging two snapshots into one", async () => {
    // Each frame carries the WHOLE list. Appending would show a list that never existed — here,
    // "write the catalogue" twice, once in each status.
    await turnWithTodos([
      THREE,
      [
        { task: "read the schema", status: "done" },
        { task: "write the catalogue", status: "done" },
      ],
    ]);

    expect(await screen.findByText("read the schema")).toBeInTheDocument();
    expect(screen.getAllByText("write the catalogue")).toHaveLength(1);
    expect(screen.queryByText("check the format")).not.toBeInTheDocument();
  });

  it("shows nothing at all when the agent recorded no list", async () => {
    await turnWithTodos([]);

    expect(await screen.findByText("done")).toBeInTheDocument(); // the turn finished
    expect(screen.queryByLabelText("Task list")).not.toBeInTheDocument();
  });

  it("says the progress is the agent's own account and not a verdict", async () => {
    await turnWithTodos([THREE]);

    // The whole point of the component. If this assertion is ever deleted, the screen has started
    // reporting a model's claim in the same voice it uses for a verifier's exit code.
    expect(await screen.findByText(/not a verdict/i)).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 done/i)).toBeInTheDocument();
  });
});
