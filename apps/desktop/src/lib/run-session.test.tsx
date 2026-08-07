import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { cancelRun, streamRun, type RunStreamHandlers } from "@/lib/api";
import { RunSessionProvider, useRunSession } from "@/lib/run-session";

vi.mock("@/lib/api", () => ({
  streamRun: vi.fn(),
  cancelRun: vi.fn(),
}));

/** Hold the live stream's handlers so a test can deliver frames when it wants to. */
let live: RunStreamHandlers = {};

beforeEach(() => {
  live = {};
  vi.mocked(streamRun).mockImplementation(async (_req, handlers) => {
    live = handlers;
  });
  vi.mocked(cancelRun).mockResolvedValue({ ok: true });
});

/** A screen that starts runs — stands in for Work. */
function Launcher() {
  const run = useRunSession();
  return (
    <button onClick={() => run.start({ task: "refactor the parser", max_attempts: 3 })}>
      start
    </button>
  );
}

/** A screen that only observes — stands in for the status bar, or for any other view. */
function Observer() {
  const { running, task, events, stopping, verify } = useRunSession();
  return (
    <div>
      <span data-testid="state">{running ? `running:${task}` : "idle"}</span>
      <span data-testid="events">{events.length}</span>
      <span data-testid="stopping">{String(stopping)}</span>
      <span data-testid="verify">{verify ? `${verify.command ?? "none"}:${verify.source}` : "-"}</span>
    </div>
  );
}

/** The shell: the observer is always mounted; the launcher comes and goes with navigation. */
function Shell() {
  const [onLauncher, setOnLauncher] = useState(true);
  return (
    <RunSessionProvider>
      <button onClick={() => setOnLauncher((v) => !v)}>navigate</button>
      <Observer />
      {onLauncher && <Launcher />}
    </RunSessionProvider>
  );
}

describe("the run session", () => {
  it("keeps the run alive after the screen that started it is gone", async () => {
    const user = userEvent.setup();
    render(<Shell />);

    await user.click(screen.getByText("start"));
    live.onRunId?.("run_7");
    live.onEvent?.({ kind: "attempt", index: 1, text: "" } as never);
    await waitFor(() => expect(screen.getByTestId("events")).toHaveTextContent("1"));

    // Leave the screen. This is the whole point: before, the launcher owned the run in its own
    // state, so unmounting it lost the progress and stranded a running agent with no Stop.
    await user.click(screen.getByText("navigate"));
    expect(screen.queryByText("start")).toBeNull();

    expect(screen.getByTestId("state")).toHaveTextContent("running:refactor the parser");
    live.onEvent?.({ kind: "attempt", index: 2, text: "" } as never);
    await waitFor(() => expect(screen.getByTestId("events")).toHaveTextContent("2"));
  });

  it("stops the run from anywhere, and stays stopping until the backend says otherwise", async () => {
    const user = userEvent.setup();
    function StopButton() {
      const run = useRunSession();
      return <button onClick={run.stop}>stop</button>;
    }
    render(
      <RunSessionProvider>
        <Observer />
        <Launcher />
        <StopButton />
      </RunSessionProvider>,
    );

    await user.click(screen.getByText("start"));
    live.onRunId?.("run_7");
    await user.click(screen.getByText("stop"));

    expect(cancelRun).toHaveBeenCalledWith("run_7");
    // Cancel is cooperative — the loop halts before its NEXT attempt. Flipping to idle on the
    // request would claim a stop that has not happened yet.
    await waitFor(() => expect(screen.getByTestId("stopping")).toHaveTextContent("true"));
    expect(screen.getByTestId("state")).toHaveTextContent("running:");

    live.onDone?.({ success: false, answer: "", attempts: 1, stopped_reason: "cancelled" } as never);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("idle"));
  });

  it("refuses to cancel before the backend has named the run", async () => {
    const user = userEvent.setup();
    function StopButton() {
      const run = useRunSession();
      return <button onClick={run.stop}>stop</button>;
    }
    render(
      <RunSessionProvider>
        <Launcher />
        <StopButton />
      </RunSessionProvider>,
    );

    await user.click(screen.getByText("start"));
    // No `run` frame yet: there is no id to address a cancel to, and POSTing to a made-up one
    // would silently no-op while the UI claimed it had stopped.
    await user.click(screen.getByText("stop"));
    expect(cancelRun).not.toHaveBeenCalled();
  });

  it("refuses a second run while one is live", async () => {
    const user = userEvent.setup();
    render(
      <RunSessionProvider>
        <Launcher />
      </RunSessionProvider>,
    );

    await user.click(screen.getByText("start"));
    await user.click(screen.getByText("start"));
    // Not queued behind the first: a queue nobody asked for is a worse surprise than a refusal.
    expect(streamRun).toHaveBeenCalledTimes(1);
  });

  it("does not report a broken stream as a failed run", async () => {
    const user = userEvent.setup();
    function Verdict() {
      const { done, broken, running } = useRunSession();
      return (
        <span data-testid="verdict">
          {running ? "running" : `${broken ? "broken" : "clean"}:${done ? done.success : "none"}`}
        </span>
      );
    }
    render(
      <RunSessionProvider>
        <Launcher />
        <Verdict />
      </RunSessionProvider>,
    );

    await user.click(screen.getByText("start"));
    live.onError?.("network error");

    // The backend run may well still be going. What ended is our view of it, and `done` stays
    // null rather than inventing a verdict we never received.
    await waitFor(() => expect(screen.getByTestId("verdict")).toHaveTextContent("broken:none"));
  });
});

describe("what is about to judge the run", () => {
  it("carries the verdict source, which the client used to drop on the floor", async () => {
    // The server has always sent this frame before the first step (`api/app.py`), and the client had
    // no branch for it. The sentence that mattered most — "nothing executable is judging this" —
    // reached the user only afterwards, in the receipt, when the run was already over.
    const user = userEvent.setup();
    render(
      <RunSessionProvider>
        <Launcher />
        <Observer />
      </RunSessionProvider>,
    );
    await user.click(screen.getByText("start"));

    live.onVerify?.({ command: null, source: "none" });
    await waitFor(() => expect(screen.getByTestId("verify")).toHaveTextContent("none:none"));
  });

  it("clears between runs, so a stale verdict cannot describe the next one", async () => {
    const user = userEvent.setup();
    render(
      <RunSessionProvider>
        <Launcher />
        <Observer />
      </RunSessionProvider>,
    );
    await user.click(screen.getByText("start"));
    live.onVerify?.({ command: "pytest -q", source: "inferred:tests/" });
    await waitFor(() => expect(screen.getByTestId("verify")).toHaveTextContent("pytest -q"));

    await user.click(screen.getByText("start"));
    await waitFor(() => expect(screen.getByTestId("verify")).toHaveTextContent("-"));
  });
});
