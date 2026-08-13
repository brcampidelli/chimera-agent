import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EditorView } from "@codemirror/view";

import { Edit, baseName } from "@/components/Edit";
import { getFsFile, saveFile } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getFsFile: vi.fn(),
  saveFile: vi.fn(),
  getFsTree: vi.fn(),
}));

/**
 * The editor SCREEN, which is about files rather than about text.
 *
 * What it owns and what these tests hold it to: a tab strip that reflects what is open, a dirty
 * marker that means something, a save that sends what you typed, and a refusal to save a file the
 * server only partially read. The text itself belongs to `Editor.test.tsx`.
 */

/** The live CodeMirror view of whichever file is showing, captured through `onReady`. */
let view: EditorView | null = null;

vi.mock("@/components/editor/Editor", async () => {
  const actual =
    await vi.importActual<typeof import("@/components/editor/Editor")>(
      "@/components/editor/Editor",
    );
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

/**
 * Renders `Edit` with the URL it would have — the parent owns the path, exactly as `App` does.
 *
 * `opens` become buttons that stand in for the file tree, so a test can open a second file the same
 * way a person does. Driving it by re-rendering with a different prop would not work: the path is
 * state here (as it is a URL there), so a changed prop after mount changes nothing.
 */
function EditHost({ initial = null as string | null, opens = [] as string[] }) {
  const [path, setPath] = useState<string | null>(initial);
  return (
    <>
      {opens.map((p) => (
        <button key={p} onClick={() => setPath(p)}>
          {`open ${p}`}
        </button>
      ))}
      <Edit workspace="/w" path={path} onOpen={setPath} />
    </>
  );
}

function file(over: Partial<{ content: string; note: string; path: string; truncated: boolean }> = {}) {
  return { content: "print('hi')\n", note: "", path: "src/app.py", truncated: false, ...over };
}

/**
 * Come back to the window.
 *
 * React Query v5 listens for `visibilitychange` ON THE WINDOW, and not for `focus` — dispatching the latter looks
 * right, does nothing, and makes a refetch test pass for the wrong reason (it never refetches, so
 * nothing can disagree). `flushPromises` after it because the refetch is a real async round trip.
 */
async function refocus() {
  await act(async () => {
    window.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
  });
}

/** Type into the editor through the same transaction path a keystroke takes. */
function type(insert: string) {
  act(() => {
    view?.dispatch({ changes: { from: 0, insert } });
  });
}

beforeEach(() => {
  view = null;
  vi.mocked(getFsFile).mockResolvedValue(file());
  vi.mocked(saveFile).mockResolvedValue({ bytes: 12, path: "src/app.py" });
});

describe("baseName", () => {
  it("names a tab after the file, not the path", () => {
    expect(baseName("src/lib/api.ts")).toBe("api.ts");
    expect(baseName("C:\\work\\main.py")).toBe("main.py");
    expect(baseName("README.md")).toBe("README.md");
  });
});

describe("Edit", () => {
  it("says nothing is open rather than showing an empty editor", async () => {
    renderWithProviders(<EditHost />);
    expect(await screen.findByText(/No file open/i)).toBeInTheDocument();
  });

  it("opens the file the URL names", async () => {
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view?.state.doc.toString()).toBe("print('hi')\n"));
    // And the strip carries it, so the way back to it is visible.
    expect(screen.getByRole("button", { name: "app.py" })).toBeInTheDocument();
  });

  it("marks a file dirty only while it differs from disk", async () => {
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    expect(screen.getByText(/^Saved$/)).toBeInTheDocument();

    type("x");
    expect(await screen.findByRole("button", { name: /^Save$/ })).toBeInTheDocument();

    // Undoing back to the original is not a change. Without this the dot stays lit and the save
    // writes a file that is byte-identical to the one on disk — a write nobody asked for.
    act(() => {
      view?.dispatch({ changes: { from: 0, to: 1, insert: "" } });
    });
    await waitFor(() => expect(screen.getByText(/^Saved$/)).toBeInTheDocument());
  });

  it("saves what you typed, to the path you typed it in", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());

    type("# ");
    await user.click(await screen.findByRole("button", { name: /^Save$/ }));

    await waitFor(() =>
      expect(saveFile).toHaveBeenCalledWith("/w", "src/app.py", "# print('hi')\n"),
    );
    // And the screen stops claiming unsaved work once the write lands.
    await waitFor(() => expect(screen.getByText(/^Saved$/)).toBeInTheDocument());
  });

  it("says so when the save fails instead of pretending it worked", async () => {
    // The dangerous version of this bug is silent: the dot clears, the file is unchanged on disk,
    // and you find out at the next reload.
    const user = userEvent.setup();
    vi.mocked(saveFile).mockRejectedValue(new Error("permission denied"));
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());

    type("# ");
    await user.click(await screen.findByRole("button", { name: /^Save$/ }));

    expect(await screen.findByText(/Could not save/i)).toBeInTheDocument();
    // Still dirty, because it still is.
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeInTheDocument();
  });

  it("refuses to edit a file the server only partly read", async () => {
    // `truncated` means the content is a PREFIX. Saving it would delete everything past the cut,
    // which is data loss dressed up as a save — so the editor locks rather than warning.
    vi.mocked(getFsFile).mockResolvedValue(file({ truncated: true }));
    renderWithProviders(<EditHost initial="src/app.py" />);

    expect(await screen.findByText(/saving would delete the rest/i)).toBeInTheDocument();
    await waitFor(() => expect(view?.state.readOnly).toBe(true));
    expect(screen.getByText(/Read-only/i)).toBeInTheDocument();
  });

  it("shows the server's note in the server's words", async () => {
    vi.mocked(getFsFile).mockResolvedValue(file({ note: "binary file", content: "" }));
    renderWithProviders(<EditHost initial="src/app.py" />);
    expect(await screen.findByText("binary file")).toBeInTheDocument();
  });

  it("keeps an unsaved edit while you look at another file", async () => {
    // The reason tabs exist. Losing a draft because you checked something next door is the kind of
    // small betrayal that teaches people not to trust an editor.
    const user = userEvent.setup();
    vi.mocked(getFsFile).mockImplementation((_w, p) =>
      Promise.resolve(file({ path: p, content: p === "a.py" ? "A\n" : "B\n" })),
    );
    renderWithProviders(<EditHost initial="a.py" opens={["b.py"]} />);
    await waitFor(() => expect(view?.state.doc.toString()).toBe("A\n"));

    type("# draft ");
    await user.click(screen.getByRole("button", { name: "open b.py" }));
    await waitFor(() => expect(view?.state.doc.toString()).toBe("B\n"));

    await user.click(screen.getByRole("button", { name: "a.py" }));
    await waitFor(() => expect(view?.state.doc.toString()).toBe("# draft A\n"));
    // And it is still unsaved, so the save button is still the offer being made.
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeInTheDocument();
  });

  it("closing a tab moves to its left-hand neighbour", async () => {
    const user = userEvent.setup();
    // Each file's content is its own name, so "which file am I looking at" has an answer that does
    // not depend on reading the tab strip — the very thing under test.
    vi.mocked(getFsFile).mockImplementation((_w, p) =>
      Promise.resolve(file({ path: p, content: p })),
    );
    renderWithProviders(<EditHost initial="a.py" opens={["b.py", "c.py"]} />);

    await user.click(screen.getByRole("button", { name: "open b.py" }));
    await user.click(screen.getByRole("button", { name: "open c.py" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "c.py" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Close c\.py/i }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "c.py" })).toBeNull());
    // b.py, where the eye already was — not a.py at the far end of the strip.
    expect(screen.getByRole("button", { name: /Close b\.py/i })).toBeInTheDocument();
    await waitFor(() => expect(view?.state.doc.toString()).toBe("b.py"));
  });

  it("says when the agent changed the file underneath an unsaved edit", async () => {
    // The premise of the whole app is that something else is editing these files. Taking the new
    // content would throw away typing; taking neither and staying silent would mean the next save
    // quietly reverts a run's work. So: keep the draft on screen, and say what happened.
    const user = userEvent.setup();
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    type("# mine ");

    vi.mocked(getFsFile).mockResolvedValue(file({ content: "print('agent wrote this')\n" }));
    await refocus();

    expect(await screen.findByText(/changed on disk/i)).toBeInTheDocument();
    // The draft is untouched — nothing was overwritten to deliver the news.
    expect(view?.state.doc.toString()).toBe("# mine print('hi')\n");

    await user.click(screen.getByRole("button", { name: /Use the file on disk/i }));
    await waitFor(() => expect(view?.state.doc.toString()).toBe("print('agent wrote this')\n"));
    expect(screen.queryByText(/changed on disk/i)).toBeNull();
  });

  it("keeping your version dismisses the warning without discarding anything", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    type("# mine ");

    vi.mocked(getFsFile).mockResolvedValue(file({ content: "print('agent wrote this')\n" }));
    await refocus();
    await user.click(await screen.findByRole("button", { name: /Keep my version/i }));

    expect(screen.queryByText(/changed on disk/i)).toBeNull();
    expect(view?.state.doc.toString()).toBe("# mine print('hi')\n");
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeInTheDocument();
  });

  it("does not cry conflict when the file is merely re-read", async () => {
    // A refetch that returns the same bytes is the common case, and a warning there would train
    // people to dismiss the one that matters.
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    type("# mine ");

    await refocus();

    expect(screen.queryByText(/changed on disk/i)).toBeNull();
  });

  it("closing the last tab leaves nothing open, not a stale file", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EditHost initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());

    await user.click(screen.getByRole("button", { name: /Close app\.py/i }));

    expect(await screen.findByText(/No file open/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "app.py" })).not.toBeInTheDocument();
  });
});
