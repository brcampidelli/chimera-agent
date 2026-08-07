import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { cancelRun, streamRun, type RunStreamHandlers } from "@/lib/api";
import { RunSessionProvider, useRunSession } from "@/lib/run-session";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/lib/api", () => ({ streamRun: vi.fn(), cancelRun: vi.fn() }));
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
  render(
    <I18nProvider>
      <RunSessionProvider>
        <Launcher />
        <AgentStatusBar />
      </RunSessionProvider>
    </I18nProvider>,
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
    render(
      <I18nProvider>
        <RunSessionProvider>
          <AgentStatusBar />
        </RunSessionProvider>
      </I18nProvider>,
    );

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
});
