import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/lib/i18n";
import { isWebLink, MAX_TABS, TerminalPanel } from "@/components/code/TerminalPanel";

// xterm needs a canvas and layout that jsdom does not have; what is tested here is the PANEL — when it
// opens a shell, in which folder, how many, and what closes them — so the emulator is a stub that
// records what it was asked.
const emulators: {
  written: string[];
  dispose: ReturnType<typeof vi.fn>;
  emit: (data: string) => void;
}[] = [];

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 100;
    rows = 30;
    written: string[] = [];
    dispose = vi.fn();
    private handler: ((d: string) => void) | null = null;
    constructor() {
      emulators.push({ written: this.written, dispose: this.dispose, emit: (d) => this.handler?.(d) });
    }
    loadAddon() {}
    open() {}
    focus() {}
    attachCustomKeyEventHandler() {}
    onResize() {}
    onData(cb: (d: string) => void) {
      this.handler = cb;
    }
    write(text: string) {
      this.written.push(text);
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

const api = vi.hoisted(() => ({
  getTerminalStatus: vi.fn(),
  openTerminal: vi.fn(),
  closeTerminal: vi.fn(),
  sendTerminalInput: vi.fn(),
  resizeTerminal: vi.fn(),
  streamTerminal: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), ...api }));

let counter = 0;

beforeEach(() => {
  counter = 0;
  emulators.length = 0;
  api.openTerminal.mockImplementation(async () => ({ id: `t${++counter}`, shell: "sh", cwd: "/w" }));
  api.getTerminalStatus.mockResolvedValue({ available: true, reason: "", open: 0, max: 8 });
  api.closeTerminal.mockResolvedValue({ ok: true });
  api.sendTerminalInput.mockResolvedValue({ ok: true });
  api.resizeTerminal.mockResolvedValue({ ok: true });
  // A stream that stays open until aborted, like a live shell.
  api.streamTerminal.mockImplementation(
    (_id: string, _h: unknown, signal: AbortSignal) =>
      new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve())),
  );
});

afterEach(() => vi.clearAllMocks());

function panel(props: Partial<React.ComponentProps<typeof TerminalPanel>> = {}) {
  return (
    <I18nProvider>
      <TerminalPanel workspace="/work/tree" commandsOn visible onHide={() => {}} {...props} />
    </I18nProvider>
  );
}

describe("TerminalPanel", () => {
  it("opens one shell in the conversation's folder when first shown", async () => {
    render(panel());
    await waitFor(() => expect(api.openTerminal).toHaveBeenCalledTimes(1));
    expect(api.openTerminal).toHaveBeenCalledWith("/work/tree", 100, 30);
  });

  it("starts no shell while hidden", async () => {
    render(panel({ visible: false }));
    await act(async () => {});
    expect(api.openTerminal).not.toHaveBeenCalled();
  });

  it("says why a machine cannot host a terminal, and asks for no shell", async () => {
    api.getTerminalStatus.mockResolvedValueOnce({ available: false, reason: "needs pywinpty", open: 0, max: 8 });
    render(panel());
    expect(await screen.findByText(/not available here: needs pywinpty/)).toBeInTheDocument();
    expect(api.openTerminal).not.toHaveBeenCalled();
  });

  it("with commands off it asks for no shell and says why", async () => {
    render(panel({ commandsOn: false }));
    await act(async () => {});
    expect(api.openTerminal).not.toHaveBeenCalled();
    expect(screen.getByText(/Commands are off for this folder/)).toBeInTheDocument();
  });

  it("sends what is typed, batched, to the shell it belongs to", async () => {
    render(panel());
    await waitFor(() => expect(api.streamTerminal).toHaveBeenCalled());
    act(() => {
      emulators[0].emit("l");
      emulators[0].emit("s\r");
    });
    await waitFor(() => expect(api.sendTerminalInput).toHaveBeenCalledWith("t1", "ls\r"));
    expect(api.sendTerminalInput).toHaveBeenCalledTimes(1);
  });

  it("opens a second tab with + and closes its shell with its x", async () => {
    const user = userEvent.setup();
    render(panel());
    await waitFor(() => expect(api.streamTerminal).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "New terminal" }));
    await waitFor(() => expect(api.openTerminal).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "Close terminal 2" }));
    await waitFor(() => expect(api.closeTerminal).toHaveBeenCalledWith("t2"));
    expect(api.closeTerminal).not.toHaveBeenCalledWith("t1");
  });

  it("stops offering + at the cap", async () => {
    const user = userEvent.setup();
    render(panel());
    await waitFor(() => expect(api.openTerminal).toHaveBeenCalledTimes(1));
    for (let i = 1; i < MAX_TABS; i++) {
      await user.click(screen.getByRole("button", { name: "New terminal" }));
    }
    await waitFor(() => expect(api.openTerminal).toHaveBeenCalledTimes(MAX_TABS));
    expect(screen.getByRole("button", { name: "New terminal" })).toBeDisabled();
  });

  it("closing the last tab hides the panel", async () => {
    const user = userEvent.setup();
    const onHide = vi.fn();
    render(panel({ onHide }));
    await waitFor(() => expect(api.streamTerminal).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Close terminal 1" }));
    expect(onHide).toHaveBeenCalled();
  });

  it("leaving the conversation ends the shells", async () => {
    const { unmount } = render(panel());
    await waitFor(() => expect(api.streamTerminal).toHaveBeenCalled());
    unmount();
    await waitFor(() => expect(api.closeTerminal).toHaveBeenCalledWith("t1"));
    expect(emulators[0].dispose).toHaveBeenCalled();
  });

  it("shows why a shell could not start", async () => {
    api.openTerminal.mockRejectedValueOnce(new Error("at most 8 terminals at once"));
    render(panel());
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 8 terminals at once");
  });

  it("says when the shell ends", async () => {
    api.streamTerminal.mockImplementationOnce(async (_id: string, h: { onExit: (c: number) => void }) => {
      h.onExit(0);
    });
    render(panel());
    await waitFor(() => expect(emulators[0]?.written.join("")).toContain("the shell ended, code 0"));
  });
});

describe("links a program prints", () => {
  it("follows only web links", () => {
    expect(isWebLink("https://example.com/a")).toBe(true);
    expect(isWebLink("http://localhost:3000")).toBe(true);
    for (const bad of ["file:///etc/passwd", "javascript:alert(1)", "tauri://localhost", "ssh://host", "example.com", ""]) {
      expect(isWebLink(bad)).toBe(false);
    }
  });
});
