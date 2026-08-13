import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const streamExec = vi.fn();
const cancelExec = vi.fn();
vi.mock("@/lib/api", () => ({
  streamExec: (...args: unknown[]) => streamExec(...args),
  cancelExec: (...args: unknown[]) => cancelExec(...args),
}));

const { Runner } = await import("@/components/editor/Runner");
const { I18nProvider } = await import("@/lib/i18n");

/**
 * The runner, and the two things that make it trustworthy rather than convenient.
 *
 * One: Stop must reach the server, not merely stop us listening. A button that aborts the fetch and
 * calls itself done leaves the command running with nothing on screen to say so — which is how a dev
 * server ends up holding a port nobody can account for.
 *
 * Two: it must say it is not a terminal BEFORE someone types `cd ..` and wonders why the next
 * command ignored it.
 */

function mount(workspace: string | null = "/ws") {
  return render(
    <I18nProvider>
      <Runner workspace={workspace} />
    </I18nProvider>,
  );
}

/** Drive the stream the way the server does, one handler at a time. */
function handlersOf(): {
  onStarted?: (id: string) => void;
  onLine?: (text: string) => void;
  onExit?: (code: number) => void;
  onError?: (message: string) => void;
} {
  return streamExec.mock.calls[streamExec.mock.calls.length - 1][1];
}

beforeEach(() => {
  streamExec.mockReset();
  // Set in `beforeEach`, not at the top: this project's vitest config resets mock implementations
  // between tests, and a `cancelExec` that returns undefined makes `.catch` throw inside a click
  // handler — which surfaces as an unhandled rejection blamed on whichever test ran last.
  cancelExec.mockReset();
  cancelExec.mockResolvedValue({ cancelled: true });
  localStorage.clear();
});

describe("Runner", () => {
  it("says it is not a terminal before anyone finds out the hard way", () => {
    mount();

    expect(screen.getByText(/not a terminal/i)).toBeTruthy();
  });

  it("shows the command, its output and its exit code as one transcript", async () => {
    streamExec.mockImplementation(async (_req: unknown, h: ReturnType<typeof handlersOf>) => {
      h.onStarted?.("run-1");
      h.onLine?.("2 passed");
      h.onExit?.(0);
    });
    mount();

    fireEvent.change(screen.getByLabelText(/command to run/i), { target: { value: "npm test" } });
    await act(async () => {
      fireEvent.keyDown(screen.getByLabelText(/command to run/i), { key: "Enter" });
    });

    expect(screen.getByText("$ npm test")).toBeTruthy();
    expect(screen.getByText("2 passed")).toBeTruthy();
    expect(screen.getByText(/exit 0/)).toBeTruthy();
  });

  it("asks the SERVER to stop, rather than just closing its own ear", async () => {
    // Aborting the fetch stops us listening. The command keeps running, and the panel would show a
    // stopped state for a process that is still holding a port — the failure that makes people
    // reboot instead of debugging.
    let finish: () => void = () => {};
    streamExec.mockImplementation(
      async (_req: unknown, h: ReturnType<typeof handlersOf>) =>
        new Promise<void>((resolve) => {
          h.onStarted?.("run-7");
          h.onLine?.("serving on 3000");
          finish = resolve;
        }),
    );
    mount();

    fireEvent.change(screen.getByLabelText(/command to run/i), { target: { value: "npm run dev" } });
    await act(async () => {
      fireEvent.keyDown(screen.getByLabelText(/command to run/i), { key: "Enter" });
    });
    const stop = await screen.findByText(/^stop$/i);

    await act(async () => {
      fireEvent.click(stop);
      finish();
    });

    expect(cancelExec).toHaveBeenCalledWith("run-7");
  });

  it("stops the command when the panel goes away", async () => {
    // Closing the panel is the same intent as pressing Stop. Without this, switching screens leaves
    // whatever you started running with no way left to see or stop it.
    let finish: () => void = () => {};
    streamExec.mockImplementation(
      async (_req: unknown, h: ReturnType<typeof handlersOf>) =>
        new Promise<void>((resolve) => {
          h.onStarted?.("run-9");
          finish = resolve;
        }),
    );
    const view = mount();
    fireEvent.change(screen.getByLabelText(/command to run/i), { target: { value: "sleep 60" } });
    await act(async () => {
      fireEvent.keyDown(screen.getByLabelText(/command to run/i), { key: "Enter" });
    });

    await act(async () => {
      view.unmount();
      finish();
    });

    expect(cancelExec).toHaveBeenCalledWith("run-9");
  });

  it("walks the history with the arrow keys, newest first", async () => {
    streamExec.mockImplementation(async (_req: unknown, h: ReturnType<typeof handlersOf>) => {
      h.onExit?.(0);
    });
    mount();
    const input = screen.getByLabelText(/command to run/i) as HTMLInputElement;

    for (const entry of ["npm test", "git status"]) {
      fireEvent.change(input, { target: { value: entry } });
      await act(async () => {
        fireEvent.keyDown(input, { key: "Enter" });
      });
    }

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.value).toBe("git status");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input.value).toBe("npm test");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.value).toBe("git status");
    // Down past the newest entry returns to an empty line, not to the newest one — otherwise there
    // is no way back to typing something new without deleting a command by hand.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.value).toBe("");
  });

  it("keeps the history across a reload, per workspace", async () => {
    streamExec.mockImplementation(async (_req: unknown, h: ReturnType<typeof handlersOf>) => {
      h.onExit?.(0);
    });
    const first = mount("/ws-a");
    const input = screen.getByLabelText(/command to run/i);
    fireEvent.change(input, { target: { value: "pytest -q" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    first.unmount();

    mount("/ws-a");
    fireEvent.keyDown(screen.getByLabelText(/command to run/i), { key: "ArrowUp" });
    await waitFor(() =>
      expect((screen.getByLabelText(/command to run/i) as HTMLInputElement).value).toBe("pytest -q"),
    );

    // A different project has its own commands: `npm test` in one repo and `pytest` in another is
    // exactly the pair that makes a shared history useless.
    screen.getByLabelText(/command to run/i);
    const other = mount("/ws-b");
    const inputs = screen.getAllByLabelText(/command to run/i);
    fireEvent.keyDown(inputs[inputs.length - 1], { key: "ArrowUp" });
    expect((inputs[inputs.length - 1] as HTMLInputElement).value).toBe("");
    other.unmount();
  });
});
