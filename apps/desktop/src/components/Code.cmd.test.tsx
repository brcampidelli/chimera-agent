import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getRuns,
  streamExec,
  streamRun,
  type ExecStreamHandlers,
} from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** Type `command` into the command runner and press its Run. The runner's Run carries an explicit
 *  "Run command" label — the agent RunPanel above it has its own, unrelated Run. */
async function runCommand(command: string) {
  const user = userEvent.setup();
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText(/^a command to run/), command);
  await user.click(screen.getByRole("button", { name: "Run command" }));
  return user;
}

describe("Code — the command runner", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
    vi.mocked(streamExec).mockImplementation(async () => {});
  });

  it("runs the command the user typed, in the open workspace", async () => {
    await runCommand("npm test");

    expect(streamExec).toHaveBeenCalledWith(
      { command: "npm test", workspace: null, cwd: "" },
      expect.anything(),
    );
  });

  it("passes the optional cwd through to the command", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.type(screen.getByPlaceholderText(/^a command to run/), "pytest -q");
    await user.type(screen.getByPlaceholderText(/^cwd \(optional/), "backend");
    await user.click(screen.getByRole("button", { name: "Run command" }));

    expect(streamExec).toHaveBeenCalledWith(
      { command: "pytest -q", workspace: null, cwd: "backend" },
      expect.anything(),
    );
  });

  it("refuses to run an empty command", async () => {
    renderWithProviders(<Code />);

    expect(screen.getByRole("button", { name: "Run command" })).toBeDisabled();
    expect(streamExec).not.toHaveBeenCalled();
  });

  it("renders the streamed output lines in the order they arrived", async () => {
    let handlers!: ExecStreamHandlers;
    vi.mocked(streamExec).mockImplementation(async (_req, h: ExecStreamHandlers) => {
      handlers = h;
    });
    await runCommand("npm test");

    await act(async () => {
      handlers.onLine?.("running 3 tests");
      handlers.onLine?.("all good");
      handlers.onExit?.(0);
    });

    const out = await screen.findByText("running 3 tests");
    // Real ordering, not a set: the first line renders before the second.
    expect(out.compareDocumentPosition(screen.getByText("all good"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("renders a zero exit code when the command succeeded", async () => {
    let handlers!: ExecStreamHandlers;
    vi.mocked(streamExec).mockImplementation(async (_req, h: ExecStreamHandlers) => {
      handlers = h;
    });
    await runCommand("true");

    await act(async () => handlers.onExit?.(0));

    expect(await screen.findByText("exit 0")).toBeInTheDocument();
  });

  it("reports a non-zero exit code as such, never as a success", async () => {
    let handlers!: ExecStreamHandlers;
    vi.mocked(streamExec).mockImplementation(async (_req, h: ExecStreamHandlers) => {
      handlers = h;
    });
    await runCommand("npm test");

    await act(async () => {
      handlers.onLine?.("1 failed");
      handlers.onExit?.(1);
    });

    expect(await screen.findByText("exit 1")).toBeInTheDocument();
    expect(screen.queryByText("exit 0")).not.toBeInTheDocument();
  });

  it("surfaces a stream error rather than ending silently", async () => {
    let handlers!: ExecStreamHandlers;
    vi.mocked(streamExec).mockImplementation(async (_req, h: ExecStreamHandlers) => {
      handlers = h;
    });
    await runCommand("npm test");

    await act(async () => handlers.onError?.("network error"));

    expect(await screen.findByText("network error")).toBeInTheDocument();
    // No exit code is invented for a command that never reported one.
    expect(screen.queryByText(/^exit /)).not.toBeInTheDocument();
  });

  it("shows no output area at all before anything has been run", () => {
    renderWithProviders(<Code />);

    expect(screen.queryByText(/^exit /)).not.toBeInTheDocument();
  });

  it("says each run is a fresh subprocess whose cwd and env do not persist", () => {
    renderWithProviders(<Code />);

    expect(
      screen.getByText(
        "Each command is a fresh subprocess — cwd and env don't persist between commands (no cd/export state).",
      ),
    ).toBeInTheDocument();
  });

  it("says it is not an interactive terminal, and renders no prompt", () => {
    renderWithProviders(<Code />);

    expect(screen.getByText(/Not an interactive terminal\./)).toBeInTheDocument();
    // No fake shell prompt / TTY is drawn anywhere on the screen.
    expect(screen.queryByText(/^\$ /)).not.toBeInTheDocument();
  });
});
