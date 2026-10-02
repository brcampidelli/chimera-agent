import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { useRunSession, type RunSession } from "@/lib/run-session";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/lib/run-session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/run-session")>("@/lib/run-session");
  return { ...actual, useRunSession: vi.fn(actual.useRunSession) };
});

/**
 * "Let the agent try to fix it" and the runs already working.
 *
 * Found reading the code on 2026-09-30 (R5 of the review of several conversations at once): the app
 * held one run at a time, and this button handed the fix to a session that refused it without a
 * word — clicking it did nothing while a run worked in ANOTHER project. The first fix disabled the
 * button and said why.
 *
 * Then the owner chose runs in parallel, one per project (same day). A run in another project no
 * longer stands in the way, so the fix starts; only a run in THIS project blocks it, and the button
 * still says so. The first version of this file asserted the opposite for another project's run —
 * it was true of the rule at the time, and the rule changed.
 */
const start = vi.fn(() => true);
const IDLE: RunSession = {
  running: false, task: "", runId: null, events: [], done: null, stopping: false, broken: false,
  workspace: null, paused: null, verify: null, alsoRunning: 0, start, stop: () => {}, clearPaused: () => {},
};

describe("Code — the fix button and the runs already working", () => {
  beforeEach(() => {
    start.mockClear();
    vi.mocked(useRunSession).mockReturnValue(IDLE);
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        verified: { command: "npm test", source: "inferred:package.json", state: "failed", output: "1 failing" },
      }),
    );
  });

  async function failedTurn() {
    const user = userEvent.setup({ delay: null });
    const view = renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "rename it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/1 failing/);
    return { user, view };
  }

  it("is disabled, and says why, while a run works in this project", async () => {
    const { view } = await failedTurn();
    // The run arrives after the turn: while it works here, the composer is blocked too, so the
    // failed verdict has to be on screen first.
    vi.mocked(useRunSession).mockReturnValue({ ...IDLE, running: true, workspace: "" });
    view.rerender(<Code />);

    expect(screen.getByRole("button", { name: /Let the agent try to fix it/i })).toBeDisabled();
    expect(screen.getByText(/A run is already working in this project/i)).toBeInTheDocument();
  });

  it("hands the fix to a run while another project has one working", async () => {
    vi.mocked(useRunSession).mockReturnValue({ ...IDLE, running: true, workspace: "/another-project" });
    const { user } = await failedTurn();

    await user.click(screen.getByRole("button", { name: /Let the agent try to fix it/i }));

    expect(start).toHaveBeenCalledOnce();
    expect(screen.queryByText(/A run is already working/i)).not.toBeInTheDocument();
  });

  it("hands the fix to a run when none is working", async () => {
    const { user } = await failedTurn();

    await user.click(screen.getByRole("button", { name: /Let the agent try to fix it/i }));

    expect(start).toHaveBeenCalledOnce();
  });
});
