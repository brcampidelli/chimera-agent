import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPlan, getRuns, streamRun } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const PLAN = "1. Read the failing test\n2. Fix the parser\n3. Re-run pytest";

async function typeTask(task = "make the test pass") {
  const user = userEvent.setup();
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Describe the change/), task);
  return user;
}

/** Preview is the consent step before any file is touched: it must call ONLY the planner, show the
 *  real steps it got back, and hand exactly those steps to the run the user approves. */
describe("Code — plan preview", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
    vi.mocked(getPlan).mockResolvedValue({ text: PLAN, steps: [], note: "" });
  });

  it("is not offered until a task has been described", async () => {
    renderWithProviders(<Code />);

    expect(screen.getByRole("button", { name: /Preview plan/ })).toBeDisabled();
  });

  it("calls the planner with the task and renders the returned steps as a list", async () => {
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));

    expect(getPlan).toHaveBeenCalledWith(null, "make the test pass");
    expect(await screen.findByText("Read the failing test")).toBeInTheDocument();
    expect(screen.getByText("Fix the parser")).toBeInTheDocument();
    expect(screen.getByText("Re-run pytest")).toBeInTheDocument();
    // Previewing must not have started anything.
    expect(streamRun).not.toHaveBeenCalled();
  });

  it("states that a preview makes no edits", async () => {
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));

    expect(
      await screen.findByText(
        "Preview only — this makes no edits. Approve or edit the plan before any file changes.",
      ),
    ).toBeInTheDocument();
  });

  it("passes the approved plan into the run request", async () => {
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));
    await user.click(await screen.findByRole("button", { name: /Run with this plan/ }));

    expect(streamRun).toHaveBeenCalledOnce();
    expect(vi.mocked(streamRun).mock.calls[0][0]).toMatchObject({
      task: "make the test pass",
      plan: PLAN,
    });
  });

  it("passes the user's edits to the plan, not the planner's original text", async () => {
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));
    const editor = await screen.findByPlaceholderText(/edit the plan before running/);
    await user.clear(editor);
    await user.type(editor, "1. Do it my way");
    await user.click(screen.getByRole("button", { name: /Run with this plan/ }));

    expect(vi.mocked(streamRun).mock.calls[0][0].plan).toBe("1. Do it my way");
  });

  it("sends no plan on the plain Run path, so the run plans for itself", async () => {
    const user = await typeTask();

    await user.click(screen.getAllByRole("button", { name: "Run" })[0]);

    expect(streamRun).toHaveBeenCalledOnce();
    expect(vi.mocked(streamRun).mock.calls[0][0].plan).toBeNull();
    // No plan was injected, so no step list is invented for the run.
    expect(screen.queryByText("Plan for this run")).not.toBeInTheDocument();
  });

  it("shows the approved plan as the run's step list once it starts", async () => {
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));
    await user.click(await screen.findByRole("button", { name: /Run with this plan/ }));

    expect(await screen.findByText("Plan for this run")).toBeInTheDocument();
    expect(screen.getByText("Fix the parser")).toBeInTheDocument();
  });

  it("shows the planner's honest degrade note instead of inventing steps", async () => {
    vi.mocked(getPlan).mockResolvedValue({ text: "", steps: [], note: "planner unavailable" });
    const user = await typeTask();

    await user.click(screen.getByRole("button", { name: /Preview plan/ }));

    expect(await screen.findByText("The planner returned no steps.")).toBeInTheDocument();
    expect(screen.getByText("planner unavailable")).toBeInTheDocument();
    // With no plan text there is nothing to approve.
    expect(screen.getByRole("button", { name: /Run with this plan/ })).toBeDisabled();
  });
});
