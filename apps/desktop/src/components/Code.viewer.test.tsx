import { screen, waitFor } from "@testing-library/react";
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

    expect(await screen.findByRole("button", { name: /Edit/ })).toBeInTheDocument();
    // The file's real content is rendered (highlighted) — not loaded into an editable field.
    expect(screen.getByText(/print/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(LOADED)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("swaps in an editor holding the loaded content when Edit is clicked", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /Edit/ }));

    expect(editorOf()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save/ })).toBeInTheDocument();
  });

  it("saves the draft to the open workspace path and leaves edit mode", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /Edit/ }));
    const editor = editorOf();
    await user.clear(editor);
    await user.type(editor, "print('bye')");
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(saveFile).toHaveBeenCalledWith(null, "src/app.py", "print('bye')"));
    // Edit mode is left (Edit is offered again) and the save is honestly acknowledged.
    expect(await screen.findByRole("button", { name: /Edit/ })).toBeInTheDocument();
    expect(screen.getByText("Saved.")).toBeInTheDocument();
  });

  it("marks the draft unsaved only once it differs from the loaded content", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /Edit/ }));
    // Freshly entered edit mode: the draft IS the file — nothing is unsaved yet.
    expect(screen.queryByText("unsaved")).not.toBeInTheDocument();

    await user.type(editorOf(), "# note");

    expect(await screen.findByText("unsaved")).toBeInTheDocument();
  });

  it("refuses to save a draft identical to the file on disk", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /Edit/ }));

    expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("reverts the draft on Discard without writing anything", async () => {
    const user = await openFile();

    await user.click(await screen.findByRole("button", { name: /Edit/ }));
    await user.type(editorOf(), "# throwaway");
    await screen.findByText("unsaved");
    await user.click(screen.getByRole("button", { name: /Discard/ }));

    expect(saveFile).not.toHaveBeenCalled();
    expect(screen.queryByText("unsaved")).not.toBeInTheDocument();
    // Back to the read-only view of the untouched file — the throwaway draft is gone.
    expect(await screen.findByRole("button", { name: /Edit/ })).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/# throwaway/)).not.toBeInTheDocument();
  });

  it("does not offer to edit a truncated file, whose unseen remainder a save would clobber", async () => {
    await openFile(fsFile({ truncated: true }));

    expect(await screen.findByText("truncated")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
  });

  it("does not offer to edit a binary file, and says why it isn't shown", async () => {
    await openFile(fsFile({ note: "binary", content: "" }));

    expect(await screen.findByText("Binary or non-text file — not shown.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
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

    await user.click(await screen.findByRole("button", { name: /Edit/ }));
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

    await user.click(await screen.findByRole("button", { name: /Edit/ }));

    expect(
      screen.getByText("No undo after save (unless this folder is a git repo you commit)."),
    ).toBeInTheDocument();
  });
});
