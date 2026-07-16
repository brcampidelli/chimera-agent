import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getRuns, streamRun, type RunStreamHandlers } from "@/lib/api";
import { attempt, emptyTree, gitStatus, receipt } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import type { RunReceipt } from "@/lib/types";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** Type a task, click Run, and let the mocked stream finish — then the panel fetches the newest
 *  receipt (the authoritative record of what the run actually did) and renders it. */
async function runAndLand(landed: RunReceipt) {
  const user = userEvent.setup();
  vi.mocked(getRuns).mockResolvedValue([landed]);
  vi.mocked(streamRun).mockImplementation(async (_req, handlers: RunStreamHandlers) => {
    handlers.onDone?.({ success: landed.success, answer: landed.answer, attempts: landed.attempts.length });
  });
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText(/^Describe the change/), "make the test pass");
  await user.click(screen.getAllByRole("button", { name: "Run" })[0]);
  return user;
}

describe("Code — the run receipt", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
  });

  it("renders the verifier's real captured output when the attempt produced some", async () => {
    await runAndLand(
      receipt({ attempts: [attempt({ verify_output: "1 passed in 0.42s" })] }),
    );

    expect(await screen.findByText("Verify output")).toBeInTheDocument();
    expect(screen.getByText("1 passed in 0.42s")).toBeInTheDocument();
  });

  it("hides the verify-output panel entirely when the verifier produced none", async () => {
    await runAndLand(receipt({ attempts: [attempt({ verify_output: "" })] }));

    // The attempt still renders (it has a diff) — but nothing fabricates a verify panel.
    expect(await screen.findByText("src/app.py")).toBeInTheDocument();
    expect(screen.queryByText("Verify output")).not.toBeInTheDocument();
  });

  it("labels a reverted attempt as attempted-and-undone, never as applied", async () => {
    await runAndLand(
      receipt({
        success: false,
        attempts: [attempt({ index: 1, success: false, verified: false, reverted: true })],
      }),
    );

    expect(await screen.findByText("↩ reverted")).toBeInTheDocument();
    expect(
      screen.getByText("reverted — these changes were undone after verification failed"),
    ).toBeInTheDocument();
    expect(screen.getByText("done: failed")).toBeInTheDocument();
    expect(screen.queryByText("done: passed")).not.toBeInTheDocument();
  });

  it("does not label a successful attempt as reverted", async () => {
    await runAndLand(receipt({ attempts: [attempt({ reverted: false })] }));

    expect(await screen.findByText("done: passed")).toBeInTheDocument();
    expect(screen.queryByText("↩ reverted")).not.toBeInTheDocument();
    expect(screen.queryByText(/undone after verification failed/)).not.toBeInTheDocument();
  });

  it("says the run changed nothing rather than showing an empty diff area", async () => {
    await runAndLand(receipt({ attempts: [attempt({ diffs: [], verify_output: "" })] }));

    expect(await screen.findByText("This run changed nothing on disk.")).toBeInTheDocument();
  });

  it("disables Discard and explains why when the workspace is not a git repo", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    await runAndLand(receipt());

    const discard = await screen.findByRole("button", { name: /Discard changes/ });
    expect(discard).toBeDisabled();
    expect(
      screen.getByText("Discard needs a git repo — edit manually or run `git init`."),
    ).toBeInTheDocument();
    // The git panel's own empty state says the same thing honestly, and offers no commit UI.
    expect(
      screen.getByText("Not a git repo — run `git init` in this folder to enable the panel."),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/commit message/)).not.toBeInTheDocument();
  });

  it("enables Discard, scoped to the run's own changed paths, inside a git repo", async () => {
    await runAndLand(receipt());

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Discard changes/ })).toBeEnabled(),
    );
  });

  it("reports an honest 'cancelled' end state rather than a plain failure", async () => {
    const user = userEvent.setup();
    vi.mocked(getRuns).mockResolvedValue([receipt()]);
    vi.mocked(streamRun).mockImplementation(async (_req, handlers: RunStreamHandlers) => {
      handlers.onDone?.({ success: false, answer: "", attempts: 1, stopped_reason: "cancelled" });
    });
    renderWithProviders(<Code />);

    await user.type(screen.getByPlaceholderText(/^Describe the change/), "make the test pass");
    await user.click(screen.getAllByRole("button", { name: "Run" })[0]);

    expect(await screen.findByText("stopped — cancelled before the next attempt")).toBeInTheDocument();
    expect(screen.queryByText("done: failed")).not.toBeInTheDocument();
  });
});
