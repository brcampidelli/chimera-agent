import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesignPane, { type AgentEdit } from "@/components/design/DesignPane";
import {
  ApiError,
  applyDesignOps,
  checkDesign,
  createDesign,
  deleteDesign,
  exportDesign,
  getDesign,
  listDesignVersions,
  proposeDesign,
  refineDesign,
  restoreDesignVersion,
  type DesignProposal,
} from "@/lib/api";
import { ToastProvider } from "@/components/ui/toast";
import { channelsToHex } from "@/lib/design/defaults";
import { designIdFromSearch } from "@/lib/design/deepLink";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import { applyOps } from "@/lib/design/ops";
import { sampleDocument } from "@/test/design-fixture";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getDesign: vi.fn(),
    applyDesignOps: vi.fn(),
    listDesigns: vi.fn(async () => []),
    createDesign: vi.fn(),
    listDesignVersions: vi.fn(),
    restoreDesignVersion: vi.fn(),
    deleteDesign: vi.fn(),
    proposeDesign: vi.fn(),
    refineDesign: vi.fn(),
    auditDesign: vi.fn(),
    checkDesign: vi.fn(),
    exportDesign: vi.fn(),
  };
});

/** A server that keeps one design and answers `/ops` the way the real one does: 409 when the
 *  expected revision is stale, otherwise apply and bump the revision by one. */
let server: DesignDocument;
function serve(initial: DesignDocument) {
  server = initial;
  vi.mocked(getDesign).mockImplementation(async () => structuredClone(server));
  vi.mocked(applyDesignOps).mockImplementation(async (_id, expected, ops) => {
    if (expected !== server.revision) throw new ApiError("the design moved on", 409);
    const out = applyOps(server, ops as DesignOperation[]);
    server = { ...out.document, revision: expected + 1 };
    return { document: structuredClone(server), inverse: out.inverse };
  });
}

function doc(revision = 1): DesignDocument {
  return { ...sampleDocument(), revision };
}

function render(agentEdit: AgentEdit | null = null, brief?: string) {
  const user = userEvent.setup();
  const props = { onOpen: vi.fn(), onClose: vi.fn() };
  const view = renderWithProviders(
    <ToastProvider>
      <DesignPane target={{ id: "doc_1", brief, key: 1 }} agentEdit={agentEdit} autosaveMs={0} {...props} />
    </ToastProvider>,
  );
  return { user, view, ...props };
}

function indicator(): HTMLElement {
  return document.querySelector("[data-save-state]") as HTMLElement;
}

/** Select the title with the keyboard and nudge it one pixel right: one `node.move`. */
async function nudgeTitle(user: ReturnType<typeof userEvent.setup>) {
  const canvas = await screen.findByRole("application");
  act(() => canvas.focus());
  await user.keyboard("n{ArrowRight}");
}

beforeEach(() => {
  vi.mocked(applyDesignOps).mockReset();
  vi.mocked(restoreDesignVersion).mockReset();
  vi.mocked(proposeDesign).mockReset();
  vi.mocked(exportDesign).mockReset();
});

describe("autosave", () => {
  it("sends each edit against the revision it started from and says it is saved", async () => {
    serve(doc(1));
    const { user } = render();
    await nudgeTitle(user);
    await waitFor(() => expect(indicator()).toHaveAttribute("data-save-state", "saved"));
    expect(applyDesignOps).toHaveBeenCalledWith("doc_1", 1, [expect.objectContaining({ type: "node.move", nodeId: "title" })]);
    expect(indicator()).toHaveTextContent("Saved · revision 2");
    expect(server.nodes.title.frame.x).toBe(101);
  });

  it("on 409 reads the newer design, replays the edit over it, and saves the merge", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    // Someone else saved meanwhile: the title changed and the revision moved to 2.
    server = { ...server, title: "Renamed elsewhere", revision: 2 };
    await nudgeTitle(user);
    await waitFor(() => expect(indicator()).toHaveTextContent("Saved · revision 3"));
    expect(indicator()).toHaveTextContent("merged with a newer version");
    expect(vi.mocked(applyDesignOps).mock.calls.map((c) => c[1])).toEqual([1, 2]);
    expect(server.title).toBe("Renamed elsewhere");
    expect(server.nodes.title.frame.x).toBe(101);
  });

  it("on 409 that its edits no longer fit, keeps the newer design and tells the owner", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    await nudgeTitle(user);
    // Arranged after the local edit is queued, before it is sent: the other side deleted the title.
    await waitFor(() => expect(applyDesignOps).toHaveBeenCalledTimes(1));
    expect(indicator()).toHaveAttribute("data-save-state", "saved");

    const removed = structuredClone(server);
    delete (removed.nodes as Record<string, unknown>).title;
    removed.pages[0].artboards[0].nodeIds = ["box", "dot"];
    server = { ...removed, revision: server.revision + 1 };
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(indicator()).toHaveAttribute("data-save-state", "conflict"));
    expect(indicator()).toHaveTextContent("no longer fit");
    expect(applyDesignOps).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("says offline when the server cannot be reached, and keeps the edit queued", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    vi.mocked(applyDesignOps).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await nudgeTitle(user);
    await waitFor(() => expect(indicator()).toHaveAttribute("data-save-state", "offline"));
    expect(indicator()).toHaveTextContent("Offline");
    // Retry by hand: the same edit, against the same revision.
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(indicator()).toHaveTextContent("Saved · revision 2"));
    expect(vi.mocked(applyDesignOps).mock.calls[1][2]).toEqual(vi.mocked(applyDesignOps).mock.calls[0][2]);
  });
});

describe("a proposal is shown first and applied only by the owner", () => {
  it("proposes, previews, and applies through /ops with origin proposal only on Apply", async () => {
    serve(doc(1));
    const rename: DesignOperation = { type: "document.rename", title: "Launch v2" };
    const preview = applyOps(server, [rename]).document;
    const proposal: DesignProposal = {
      ok: true,
      base_revision: 1,
      summary: "A bolder title.",
      assumptions: ["The audience reads English"],
      operations: [rename],
      checks: ["Title contrast is AA"],
      questions: [],
      preview,
      error: null,
      attempts: 1,
      usd: 0.001,
    };
    vi.mocked(proposeDesign).mockResolvedValue(proposal);
    const { user } = render(null, "A launch page");
    await screen.findByRole("application");
    await user.click(screen.getByRole("tab", { name: "Assistant" }));
    expect(screen.getByLabelText("What should this design be?")).toHaveValue("A launch page");

    await user.click(screen.getByRole("button", { name: "Propose" }));
    expect(await screen.findByText("A bolder title.")).toBeInTheDocument();
    expect(screen.getByText("The audience reads English")).toBeInTheDocument();
    expect(screen.getByText("Title contrast is AA")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Preview of Launch v2" })).toBeInTheDocument();
    expect(proposeDesign).toHaveBeenCalledWith("doc_1", "A launch page");
    // Seen, not applied.
    expect(applyDesignOps).not.toHaveBeenCalled();
    expect(server.title).toBe("Launch page");

    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(applyDesignOps).toHaveBeenCalledWith("doc_1", 1, [rename], "proposal"));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Launch v2" })).toBeInTheDocument());
  });
});

describe("versions and export ask first", () => {
  it("restores a version only after the confirm step, and the restore is one step to undo", async () => {
    serve(doc(2));
    vi.mocked(listDesignVersions).mockResolvedValue([
      { revision: 2, title: "Launch page", updated_at: "2026-10-18T10:00:00Z" },
      { revision: 1, title: "Launch page", updated_at: "2026-10-18T09:00:00Z" },
    ]);
    vi.mocked(restoreDesignVersion).mockImplementation(async (_id, revision) => {
      server = { ...server, revision: server.revision + 1, title: `Restored ${revision}` };
      return structuredClone(server);
    });
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Versions" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));
    expect(await screen.findByText("Restore revision 1?")).toBeInTheDocument();
    expect(restoreDesignVersion).not.toHaveBeenCalled();

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(restoreDesignVersion).toHaveBeenCalledWith("doc_1", 1, 2));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Undo restores what was there before the restore.
    const canvas = screen.getByRole("application");
    act(() => canvas.focus());
    await user.keyboard("{Control>}z{/Control}");
    await waitFor(() => expect(restoreDesignVersion).toHaveBeenLastCalledWith("doc_1", 2, 3));
  });

  it("exports only after the confirm step, as a file", async () => {
    serve(doc(1));
    vi.mocked(exportDesign).mockResolvedValue({ blob: new Blob(["<html></html>"]), filename: "design-doc_1.html" });
    const create = vi.fn(() => "blob:design");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Export" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("This writes the design to a file that leaves the app.")).toBeInTheDocument();
    expect(exportDesign).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Export file" }));
    await waitFor(() => expect(exportDesign).toHaveBeenCalledWith("doc_1", "html"));
    await waitFor(() => expect(click).toHaveBeenCalled());
    click.mockRestore();
  });

  it("offers PDF, PNG and PPTX, and shows the server's sentence when a renderer is missing", async () => {
    serve(doc(1));
    const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    vi.mocked(exportDesign).mockRejectedValue(
      new ApiError("PDF export needs Chromium: run `playwright install chromium` once.", 503),
    );
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Export" }));
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getByLabelText("Format");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["HTML page", "PDF document", "PNG image", "PowerPoint (PPTX)"]);
    await user.selectOptions(select, "pdf");
    await user.click(within(dialog).getByRole("button", { name: "Export file" }));
    await waitFor(() => expect(exportDesign).toHaveBeenCalledWith("doc_1", "pdf"));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("playwright install chromium");
  });
});

describe("the agent's edits", () => {
  it("refetches on a design frame and makes the agent's change one step to undo", async () => {
    serve(doc(1));
    vi.mocked(restoreDesignVersion).mockImplementation(async () => {
      server = { ...doc(1), revision: server.revision + 1 };
      return structuredClone(server);
    });
    const { user, view } = render();
    await screen.findByRole("application");

    // The agent saved revision 2 on the server.
    server = { ...server, title: "Agent title", revision: 2 };
    view.rerender(
      <ToastProvider><DesignPane target={{ id: "doc_1", key: 1 }} agentEdit={{ documentId: "doc_1", revision: 2, key: 1 }} autosaveMs={0} onOpen={vi.fn()} onClose={vi.fn()} /></ToastProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Agent title" })).toBeInTheDocument();

    const canvas = screen.getByRole("application");
    act(() => canvas.focus());
    await user.keyboard("{Control>}z{/Control}");
    await waitFor(() => expect(restoreDesignVersion).toHaveBeenCalledWith("doc_1", 1, 2));
    expect(await screen.findByRole("heading", { name: "Launch page" })).toBeInTheDocument();
  });

  it("ignores a frame about another design", async () => {
    serve(doc(1));
    const { view } = render();
    await screen.findByRole("application");
    const calls = vi.mocked(getDesign).mock.calls.length;
    view.rerender(
      <ToastProvider><DesignPane target={{ id: "doc_1", key: 1 }} agentEdit={{ documentId: "other", revision: 9, key: 1 }} autosaveMs={0} onOpen={vi.fn()} onClose={vi.fn()} /></ToastProvider>,
    );
    await act(async () => {});
    expect(vi.mocked(getDesign).mock.calls.length).toBe(calls);
  });
});

describe("the design panel", () => {
  it("edits a colour token as one tokens.replace, keeping the rest of the system", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("tab", { name: "Tokens" }));
    const field = screen.getByLabelText("primary");
    const next = channelsToHex(200, 40, 40);
    await user.clear(field);
    await user.type(field, `${next}{Enter}`);
    await waitFor(() => expect(applyDesignOps).toHaveBeenCalled());
    const [op] = vi.mocked(applyDesignOps).mock.calls[0][2] as DesignOperation[];
    expect(op).toMatchObject({ type: "tokens.replace" });
    expect(op.type === "tokens.replace" && op.tokens.colors).toEqual({ ...sampleDocument().tokens.colors, primary: next });
  });

  it("checks against the tokens and jumps to the element a finding names", async () => {
    serve(doc(1));
    vi.mocked(checkDesign).mockResolvedValue({
      revision: 1,
      findings: [{ kind: "low_contrast", node_id: "title", artboard_id: "board_1", message: "contrast 2.1:1" }],
    });
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("tab", { name: "Tokens" }));
    await user.click(screen.getByRole("button", { name: "Check against tokens" }));
    expect(await screen.findByText("Low contrast:")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show element" }));
    await waitFor(() =>
      expect(screen.getAllByRole("status").some((s) => s.textContent?.includes("Selected: Title"))).toBe(true),
    );
  });

  it("refines only what is selected", async () => {
    serve(doc(1));
    vi.mocked(refineDesign).mockResolvedValue({
      ok: true, base_revision: 1, summary: "Bigger.", assumptions: [], operations: [], checks: [], questions: [],
      preview: null, error: null, attempts: 1, usd: null,
    });
    const { user } = render();
    const canvas = await screen.findByRole("application");
    act(() => canvas.focus());
    await user.keyboard("n");
    await user.click(screen.getByRole("tab", { name: "Assistant" }));
    await user.type(screen.getByLabelText(/What should change/), "make it bigger");
    await user.click(screen.getByRole("button", { name: "Refine selection" }));
    await waitFor(() => expect(refineDesign).toHaveBeenCalledWith("doc_1", "make it bigger", ["title"]));
    expect(await screen.findByText("Bigger.")).toBeInTheDocument();
  });

  it("adds a comment to the selected element, saved like any edit", async () => {
    serve(doc(1));
    const { user } = render();
    const canvas = await screen.findByRole("application");
    act(() => canvas.focus());
    await user.keyboard("n");
    await user.type(screen.getByLabelText("Comment on Title"), "Shorter, please");
    await user.click(screen.getByRole("button", { name: "Add comment" }));
    await waitFor(() => expect(applyDesignOps).toHaveBeenCalled());
    expect(vi.mocked(applyDesignOps).mock.calls[0][2]).toEqual([
      expect.objectContaining({ type: "comment.add", comment: expect.objectContaining({ nodeId: "title", text: "Shorter, please", artboardId: "board_1" }) }),
    ]);
  });
});

describe("the title menu", () => {
  it("is the heading, and it holds rename, duplicate and export", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    const heading = screen.getByRole("heading", { name: "Launch page" });
    await user.click(within(heading).getByRole("button", { name: "Launch page" }));
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Duplicate design" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Export" })).toBeInTheDocument();
  });

  it("renames through a dialog and saves the new title", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Launch page" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));

    const dialog = await screen.findByRole("dialog", { name: "Rename design" });
    const field = within(dialog).getByLabelText("Title");
    await user.clear(field);
    await user.type(field, "Launch v3");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(applyDesignOps).toHaveBeenCalledWith("doc_1", 1, [{ type: "document.rename", title: "Launch v3" }]));
    expect(await screen.findByRole("heading", { name: "Launch v3" })).toBeInTheDocument();
  });

  it("duplicates into a new design and opens the copy", async () => {
    serve(doc(1));
    vi.mocked(createDesign).mockResolvedValue({ ...doc(1), id: "doc_2" });
    const { user, onOpen } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Launch page" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate design" }));

    await waitFor(() => expect(onOpen).toHaveBeenCalledWith("doc_2"));
    expect(applyDesignOps).toHaveBeenCalledWith("doc_2", 1, expect.any(Array));
    expect(deleteDesign).not.toHaveBeenCalled();
  });

  it("removes the empty copy and says so when its content cannot be written", async () => {
    serve(doc(1));
    vi.mocked(createDesign).mockResolvedValue({ ...doc(1), id: "doc_2" });
    vi.mocked(deleteDesign).mockResolvedValue({ deleted: true });
    vi.mocked(applyDesignOps).mockRejectedValueOnce(new ApiError("refused", 500));
    const { user, onOpen } = render();
    await screen.findByRole("application");
    await user.click(screen.getByRole("button", { name: "Launch page" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate design" }));

    await waitFor(() => expect(deleteDesign).toHaveBeenCalledWith("doc_2"));
    expect(onOpen).not.toHaveBeenCalled();
    expect(await screen.findByText("The copy could not be made. Nothing was changed.")).toBeInTheDocument();
  });
});

describe("sharing a local link", () => {
  it("copies a link that names this design, and the link reads back to it", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await user.click(screen.getByRole("button", { name: "Share local link" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(designIdFromSearch(new URL(String(writeText.mock.calls[0][0])).search)).toBe("doc_1");
    expect(await screen.findByText("Link copied")).toBeInTheDocument();
    writeText.mockRestore();
  });

  it("shows the link in a field to copy by hand when the clipboard refuses", async () => {
    serve(doc(1));
    const { user } = render();
    await screen.findByRole("application");
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    await user.click(screen.getByRole("button", { name: "Share local link" }));

    const dialog = await screen.findByRole("dialog", { name: "Share local link" });
    expect((within(dialog).getByLabelText("Link") as HTMLInputElement).value).toContain("design=doc_1");
    writeText.mockRestore();
  });
});
