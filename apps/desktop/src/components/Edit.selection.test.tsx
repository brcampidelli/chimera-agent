import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EditorView } from "@codemirror/view";

import { Edit } from "@/components/Edit";
import { getFsFile, listRunningTurns, saveFile, streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getFsFile: vi.fn(),
  saveFile: vi.fn(),
  getFsTree: vi.fn(),
  listRunningTurns: vi.fn(),
  listCodeProjects: vi.fn().mockResolvedValue([]),
  registerCodeProject: vi.fn(),
  migrateShellGrants: vi.fn().mockResolvedValue({}),
  streamCodeTurn: vi.fn(),
}));

/**
 * The Editor talking to the agent (study 34, gap #1 against Cursor, VS Code and Zed).
 *
 * What these hold it to: a selection offers "Ask Chimera" and "Change this"; the question leaves
 * with the file, the lines and the text; a proposed change is a diff that is applied by Accept and
 * by nothing else; a file the agent changed on disk is held for review instead of swapped in; and
 * the screen says where you are — project, path, line, column, language — in its own words.
 */

let view: EditorView | null = null;

vi.mock("@/components/editor/Editor", async () => {
  const actual = await vi.importActual<typeof import("@/components/editor/Editor")>("@/components/editor/Editor");
  return {
    Editor: (props: Parameters<typeof actual.Editor>[0]) =>
      actual.Editor({
        ...props,
        onReady: (v) => {
          view = v;
          props.onReady?.(v);
        },
      }),
  };
});

const FILE = "def a():\n    return 1\n\ndef b():\n    return 2\n";
const WS = "/home/me/projects/chimera";

function file(content = FILE, path = "src/app.py") {
  return { content, note: "", path, truncated: false, document: "" };
}

/** Select from the start of line `from` to the end of line `to`, as a drag would. */
function selectLines(from: number, to: number) {
  act(() => {
    const v = view as EditorView;
    v.dispatch({ selection: { anchor: v.state.doc.line(from).from, head: v.state.doc.line(to).to } });
  });
}

/** A turn that answers with `answer`, the way `streamCodeTurn` reports one. */
function answering(answer: string) {
  return async (_req: unknown, h: CodeTurnHandlers) => {
    h.onSession?.("s9");
    h.onToken?.(answer);
    h.onDone?.({ answer } as Parameters<NonNullable<CodeTurnHandlers["onDone"]>>[0]);
  };
}

async function refocus() {
  await act(async () => {
    window.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
  });
}

function mount(path = "src/app.py", onAsk = vi.fn()) {
  renderWithProviders(<Edit workspace={WS} path={path} onOpen={() => {}} onAsk={onAsk} />);
  return onAsk;
}

beforeEach(() => {
  view = null;
  localStorage.clear();
  vi.mocked(getFsFile).mockReset().mockResolvedValue(file());
  vi.mocked(saveFile).mockReset().mockResolvedValue({ bytes: 1, path: "src/app.py" });
  vi.mocked(listRunningTurns).mockReset().mockResolvedValue([]);
  vi.mocked(streamCodeTurn).mockReset();
});

describe("Edit — a selection talks to the agent", () => {
  it("offers nothing without a selection, and both actions with one", async () => {
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    expect(screen.queryByRole("toolbar")).toBeNull();

    selectLines(1, 2);
    const bar = await screen.findByRole("toolbar", { name: /lines 1–2/i });
    expect(within(bar).getByRole("button", { name: /Ask Chimera/ })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /Change this/ })).toBeInTheDocument();
  });

  it("asks with the file, the whole lines and their text, and the words typed", async () => {
    const user = userEvent.setup();
    const onAsk = mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    // From the middle of line 4 to the middle of line 5: the question is about whole lines.
    act(() => {
      const v = view as EditorView;
      v.dispatch({ selection: { anchor: v.state.doc.line(4).from + 4, head: v.state.doc.line(5).from + 6 } });
    });

    await user.click(await screen.findByRole("button", { name: /Ask Chimera/ }));
    await user.type(screen.getByRole("textbox", { name: /Question about lines 4–5/ }), "why 2?{Enter}");

    expect(onAsk).toHaveBeenCalledWith({
      message: "why 2?",
      selection: { path: "src/app.py", startLine: 4, endLine: 5, text: "def b():\n    return 2" },
    });
    // Asking does not write, and does not start a turn here: the conversation sends it.
    expect(streamCodeTurn).not.toHaveBeenCalled();
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("will not send an empty instruction", async () => {
    const user = userEvent.setup();
    const onAsk = mount();
    await waitFor(() => expect(view).not.toBeNull());
    selectLines(1, 1);
    await user.click(await screen.findByRole("button", { name: /Ask Chimera/ }));
    expect(screen.getByRole("button", { name: /^Ask$/ })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: /Question about line/ }), "   {Enter}");
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("asks to save first while the file has unsaved edits, since the agent reads the disk", async () => {
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    act(() => {
      view?.dispatch({ changes: { from: 0, insert: "# " } });
    });
    selectLines(2, 2);
    const bar = await screen.findByRole("toolbar");
    expect(within(bar).getByText(/save to ask Chimera/)).toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: /Ask Chimera|Change this/ })).toBeNull();
  });

  it("opens the bar from the keyboard (Ctrl+L, Ctrl+I) only when something is selected", async () => {
    mount();
    await waitFor(() => expect(view).not.toBeNull());
    const content = (view as EditorView).contentDOM;

    // No selection: the key is CodeMirror's own (select line), and no bar opens.
    fireEvent.keyDown(content, { key: "i", code: "KeyI", ctrlKey: true });
    expect(screen.queryByRole("textbox", { name: /Change to make/ })).toBeNull();

    selectLines(2, 2);
    fireEvent.keyDown(content, { key: "i", code: "KeyI", ctrlKey: true });
    expect(await screen.findByRole("textbox", { name: /Change to make in lines 2/ })).toBeInTheDocument();
  });
});

describe("Edit — Change this", () => {
  async function propose(answer: string, user = userEvent.setup()) {
    vi.mocked(streamCodeTurn).mockImplementation(answering(answer));
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    selectLines(2, 2);
    await user.click(await screen.findByRole("button", { name: /Change this/ }));
    await user.type(screen.getByRole("textbox", { name: /Change to make in lines 2/ }), "return 10{Enter}");
    return user;
  }

  it("asks for a read-only proposal limited to the lines, in the project's conversation", async () => {
    localStorage.setItem(`chimera:code:last-session:${WS}`, "conv-1");
    await propose("```python\n    return 10\n```");

    expect(streamCodeTurn).toHaveBeenCalledOnce();
    const req = vi.mocked(streamCodeTurn).mock.calls[0][0];
    expect(req).toMatchObject({
      message: "return 10",
      session_id: "conv-1",
      workspace: WS,
      posture: { reach: "read_only" },
      // The lines by the one mention door, and the one thing a proposal adds.
      context: [{ kind: "file", path: "src/app.py", start_line: 2, end_line: 2 }],
      propose_change: true,
    });
  });

  it("shows the proposal as a diff and never applies it by itself", async () => {
    await propose("Here:\n```python\n    return 10\n```");

    const diff = await screen.findByLabelText("Proposed change");
    expect(diff.querySelector('[data-kind="del"]')?.textContent).toBe("-     return 1");
    expect(diff.querySelector('[data-kind="add"]')?.textContent).toBe("+     return 10");
    // Arrived, shown — and the file and the editor are exactly as they were.
    expect(saveFile).not.toHaveBeenCalled();
    expect(view?.state.doc.toString()).toBe(FILE);
  });

  it("Accept writes the change through the editor's save", async () => {
    const user = await propose("```\n    return 10\n```");
    await user.click(await screen.findByRole("button", { name: /^Accept$/ }));

    const changed = FILE.replace("return 1\n", "return 10\n");
    await waitFor(() => expect(saveFile).toHaveBeenCalledWith(WS, "src/app.py", changed));
    expect(view?.state.doc.toString()).toBe(changed);
    expect(screen.queryByLabelText("Proposed change")).toBeNull();
  });

  it("Reject discards it: nothing written, nothing changed", async () => {
    const user = await propose("```\n    return 10\n```");
    await user.click(await screen.findByRole("button", { name: /^Reject$/ }));

    expect(screen.queryByLabelText("Proposed change")).toBeNull();
    expect(saveFile).not.toHaveBeenCalled();
    expect(view?.state.doc.toString()).toBe(FILE);
  });

  it("offers nothing to apply when the answer has no replacement block", async () => {
    await propose("I would rather not change that line.");
    expect(await screen.findByText(/without a replacement/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Accept$/ })).toBeNull();
  });

  it("withdraws Accept when the lines changed after the proposal was asked for", async () => {
    await propose("```\n    return 10\n```");
    await screen.findByRole("button", { name: /^Accept$/ });

    act(() => {
      const v = view as EditorView;
      v.dispatch({ changes: { from: v.state.doc.line(2).from, insert: "#" } });
    });

    expect(await screen.findByText(/changed since you asked/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Accept$/ })).toBeNull();
  });
});

describe("Edit — a file the agent changed while it was open", () => {
  const AGENT = FILE.replace("return 2", "return 20");

  it("holds the change for review, named as Chimera's while a turn runs here", async () => {
    vi.mocked(listRunningTurns).mockResolvedValue([
      { turn_id: "t", session_id: "s", workspace: WS, message: "m", started_at: 0, live_since: 0, transcript_saved: false },
    ]);
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));

    vi.mocked(getFsFile).mockResolvedValue(file(AGENT));
    await refocus();

    const banner = await screen.findByRole("region", { name: "Changed by Chimera — review" });
    expect(within(banner).getByLabelText("What changed").querySelector('[data-kind="add"]')?.textContent).toBe(
      "+     return 20",
    );
    // Not reloaded silently, and not typeable over while it waits.
    expect(view?.state.doc.toString()).toBe(FILE);
    expect(view?.state.readOnly).toBe(true);
  });

  it("does not name Chimera when nothing of Chimera's was running", async () => {
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    vi.mocked(getFsFile).mockResolvedValue(file(AGENT));
    await refocus();
    expect(await screen.findByRole("region", { name: "Changed on disk — review" })).toBeInTheDocument();
  });

  it("Undo writes back what was on screen", async () => {
    const user = userEvent.setup();
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    vi.mocked(getFsFile).mockResolvedValue(file(AGENT));
    await refocus();

    // The disk follows the write, as the server's does: the refetch after Undo reads it back.
    vi.mocked(saveFile).mockImplementation(async (_w, p, content) => {
      vi.mocked(getFsFile).mockResolvedValue(file(content, p));
      return { bytes: content.length, path: p };
    });
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(saveFile).toHaveBeenCalledWith(WS, "src/app.py", FILE));
    await waitFor(() => expect(screen.queryByRole("region", { name: /review/ })).toBeNull());
    expect(view?.state.doc.toString()).toBe(FILE);
  });

  it("Keep shows the new text and writes nothing", async () => {
    const user = userEvent.setup();
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    vi.mocked(getFsFile).mockResolvedValue(file(AGENT));
    await refocus();

    await user.click(await screen.findByRole("button", { name: /Keep the change/ }));
    await waitFor(() => expect(view?.state.doc.toString()).toBe(AGENT));
    expect(saveFile).not.toHaveBeenCalled();
    expect(view?.state.readOnly).toBe(false);
  });
});

describe("Edit — where you are", () => {
  it("names the project, with its path on hover, and the file's folders as a trail", async () => {
    mount("src/lib/app.py");
    const trail = await screen.findByRole("navigation", { name: "File path" });
    const project = within(trail).getByTestId("edit-project");
    expect(project).toHaveTextContent(/^chimera$/);
    expect(project).toHaveAttribute("title", WS);
    expect(within(trail).getByText("lib")).toBeInTheDocument();
    expect(within(trail).getByText("app.py")).toHaveAttribute("aria-current", "page");
  });

  it("has its own status bar: line and column, language, encoding, saved", async () => {
    mount();
    await waitFor(() => expect(view?.state.doc.toString()).toBe(FILE));
    const status = screen.getByTestId("edit-status");
    expect(within(status).getByText("Ln 1, Col 1")).toBeInTheDocument();
    expect(within(status).getByText("Python")).toBeInTheDocument();
    expect(within(status).getByText("UTF-8")).toBeInTheDocument();
    expect(within(status).getByText("Saved")).toBeInTheDocument();

    act(() => {
      const v = view as EditorView;
      v.dispatch({ selection: { anchor: v.state.doc.line(2).from + 4 } });
    });
    expect(await within(status).findByText("Ln 2, Col 5")).toBeInTheDocument();
    // The path is the trail's now; the footer no longer carries a truncated copy of it.
    expect(within(status).queryByText("src/app.py")).toBeNull();
  });

  it("calls a file with no grammar plain text rather than guessing", async () => {
    vi.mocked(getFsFile).mockResolvedValue(file("x", "notes.txt"));
    mount("notes.txt");
    expect(await within(await screen.findByTestId("edit-status")).findByText("Plain text")).toBeInTheDocument();
  });
});
