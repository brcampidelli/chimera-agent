import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  cancelRun,
  getFsTree,
  getGitStatus,
  getRuns,
  streamRun,
  type RunStreamHandlers,
} from "@/lib/api";
import { emptyTree, gitStatus, receipt } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** Start a run whose stream stays open: it announces its run id and then hangs, exactly like a real
 *  run mid-attempt. Returns the captured handlers so a test can end the run when it wants. */
async function startHangingRun(runId: string | null = "run_42") {
  const user = userEvent.setup();
  let captured!: RunStreamHandlers;
  vi.mocked(streamRun).mockImplementation((_req, handlers: RunStreamHandlers) => {
    captured = handlers;
    if (runId) handlers.onRunId?.(runId);
    return new Promise<void>(() => {}); // never settles: the run is in flight
  });
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText(/^Describe the change/), "make the test pass");
  await user.click(screen.getAllByRole("button", { name: "Run" })[0]);
  return { user, handlers: () => captured };
}

describe("Code — stopping a run", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([receipt()]);
    vi.mocked(cancelRun).mockResolvedValue({ ok: true });
  });

  it("offers no Stop button until a run is in flight", () => {
    renderWithProviders(<Code />);

    expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
  });

  it("shows Stop while the run streams", async () => {
    await startHangingRun();

    expect(await screen.findByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("cancels the in-flight run by its id and says it stops after this attempt", async () => {
    const { user } = await startHangingRun("run_42");

    await user.click(await screen.findByRole("button", { name: "Stop" }));

    expect(cancelRun).toHaveBeenCalledWith("run_42");
    expect(await screen.findByText(/Stopping after this attempt/)).toBeInTheDocument();
  });

  it("does not offer to cancel before the run has reported an id", async () => {
    await startHangingRun(null);

    expect(await screen.findByRole("button", { name: "Stop" })).toBeDisabled();
    expect(cancelRun).not.toHaveBeenCalled();
  });

  it("clears the stopping state once the run actually ends", async () => {
    const { user, handlers } = await startHangingRun("run_42");

    await user.click(await screen.findByRole("button", { name: "Stop" }));
    await screen.findByText(/Stopping after this attempt/);
    // The backend's terminal frame lands (the run halted before its next attempt).
    await act(async () => {
      handlers().onDone?.({ success: false, answer: "", attempts: 1, stopped_reason: "cancelled" });
    });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByText("stopped — cancelled before the next attempt")).toBeInTheDocument();
  });
});
