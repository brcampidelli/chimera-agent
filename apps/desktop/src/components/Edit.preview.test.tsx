import { useState } from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EditorView } from "@codemirror/view";

import { Edit } from "@/components/Edit";
import { FileTree } from "@/components/editor/FileTree";
import { findPaths, getFsFile, getGitStatus, saveFile } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getFsFile: vi.fn(),
  saveFile: vi.fn(),
  getFsTree: vi.fn().mockResolvedValue({ workspace: "/w", path: "", capped: false, entries: [] }),
  getGitStatus: vi.fn(),
  findPaths: vi.fn(),
  listRunningTurns: vi.fn().mockResolvedValue([]),
  listCodeProjects: vi.fn().mockResolvedValue([]),
  registerCodeProject: vi.fn(),
  migrateShellGrants: vi.fn().mockResolvedValue({}),
  streamCodeTurn: vi.fn(),
}));

/**
 * The Editor's second round (editor part 2): the empty state that offers a way back into work,
 * Ctrl+P, the rendered page of an .html file, and a tab that will not drop typing on a stray click.
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

const PAGE = "<h1>Hello</h1><script>1+1</script>";

function Host({ initial = null as string | null, opens = [] as string[] }) {
  const [path, setPath] = useState<string | null>(initial);
  return (
    <>
      {opens.map((p) => (
        <button key={p} onClick={() => setPath(p)}>
          {`open ${p}`}
        </button>
      ))}
      <Edit workspace="/w" path={path} onOpen={setPath} />
      <output data-testid="current">{path ?? "none"}</output>
    </>
  );
}

function type(insert: string) {
  act(() => {
    view?.dispatch({ changes: { from: 0, insert } });
  });
}

function status(files: Array<{ path: string; x: string; y: string; untracked?: boolean }>) {
  return {
    is_repo: true,
    branch: "main",
    worktree: false,
    files: files.map((f) => ({ staged: false, untracked: false, ...f })),
  };
}

beforeEach(() => {
  view = null;
  localStorage.clear();
  vi.mocked(getFsFile).mockReset().mockImplementation((_w, p) =>
    Promise.resolve({
      content: p.endsWith(".html") ? PAGE : "print('hi')\n",
      note: "",
      path: p,
      truncated: false,
      document: "",
    }),
  );
  vi.mocked(saveFile).mockReset().mockResolvedValue({ bytes: 1, path: "x" });
  vi.mocked(getGitStatus).mockReset().mockResolvedValue(status([]));
  vi.mocked(findPaths).mockReset().mockResolvedValue({ capped: false, entries: [] });
});

describe("Edit — the empty state", () => {
  it("offers the files you had open, the files git says changed, and the quick-open chord", async () => {
    localStorage.setItem("chimera:editor:recent", JSON.stringify({ "/w": ["src/app.py", "docs/guide.md"] }));
    vi.mocked(getGitStatus).mockResolvedValue(
      status([
        { path: "src/api.py", x: " ", y: "M" },
        { path: "gone.py", x: " ", y: "D" },
      ]),
    );
    const user = userEvent.setup();
    renderWithProviders(<Host />);

    expect(await screen.findByRole("region", { name: "Recent files" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /guide\.md/ })).toBeInTheDocument();
    const changed = await screen.findByRole("region", { name: "Changed in this folder" });
    expect(changed).toHaveTextContent("api.py");
    // A deleted file has nothing to open, so it is not offered.
    expect(changed).not.toHaveTextContent("gone.py");
    expect(screen.getByRole("button", { name: /Open a file by name/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /guide\.md/ }));
    expect(screen.getByTestId("current")).toHaveTextContent("docs/guide.md");
  });

  it("remembers what was opened, per project, for the next visit", async () => {
    renderWithProviders(<Host initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    expect(JSON.parse(localStorage.getItem("chimera:editor:recent") ?? "{}")["/w"]).toEqual(["src/app.py"]);
  });
});

describe("Edit — Ctrl+P", () => {
  it("opens a file by the name you type, taking the key before the editor can", async () => {
    vi.mocked(findPaths).mockResolvedValue({
      capped: false,
      entries: [
        { path: "src/login", is_dir: true },
        { path: "src/login.py", is_dir: false },
        { path: "tests/test_login.py", is_dir: false },
      ],
    });
    const user = userEvent.setup();
    renderWithProviders(<Host initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());

    // The key is pressed with focus in the editor; the browser's own meaning (print) is refused.
    const notPrevented = fireEvent.keyDown(view!.contentDOM, { key: "p", code: "KeyP", ctrlKey: true });
    expect(notPrevented).toBe(false);

    const box = await screen.findByRole("combobox");
    await user.type(box, "login");
    await waitFor(() => expect(findPaths).toHaveBeenLastCalledWith("/w", "login", 30));
    // Folders are not files: only the two files are offered.
    expect(await screen.findAllByRole("option")).toHaveLength(2);

    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("tests/test_login.py"));
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("says when nothing matches", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Host />);
    await user.click(await screen.findByRole("button", { name: /Open a file by name/ }));
    await user.type(await screen.findByRole("combobox"), "zzz");
    expect(await screen.findByText("No file matches.")).toBeInTheDocument();
  });
});

describe("Edit — HTML preview", () => {
  it("renders the page in the sandboxed frame the Code screen uses, and goes back to the source", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<Host initial="site/index.html" />);
    await waitFor(() => expect(view?.state.doc.toString()).toBe(PAGE));

    await user.click(screen.getByRole("button", { name: "Preview" }));

    const frame = await waitFor(() => {
      const f = container.querySelector("iframe");
      expect(f).not.toBeNull();
      return f as HTMLIFrameElement;
    });
    // The same sandbox, not a wider one: scripts yes, same-origin no.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Hello</h1>");

    await user.click(screen.getByRole("button", { name: "Show the source" }));
    expect(container.querySelector("iframe")).toBeNull();
    // The editor was never torn down: the text is where it was.
    expect(view?.state.doc.toString()).toBe(PAGE);
  });

  it("previews what is in the editor, unsaved typing included", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<Host initial="site/index.html" />);
    await waitFor(() => expect(view?.state.doc.toString()).toBe(PAGE));
    type("<p>draft</p>");

    await user.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(container.querySelector("iframe")?.getAttribute("srcdoc")).toContain("<p>draft</p>"));
  });

  it("offers no preview for a file that is not a page", async () => {
    renderWithProviders(<Host initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  });
});

describe("Edit — closing a tab with unsaved changes", () => {
  async function dirtyTab() {
    const user = userEvent.setup();
    renderWithProviders(<Host initial="src/app.py" opens={["other.py"]} />);
    await waitFor(() => expect(view).not.toBeNull());
    type("# draft ");
    await user.click(screen.getByRole("button", { name: /Close app\.py/ }));
    return user;
  }

  it("asks first, and 'keep editing' leaves the tab and the typing alone", async () => {
    const user = await dirtyTab();
    expect(await screen.findByRole("dialog", { name: "Close app.py?" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(saveFile).not.toHaveBeenCalled();
    expect(view?.state.doc.toString()).toBe("# draft print('hi')\n");
    expect(screen.getByTestId("current")).toHaveTextContent("src/app.py");
  });

  it("closes and drops the draft when you say so", async () => {
    const user = await dirtyTab();
    await user.click(await screen.findByRole("button", { name: "Close without saving" }));

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("none"));
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("saves what you typed, then closes", async () => {
    const user = await dirtyTab();
    await user.click(await screen.findByRole("button", { name: "Save and close" }));

    await waitFor(() => expect(saveFile).toHaveBeenCalledWith("/w", "src/app.py", "# draft print('hi')\n"));
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("none"));
  });

  it("does not close when the save fails: the typing is the only copy", async () => {
    vi.mocked(saveFile).mockRejectedValue(new Error("disk full"));
    const user = await dirtyTab();
    await user.click(await screen.findByRole("button", { name: "Save and close" }));

    expect(await screen.findByText("Could not save")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("current")).toHaveTextContent("src/app.py");
  });

  it("closes a clean tab without asking", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Host initial="src/app.py" />);
    await waitFor(() => expect(view).not.toBeNull());
    await user.click(screen.getByRole("button", { name: /Close app\.py/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("none"));
  });
});

describe("Edit — git marks follow saves", () => {
  it("asks git again after a save, so the tree's marks are not stale", async () => {
    const user = userEvent.setup();
    // The tree beside the editor is what observes the status, exactly as on the real screen.
    renderWithProviders(
      <>
        <FileTree workspace="/w" activePath={null} onOpen={() => {}} />
        <Host initial="src/app.py" />
      </>,
    );
    await waitFor(() => expect(view).not.toBeNull());
    await waitFor(() => expect(getGitStatus).toHaveBeenCalled());
    const before = vi.mocked(getGitStatus).mock.calls.length;

    type("# x ");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() => expect(vi.mocked(getGitStatus).mock.calls.length).toBeGreaterThan(before));
  });
});
