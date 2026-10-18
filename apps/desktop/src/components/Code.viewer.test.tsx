import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsFile,
  getFsImage,
  getGitStatus,
  getPostureFacts,
  getRuns,
  saveFile,
  streamCodeTurn,
  streamRun,
} from "@/lib/api";
import { fsFile, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import type { FsFile } from "@/lib/types";
import { VIEWER_WRAP_KEY } from "@/lib/viewerWrap";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** The default fixture's content as a display-value query sees it (Testing Library trims/normalizes
 *  whitespace, so the file's trailing newline isn't part of the match). */
const LOADED = "print('hi')";

/** The viewer's editor textarea, identified by the draft it holds. */
function editorOf(): HTMLElement {
  return screen.getByDisplayValue(LOADED);
}

/** Render the screen and open `src/app.py` the way the app now offers files: by clicking a path the
 *  agent actually touched, in the transcript.
 *
 *  The file TREE used to be the entry point, and deleting it deleted the only way in — so the
 *  viewer needed a new one rather than deletion. This is the honest replacement: you look at what
 *  the agent read or wrote, not at a browser you have to navigate yourself. It also means the
 *  viewer can only show files that are part of the conversation, which is a real narrowing and the
 *  intended one. */
async function openFile(file: FsFile = fsFile(), path = "src/app.py") {
  const user = userEvent.setup();
  vi.mocked(getFsFile).mockResolvedValue(file);
  vi.mocked(streamCodeTurn).mockImplementation(
    scriptTurn({
      tools: [{ name: "read_file", arguments: { path }, ok: true, observation: "" }],
    }),
  );
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText(/Ask about this code/i), "look at it");
  await user.click(screen.getByRole("button", { name: /Send/ }));
  await user.click(await screen.findByRole("button", { name: path }));
  return user;
}

// `/^Edit$/` rather than `/Edit/`: the composer strip now carries a model picker per role, and the
// one for the Edit role is named "Edit: default · x". Unanchored, this matched both and failed in a
// way that reads like the viewer broke.
describe("Code — the file viewer", () => {
  beforeEach(() => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
    vi.mocked(saveFile).mockResolvedValue({ path: "src/app.py", bytes: 12 });
  });

  it("opens a file read-only, with no editor until Edit is clicked", async () => {
    await openFile();

    expect(await screen.findByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    // The file's real content is rendered (highlighted) — not loaded into an editable field.
    expect(screen.getByText(/print/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(LOADED)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("swaps in an editor holding the loaded content when Edit is clicked", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));

    expect(editorOf()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save/ })).toBeInTheDocument();
  });

  it("saves the draft to the open workspace path and leaves edit mode", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));
    const editor = editorOf();
    await user.clear(editor);
    await user.type(editor, "print('bye')");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(saveFile).toHaveBeenCalledWith(null, "src/app.py", "print('bye')"));
    // Edit mode is left (Edit is offered again) and the save is honestly acknowledged.
    expect(await screen.findByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    expect(screen.getByText("Saved.")).toBeInTheDocument();
  });

  it("marks the draft unsaved only once it differs from the loaded content", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));
    // Freshly entered edit mode: the draft IS the file — nothing is unsaved yet.
    expect(screen.queryByText("unsaved")).not.toBeInTheDocument();

    await user.type(editorOf(), "# note");

    expect(await screen.findByText("unsaved")).toBeInTheDocument();
  });

  it("refuses to save a draft identical to the file on disk", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));

    expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("reverts the draft on Discard without writing anything", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));
    await user.type(editorOf(), "# throwaway");
    await screen.findByText("unsaved");
    await user.click(screen.getByRole("button", { name: /Discard/ }));

    expect(saveFile).not.toHaveBeenCalled();
    expect(screen.queryByText("unsaved")).not.toBeInTheDocument();
    // Back to the read-only view of the untouched file — the throwaway draft is gone.
    expect(await screen.findByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/# throwaway/)).not.toBeInTheDocument();
  });

  it("does not offer to edit a truncated file, whose unseen remainder a save would clobber", async () => {
    await openFile(fsFile({ truncated: true }));

    expect(await screen.findByText("truncated")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  // A long file showed its first 20,000 characters, a "truncated" badge, and no way to read on.
  it("offers the rest of a truncated file and loads it whole when asked", async () => {
    const user = await openFile(fsFile({ truncated: true, total_chars: 54321 }));

    const more = await screen.findByRole("button", {
      name: `Show all (${new Intl.NumberFormat("en").format(54321)} characters)`,
    });
    // On its own row, not in the header beside the file name: there it squeezed the name to its
    // first letters in a narrow viewer (0.65.0 visual check). The full path is the name's title.
    const viewer = more.closest("[data-panel='viewer']") as HTMLElement;
    const header = viewer.firstElementChild as HTMLElement;
    expect(header.contains(more)).toBe(false);
    expect(within(header).getByTitle("src/app.py")).toBeInTheDocument();
    vi.mocked(getFsFile).mockResolvedValue(
      fsFile({ content: "print('all of it')\n", truncated: false, total_chars: 19 }),
    );
    await user.click(more);

    await waitFor(() =>
      expect(vi.mocked(getFsFile)).toHaveBeenLastCalledWith(null, "src/app.py", 1_000_000),
    );
    expect(await screen.findByText(/all of it/)).toBeInTheDocument();
    expect(screen.queryByText("truncated")).not.toBeInTheDocument();
  });

  it("does not offer the rest of a file that is already whole", async () => {
    await openFile(fsFile({ total_chars: 12 }));
    expect(await screen.findByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Show all/ })).not.toBeInTheDocument();
  });

  it("does not offer to edit a binary file, and says why it isn't shown", async () => {
    await openFile(fsFile({ note: "binary", content: "" }));

    expect(await screen.findByText("Binary or non-text file — not shown.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it("shows the chart the agent drew, instead of calling it a binary file", async () => {
    // `render_chart` and `generate_image` write PNGs into the workspace and the viewer answered
    // "binary or non-text" about them — our own app could not display our own tool's output.
    vi.mocked(getFsImage).mockResolvedValue(new Blob([new Uint8Array([137, 80, 78, 71])]));
    await openFile(fsFile({ path: "chart.png", note: "binary", content: "" }), "chart.png");

    const img = await screen.findByRole("img", { name: /chart\.png/ });
    expect(img).toHaveAttribute("src", expect.stringContaining("blob:"));
    expect(getFsImage).toHaveBeenCalledWith(null, "chart.png");
    expect(screen.queryByText("Binary or non-text file — not shown.")).not.toBeInTheDocument();
  });

  it("still refuses to guess at a binary that is not an image", async () => {
    // An <img> pointed at a .zip renders a broken-image icon, which claims a failure that did not
    // happen. The honest note is still the right answer here, and it must not be asked for either.
    await openFile(fsFile({ path: "bundle.zip", note: "binary", content: "" }), "bundle.zip");

    expect(await screen.findByText("Binary or non-text file — not shown.")).toBeInTheDocument();
    expect(getFsImage).not.toHaveBeenCalled();
  });

  it("says an image failed to load rather than showing an empty frame", async () => {
    vi.mocked(getFsImage).mockRejectedValue(new Error("415 unsupported"));
    await openFile(fsFile({ path: "chart.png", note: "binary", content: "" }), "chart.png");

    expect(await screen.findByText("Couldn't load this image.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("surfaces a failed save instead of claiming the file was saved", async () => {
    vi.mocked(saveFile).mockRejectedValue(new Error("413 too large"));
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));
    await user.type(editorOf(), "# more");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    expect(
      await screen.findByText("Couldn't save — is the bearer token required, or the file too large?"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Saved.")).not.toBeInTheDocument();
    // The draft is still in the editor — a failed save must not silently drop the user's work.
    expect(screen.getByDisplayValue(/# more/)).toBeInTheDocument();
  });

  it("warns while editing that a save cannot be undone", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /^Edit$/ }));

    expect(
      screen.getByText("No undo after save (unless this folder is a git repo you commit)."),
    ).toBeInTheDocument();
  });

  // Study 29, P6.3: what the turn produced is one press from the viewer, and a document shows as its
  // text instead of "binary or non-text", which is what "Open beside" would have opened before.
  it("offers to open the document a turn wrote beside the conversation, as a text preview", async () => {
    const user = userEvent.setup();
    vi.mocked(getFsFile).mockResolvedValue(
      fsFile({ path: "report.docx", content: "# Relatório\n\nVendas subiram.", document: "docx" }),
    );
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        tools: [
          { name: "create_document", arguments: { path: "report.docx" }, ok: true, observation: "saved" },
        ],
      }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "write the report");
    await user.click(screen.getByRole("button", { name: /Send/ }));

    await user.click(await screen.findByRole("button", { name: "Open report.docx beside" }));

    expect(getFsFile).toHaveBeenCalledWith(null, "report.docx");
    expect(await screen.findByText(/Text preview of this DOCX/)).toBeInTheDocument();
    expect(screen.getByText(/Vendas subiram\./)).toBeInTheDocument();
    // Its text, not the file: saving it would replace the .docx with its own text.
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Binary or non-text file — not shown.")).not.toBeInTheDocument();
  });

  it("says a document has no preview here rather than calling it binary", async () => {
    await openFile(
      fsFile({ path: "deck.pptx", content: "", document: "pptx", note: "previewing documents needs the 'documents' extra" }),
      "deck.pptx",
    );

    expect(await screen.findByText(/No preview of this PPTX here/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it("offers nothing to open under a turn that produced nothing openable", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        tools: [{ name: "write_file", arguments: { path: "src/app.py" }, ok: true, observation: "" }],
      }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "edit it");
    await user.click(screen.getByRole("button", { name: /Send/ }));

    expect(await screen.findByRole("button", { name: "src/app.py" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /beside$/ })).not.toBeInTheDocument();
  });
});

/** The viewer's own pane — a region named after it — so a path or a button in the transcript is never
 *  mistaken for the viewer's. `queryByRole` skips a hidden region, which is what "closed" is. */
function viewerPane(): HTMLElement | null {
  return screen.queryByRole("region", { name: "File viewer" });
}

// The owner's ask (2026-10-10): the viewer could only be maximised, so a file opened once held a third of
// the window. It now minimises to its header and closes, with the controls the cards and the dock panels
// already carry.
describe("Code — the file viewer minimises and closes", () => {
  beforeEach(() => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
  });

  it("carries minimise, maximise and close, each named for the viewer", async () => {
    await openFile();

    const pane = await waitFor(() => viewerPane() as HTMLElement);
    expect(within(pane).getByRole("button", { name: "Minimize File viewer" })).toHaveAttribute("aria-expanded", "true");
    expect(within(pane).getByRole("button", { name: "Maximize File viewer" })).toBeInTheDocument();
    expect(within(pane).getByRole("button", { name: "Close File viewer" })).toBeInTheDocument();
  });

  it("minimises to its header and restores the content", async () => {
    const user = await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await within(pane).findByText(/print/);

    await user.click(within(pane).getByRole("button", { name: "Minimize File viewer" }));

    // The header stays — which file, and the way back — and the body is gone.
    expect(within(pane).getByText("src/app.py")).toBeInTheDocument();
    expect(within(pane).queryByText(/print/)).not.toBeInTheDocument();
    const expand = within(pane).getByRole("button", { name: "Expand File viewer" });
    expect(expand).toHaveAttribute("aria-expanded", "false");

    await user.click(expand);

    expect(await within(pane).findByText(/print/)).toBeInTheDocument();
  });

  it("closes, leaving the conversation", async () => {
    const user = await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);

    await user.click(within(pane).getByRole("button", { name: "Close File viewer" }));

    expect(viewerPane()).toBeNull();
    expect(screen.getByPlaceholderText(/Ask about this code/i)).toBeInTheDocument();
  });

  it("comes back when a file is opened again", async () => {
    const user = await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await user.click(within(pane).getByRole("button", { name: "Close File viewer" }));
    expect(viewerPane()).toBeNull();

    // The path in the transcript is the way a file is opened; opening it is asking to see it.
    await user.click(screen.getByRole("button", { name: "src/app.py" }));

    const again = await waitFor(() => viewerPane() as HTMLElement);
    expect(await within(again).findByText(/print/)).toBeInTheDocument();
  });

  it("opens a minimised viewer when a file is opened again", async () => {
    const user = await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await user.click(within(pane).getByRole("button", { name: "Minimize File viewer" }));

    await user.click(screen.getByRole("button", { name: "src/app.py" }));

    expect(await within(viewerPane() as HTMLElement).findByText(/print/)).toBeInTheDocument();
  });

  // A markdown paragraph is one long line; unwrapped in a column a few hundred pixels wide it ran off
  // the right edge (the owner's screenshot of `research/SINTESE-final.md`).
  it("wraps a markdown file", async () => {
    await openFile(
      fsFile({ path: "research/SINTESE-final.md", content: "# Síntese\n\nUma linha longa." }),
      "research/SINTESE-final.md",
    );
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await within(pane).findByText(/Síntese/);
    // The wrapping is a class on the <pre>; there is no role to reach it by.
    expect(pane.querySelector("pre")).toHaveClass("whitespace-pre-wrap");
  });

  it("keeps code unwrapped, scrolling sideways", async () => {
    await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await within(pane).findByText(/print/);
    const pre = pane.querySelector("pre");
    expect(pre).toHaveClass("overflow-x-auto");
    expect(pre).not.toHaveClass("whitespace-pre-wrap");
  });

  // C9 of study 34: code had no way to wrap at all — a minified JSON or a log line was read by
  // dragging a scrollbar. The toggle wraps it and is remembered for that kind of file.
  it("wraps code on request, remembers it, and leaves prose's default alone", async () => {
    localStorage.clear();
    const user = await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await within(pane).findByText(/print/);
    const toggle = within(pane).getByRole("button", { name: "Wrap long lines" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(pane.querySelector("pre")).toHaveClass("whitespace-pre-wrap");
    expect(JSON.parse(localStorage.getItem(VIEWER_WRAP_KEY) ?? "{}")).toEqual({ code: true });

    await user.click(toggle);
    expect(pane.querySelector("pre")).toHaveClass("overflow-x-auto");
    expect(JSON.parse(localStorage.getItem(VIEWER_WRAP_KEY) ?? "{}")).toEqual({ code: false });
  });

  it("opens code wrapped once that was chosen", async () => {
    localStorage.clear();
    localStorage.setItem(VIEWER_WRAP_KEY, JSON.stringify({ code: true }));
    await openFile();
    const pane = await waitFor(() => viewerPane() as HTMLElement);
    await within(pane).findByText(/print/);
    expect(pane.querySelector("pre")).toHaveClass("whitespace-pre-wrap");
    expect(within(pane).getByRole("button", { name: "Wrap long lines" })).toHaveAttribute("aria-pressed", "true");
  });
});
