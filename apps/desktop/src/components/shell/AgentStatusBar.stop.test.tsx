import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { cancelRun, streamRun, type RunStreamHandlers } from "@/lib/api";
import { useRunSession } from "@/lib/run-session";
import { renderWithProviders } from "@/test/utils";

// `getApprovals` joined the list because the bar now carries the pending-question chip. An empty
// answer is what "no question is parked" looks like, which is the state every test here assumes.
vi.mock("@/lib/api", () => ({
  streamRun: vi.fn(),
  cancelRun: vi.fn(),
  getApprovals: vi.fn(async () => []),
  // The bar lists the coding turns running in other conversations: none here.
  listRunningTurns: vi.fn(async () => []),
  stopCodeTurn: vi.fn(),
}));
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

/**
 * Stopping a run, asserted against the bar that stops it from anywhere.
 *
 * These five claims were written against a launcher folded under the Code screen — a second
 * implementation of the Work screen's launcher, with fewer features, which is gone. Nothing about
 * what they protect went with it: a Stop that appears only while something is running, that cannot
 * be pressed before the backend has given us a handle to cancel, that says the run halts AFTER the
 * current attempt rather than instantly, and that clears itself when the run really ends.
 *
 * They belong here for a better reason than convenience: this bar sits outside the view switch, so
 * these are now true from every screen instead of one.
 */
function Launcher() {
  const run = useRunSession();
  return (
    <button onClick={() => run.start({ task: "make the test pass", max_attempts: 3 })}>go</button>
  );
}

async function startHangingRun(runId: string | null = "run_42") {
  const user = userEvent.setup();
  let captured!: RunStreamHandlers;
  vi.mocked(streamRun).mockImplementation((_req, handlers: RunStreamHandlers) => {
    captured = handlers;
    if (runId) handlers.onRunId?.(runId);
    return new Promise<void>(() => {}); // never settles: the run is in flight
  });
  renderWithProviders(
    <>
      <Launcher />
      <AgentStatusBar />
    </>,
  );
  await user.click(screen.getByText("go"));
  return { user, handlers: () => captured };
}

describe("AgentStatusBar — stopping a run", () => {
  beforeEach(() => {
    vi.mocked(cancelRun).mockReset();
    vi.mocked(cancelRun).mockResolvedValue({ ok: true });
  });

  it("offers no Stop until a run is in flight", () => {
    renderWithProviders(<AgentStatusBar />);

    expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
  });

  it("shows Stop while the run streams", async () => {
    await startHangingRun();

    expect(await screen.findByRole("button", { name: /Stop/ })).toBeEnabled();
  });

  it("cancels the in-flight run by its id", async () => {
    const { user } = await startHangingRun("run_42");

    await user.click(await screen.findByRole("button", { name: /Stop/ }));

    expect(cancelRun).toHaveBeenCalledWith("run_42");
  });

  it("does not offer to cancel before the run has reported an id", async () => {
    // Cancelling needs a handle. A button that is pressable before we have one either does nothing
    // or throws, and both read to the user as "Stop is broken".
    await startHangingRun(null);

    expect(await screen.findByRole("button", { name: /Stop/ })).toBeDisabled();
    expect(cancelRun).not.toHaveBeenCalled();
  });

  it("clears the stopping state once the run actually ends", async () => {
    const { user, handlers } = await startHangingRun("run_42");

    await user.click(await screen.findByRole("button", { name: /Stop/ }));
    await act(async () => {
      handlers().onDone?.({ success: false, answer: "", attempts: 1, stopped_reason: "cancelled" });
    });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument(),
    );
  });

  it("names the runs working in other projects", async () => {
    // Runs work in several projects at once now (2026-09-30). The bar names the latest and says how
    // many others there are, so a run started from another screen is never out of sight.
    vi.mocked(streamRun).mockImplementation(() => new Promise<void>(() => {}));
    const user = userEvent.setup();
    function In({ ws }: { ws: string }) {
      const run = useRunSession(ws);
      return <button onClick={() => run.start({ task: `task ${ws}`, workspace: ws, max_attempts: 3 })}>go {ws}</button>;
    }
    renderWithProviders(
      <>
        <In ws="/a" />
        <In ws="/b" />
        <AgentStatusBar />
      </>,
    );

    await user.click(screen.getByText("go /a"));
    await user.click(screen.getByText("go /b"));

    expect(await screen.findByText("task /b")).toBeInTheDocument();
    expect(screen.getByText("+1 more running")).toBeInTheDocument();
  });
});
