import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DesignWorkspace } from "@/components/design/DesignWorkspace";
import { DesignPreview, staticPreviewHtml } from "@/components/design/DesignPreview";
import { DesignPresentation } from "@/components/design/DesignPresentation";
import { DesignInspector } from "@/components/design/DesignInspector";
import { DESIGN_SHORTCUTS } from "@/lib/design/shortcuts";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import type { Viewport } from "@/lib/design/geometry";
import { emptyDocument, sampleDocument } from "@/test/design-fixture";
import { renderWithProviders } from "@/test/utils";

/** The owner the workspace expects: keeps the applied document, as the Code viewer will. */
function setup(initial: DesignDocument = sampleDocument()) {
  const seen: { doc: DesignDocument; ops: DesignOperation[][] } = { doc: initial, ops: [] };
  let updateSelectionRequest: (request: { ids: string[]; key: number } | undefined) => void = () => {};
  function Owner() {
    const [doc, setDoc] = useState(initial);
    const [selectionRequest, setSelectionRequest] = useState<{ ids: string[]; key: number } | undefined>();
    updateSelectionRequest = setSelectionRequest;
    return (
      <DesignWorkspace
        document={doc}
        author="Bruno"
        select={selectionRequest}
        onOps={(ops, result) => {
          seen.doc = result.document;
          seen.ops.push(ops);
          setDoc(result.document);
        }}
      />
    );
  }
  const user = userEvent.setup();
  const view = renderWithProviders(<Owner />);
  return { user, seen, view, requestSelection: updateSelectionRequest };
}

describe("design index preview", () => {
  it("renders partial HTML as inert content with scripts and model metadata removed", () => {
    const html = staticPreviewHtml('<html><head></head><body><script>alert(1)</script><script type=\"text/x-chimera\">secret</script><main>safe</main></body></html>');
    expect(html).toContain("<main>safe</main>");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("text/x-chimera");
    expect(html).toContain("default-src 'none'");
  });

  it("renders linked artboards as named, navigable thumbnails", async () => {
    const doc = sampleDocument();
    doc.pages.push({
      id: "index", name: "Índice", artboards: [
        { id: "thumb", name: "Desktop", width: 320, height: 220, background: doc.pages[0].artboards[0].background, nodeIds: [], thumbnailOf: "board_1" },
      ],
    });
    const navigate = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<DesignPreview doc={{ ...doc, pages: [doc.pages[1], doc.pages[0]] }} onNavigate={navigate} />);
    await user.click(screen.getByRole("button", { name: "Open Desktop" }));
    expect(navigate).toHaveBeenCalledWith("board_1");
    expect(screen.getByRole("button", { name: "Open Desktop" }).querySelector("svg[role='img']")).not.toBeNull();
  });
});

function canvas(): HTMLElement {
  return screen.getByRole("application");
}

describe("HTML artboard rendering", () => {
  function htmlDocument(): DesignDocument {
    const doc = sampleDocument();
    doc.pages[0].artboards[0].html = "<!doctype html><main>Rendered page</main>";
    doc.pages[0].artboards[0].mode = "fill";
    return doc;
  }

  function expectSandboxedFrame(frame: HTMLIFrameElement | null, editing = true): asserts frame is HTMLIFrameElement {
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("sandbox")).toBe(editing ? "allow-scripts" : "");
    if (editing) expect(frame?.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame?.getAttribute("loading")).toBe("lazy");
    expect(frame?.getAttribute("srcdoc")).toContain(editing ? "script-src 'sha256-" : "script-src 'none'");
  }

  it("renders HTML boards in an inert sandbox and keeps node boards as SVG", () => {
    const { view } = setup(htmlDocument());
    expectSandboxedFrame(view.container.querySelector("[data-artboard-id='board_1'] iframe"));
    expect(view.container.querySelector("[data-artboard-id='board_1'] [data-node-id='title']")).toBeNull();
    expect(view.container.querySelector("[data-artboard-id='board_2'] iframe")).toBeNull();
    expect(view.container.querySelector("[data-artboard-id='board_2'] [data-node-id='title']")).toBeNull();
    expect(view.container.querySelector("[data-artboard-id='board_1'] foreignObject")).toBeInTheDocument();
  });

  it("renders HTML in proposal previews", () => {
    const doc = htmlDocument();
    doc.pages[0].artboards[0].html = null;
    doc.pages[0].artboards[0].nodeIds = [];
    doc.pages[0].artboards[1].html = "<main>Rendered preview</main>";
    renderWithProviders(<DesignPreview doc={doc} />);
    expectSandboxedFrame(document.querySelector("iframe"), false);
  });

  it("renders HTML in presentation", () => {
    const doc = htmlDocument();
    const viewport: Viewport = { x: 40, y: 40, zoom: 0.5 };
    renderWithProviders(<DesignPresentation doc={doc} pageId="page_1" boards={doc.pages[0].artboards} index={0} onIndex={vi.fn()} onClose={vi.fn()} viewport={viewport} />);
    expectSandboxedFrame(document.querySelector("iframe"), false);
  });

  it("observes the canvas width only when a fluid HTML board is presented", () => {
    const observed: Element[] = [];
    const original = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      observe(target: Element): void {
        observed.push(target);
      }
      unobserve(): void {}
      disconnect(): void {}
    } as unknown as typeof ResizeObserver;
    const observesCanvas = () => observed.some((target) => target.tagName.toLowerCase() === "svg");
    const viewport: Viewport = { x: 40, y: 40, zoom: 0.5 };
    try {
      const doc = htmlDocument();
      const presented = renderWithProviders(<DesignPresentation doc={doc} pageId="page_1" boards={doc.pages[0].artboards} index={0} onIndex={vi.fn()} onClose={vi.fn()} viewport={viewport} />);
      expect(observesCanvas()).toBe(true);
      presented.unmount();

      observed.length = 0;
      const plain = sampleDocument();
      renderWithProviders(<DesignPresentation doc={plain} pageId="page_1" boards={plain.pages[0].artboards} index={0} onIndex={vi.fn()} onClose={vi.fn()} viewport={viewport} />);
      expect(observesCanvas()).toBe(false);
    } finally {
      globalThis.ResizeObserver = original;
    }
  });

  it("shows HTML artboard mode and the editing limitation in the inspector", () => {
    const doc = htmlDocument();
    renderWithProviders(<DesignInspector doc={doc} pageId="page_1" selection={[]} dispatch={() => false} />);
    expect(screen.getByText("Mode: fill")).toBeInTheDocument();
    expect(screen.getByText("Piece-by-piece editing is not available for HTML artboards yet.")).toBeInTheDocument();
  });
});

async function status(text: string) {
  const region = screen.getByRole("status");
  await vi.waitFor(() => expect(region).toHaveTextContent(text));
}

describe("the design canvas, by keyboard alone", () => {
  it("selects, moves, deletes and undoes", async () => {
    const { user, seen } = setup();
    expect(canvas()).toHaveAttribute("tabindex", "0");
    act(() => canvas().focus());

    await user.keyboard("n");
    await status("Selected: Title");

    await user.keyboard("{ArrowRight}");
    await user.keyboard("{Shift>}{ArrowDown}{/Shift}");
    expect(seen.doc.nodes.title.frame).toMatchObject({ x: 101, y: 110 });

    await user.keyboard("{Delete}");
    expect(seen.doc.nodes.title).toBeUndefined();
    await status("Deleted");

    await user.keyboard("{Control>}z{/Control}");
    expect(seen.doc.nodes.title).toBeDefined();
    expect(seen.doc.pages[0].artboards[0].nodeIds[0]).toBe("title");
    await status("Undone: Deleted");

    // The two nudges were one step: a single undo takes the element home.
    await user.keyboard("{Control>}z{/Control}");
    expect(seen.doc.nodes.title.frame).toMatchObject({ x: 100, y: 100 });

    await user.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    expect(seen.doc.nodes.title.frame).toMatchObject({ x: 101, y: 110 });

    await user.keyboard("n");
    await status("Selected: Title");
    await user.keyboard("{Escape}");
    await status("Nothing selected");
  });

  it("edits text in place with Enter, and commits on Escape", async () => {
    const { user, seen } = setup();
    act(() => canvas().focus());
    await user.keyboard("n{Enter}");
    const editor = screen.getByRole("textbox", { name: "Edit text" });
    expect(editor).toHaveValue("Hello");
    await user.clear(editor);
    await user.type(editor, "World");
    await user.keyboard("{Escape}");
    expect(seen.doc.nodes.title).toMatchObject({ text: "World" });
    expect(screen.queryByRole("textbox", { name: "Edit text" })).toBeNull();
    expect(canvas().querySelector("[data-node-id='title']")).toHaveTextContent("World");
    expect(canvas()).toHaveFocus();
  });

  it("keeps Enter and Shift+Enter as line breaks and commits with Mod+Enter", async () => {
    const { user, seen } = setup();
    act(() => canvas().focus());
    await user.keyboard("n{Enter}");
    const editor = screen.getByRole("textbox", { name: "Edit text" });
    await user.clear(editor);
    await user.type(editor, "First line");
    await user.keyboard("{Enter}");
    await user.type(editor, "Second line");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(editor, "Third line");
    expect(editor).toHaveValue("First line\nSecond line\nThird line");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(seen.doc.nodes.title).toMatchObject({ text: "First line\nSecond line\nThird line" });
    expect(screen.queryByRole("textbox", { name: "Edit text" })).toBeNull();
  });

  it("opens the same editor on double-click", async () => {
    const { user } = setup();
    act(() => canvas().focus());
    await user.keyboard("n");
    fireEvent.doubleClick(canvas());
    expect(screen.getByRole("textbox", { name: "Edit text" })).toBeInTheDocument();
  });

  it("duplicates with Mod+D and selects the copy", async () => {
    const { user, seen } = setup();
    act(() => canvas().focus());
    await user.keyboard("nn{Control>}d{/Control}");
    const ids = seen.doc.pages[0].artboards[0].nodeIds;
    expect(ids).toHaveLength(4);
    expect(seen.doc.nodes[ids[3]].frame).toMatchObject({ x: 110, y: 310 });
    await status("Duplicated");
  });

  it("navigates between artboards and opens the index from a thumbnail", async () => {
    const initial = sampleDocument();
    initial.pages.push({ id: "index", name: "Índice", artboards: [
      { id: "thumb1", name: "Desktop", width: 320, height: 220, background: initial.pages[0].artboards[0].background, nodeIds: [], thumbnailOf: "board_1" },
      { id: "thumb2", name: "Phone", width: 320, height: 220, background: initial.pages[0].artboards[0].background, nodeIds: [], thumbnailOf: "board_2" },
    ] });
    const { user } = setup(initial);
    await user.click(screen.getByRole("button", { name: "Phone" }));
    expect(canvas().querySelector("[data-artboard-id='board_2']")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Phone" })).toHaveAttribute("aria-current", "page");

    await user.selectOptions(screen.getByRole("combobox", { name: "Page" }), "index");
    expect(canvas().querySelector("[data-artboard-id='thumb1']")).toBeInTheDocument();
    fireEvent.pointerDown(canvas().querySelector("[data-thumbnail-of='board_1']")!, { pointerId: 2, button: 0, clientX: 50, clientY: 50 });
    expect(screen.getByRole("button", { name: "Desktop" })).toHaveAttribute("aria-current", "page");
  });

  it("shows an index page under its translated title, however it is named", async () => {
    const initial = sampleDocument();
    initial.pages.push({ id: "index", name: "Index", isIndex: true, artboards: [
      { id: "thumb1", name: "Desktop", width: 320, height: 220, background: initial.pages[0].artboards[0].background, nodeIds: [], thumbnailOf: "board_1" },
      { id: "thumb2", name: "Phone", width: 320, height: 220, background: initial.pages[0].artboards[0].background, nodeIds: [], thumbnailOf: "board_2" },
    ] });
    const { user } = setup(initial);
    expect(screen.getByRole("option", { name: "Design index" })).toHaveValue("index");
    await user.click(screen.getByRole("button", { name: "Open index" }));
    expect(canvas().querySelector("[data-artboard-id='thumb1']")).toBeInTheDocument();
  });

  it("selects a linked element on a plain click, and follows its link only with Ctrl or Meta", async () => {
    const initial = sampleDocument();
    initial.nodes.title.linkTo = "board_2";
    const { user } = setup(initial);
    const title = canvas().querySelector("[data-node-id='title']")!;

    await user.pointer({ keys: "[MouseLeft]", target: title });
    expect(screen.getByRole("button", { name: "Phone" })).not.toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Desktop" })).toHaveAttribute("aria-current", "page");

    await user.keyboard("{Control>}");
    await user.pointer({ keys: "[MouseLeft]", target: title });
    await user.keyboard("{/Control}");
    expect(screen.getByRole("button", { name: "Phone" })).toHaveAttribute("aria-current", "page");
  });

  it("guides the comment tool and adds an accessible marker on an element", async () => {
    const { user, seen } = setup();
    await user.click(screen.getByRole("button", { name: "More" }));
    await user.click(screen.getByRole("menuitem", { name: "Comment" }));
    expect(screen.getByText("Select an element to comment on")).toBeVisible();
    fireEvent.pointerDown(canvas(), { pointerId: 1, button: 0, clientX: 150, clientY: 100 });
    const editor = screen.getByRole("textbox", { name: "New comment" });
    await user.type(editor, "Increase contrast");
    await user.keyboard("{Enter}");
    expect(seen.doc.comments).toHaveLength(1);
    expect(seen.doc.comments[0]).toMatchObject({ nodeId: "title", text: "Increase contrast" });
    expect(screen.getByRole("button", { name: "Comment 1: Increase contrast" })).toBeInTheDocument();
  });

  it("opens, resolves and reopens a comment thread", async () => {
    const doc = sampleDocument();
    doc.comments = [{ id: "comment_1", artboardId: "board_1", nodeId: "title", x: 500, y: 160, text: "Review this", author: "Bruno", resolved: false, createdAt: "2026-10-17T00:00:00.000Z" }];
    const { user, seen } = setup(doc);
    await user.click(screen.getByRole("button", { name: "Comment 1: Review this" }));
    await user.click(screen.getAllByRole("button", { name: "Mark resolved" })[0]);
    expect(seen.doc.comments[0].resolved).toBe(true);
    expect(screen.getAllByRole("button", { name: "Reopen" })[0]).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Reopen" })[0]);
    expect(seen.doc.comments[0].resolved).toBe(false);
  });

  it("allows agent selection after jumping to a comment", async () => {
    const doc = sampleDocument();
    doc.comments = [{ id: "comment_1", artboardId: "board_1", nodeId: "title", x: 500, y: 160, text: "Review this", author: "Bruno", resolved: false, createdAt: "2026-10-17T00:00:00.000Z" }];
    const { user, requestSelection } = setup(doc);
    await user.click(screen.getByRole("button", { name: "Comment 1: Review this" }));
    act(() => requestSelection({ ids: ["box"], key: 1 }));
    await status("Selected: Box");
  });

  it("keeps applying later parent selections after any number of comment jumps", async () => {
    const doc = sampleDocument();
    doc.comments = [{ id: "comment_1", artboardId: "board_1", nodeId: "title", x: 500, y: 160, text: "Review this", author: "Bruno", resolved: false, createdAt: "2026-10-17T00:00:00.000Z" }];
    const { user, requestSelection } = setup(doc);
    const jumpFromPanel = async () => {
      await user.click(screen.getByRole("tab", { name: "Comments" }));
      await user.click(within(screen.getByRole("region", { name: "Comments" })).getByRole("button", { name: /Review this/ }));
    };
    await jumpFromPanel();
    await status("Selected: Title");
    act(() => requestSelection({ ids: ["box"], key: 7 }));
    await status("Selected: Box");
    await jumpFromPanel();
    await status("Selected: Title");
    act(() => requestSelection({ ids: ["box"], key: 8 }));
    await status("Selected: Box");
  });

  it("lists comments in the side panel and jumps to their element", async () => {
    const doc = sampleDocument();
    doc.comments = [{ id: "comment_1", artboardId: "board_1", nodeId: "title", x: 500, y: 160, text: "Review this", author: "Bruno", resolved: false, createdAt: "2026-10-17T00:00:00.000Z" }];
    const { user } = setup(doc);
    await user.click(screen.getByRole("tab", { name: "Comments" }));
    const row = within(screen.getByRole("region", { name: "Comments" })).getByRole("button", { name: /Review this/ });
    expect(row).toBeInTheDocument();
    await user.click(row);
    await vi.waitFor(() => expect(screen.getByRole("tab", { name: "Properties" })).toHaveAttribute("aria-selected", "true"));
    expect(canvas().querySelector("[data-node-id='title']")).toBeInTheDocument();
  });

  it("moves an element by dragging, as one step to undo", async () => {
    const { user, seen } = setup();
    // Viewport starts at (40, 40) × 0.5, so the box (100, 300) is at screen (90, 190).
    fireEvent.pointerDown(canvas(), { pointerId: 1, button: 0, clientX: 140, clientY: 215 });
    fireEvent.pointerMove(canvas(), { pointerId: 1, clientX: 190, clientY: 215 });
    fireEvent.pointerMove(canvas(), { pointerId: 1, clientX: 240, clientY: 215 });
    fireEvent.pointerUp(canvas(), { pointerId: 1, clientX: 240, clientY: 215 });
    expect(seen.doc.nodes.box.frame.x).toBe(300);
    expect(seen.ops).toHaveLength(1);
    act(() => canvas().focus());
    await user.keyboard("{Control>}z{/Control}");
    expect(seen.doc.nodes.box.frame.x).toBe(100);
  });
});

describe("colours are data, and only valid data is painted", () => {
  it("never puts an invalid colour into the DOM", () => {
    const doc = sampleDocument();
    const evil = "red;stroke:url(javascript:alert(1))";
    doc.pages[0].artboards[0].background = "blue";
    doc.nodes.box = { ...doc.nodes.box, style: { fill: evil, stroke: "expression(alert(1))" } };
    doc.tokens.colors.text = "url(#steal)";
    const { view } = setup(doc);
    const html = view.container.innerHTML;
    expect(html).not.toContain("javascript");
    expect(html).not.toContain("expression(");
    expect(html).not.toContain("url(#steal)");
    expect(view.container.querySelector("[data-artboard-id='board_1'] > rect")?.getAttribute("fill")).toBeNull();
    // The valid one still paints.
    expect(view.container.querySelector("[data-artboard-id='board_2'] > rect")?.getAttribute("fill")).toBe(doc.pages[0].artboards[1].background);
  });
});

describe("the empty canvas", () => {
  it("offers an explicit first-artboard action with the size chosen first", async () => {
    const { user, seen } = setup(emptyDocument());
    expect(screen.queryByRole("application")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Phone/ }));
    expect(screen.getByRole("button", { name: /Phone/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Add first artboard" }));
    expect(seen.ops[0].map((op) => op.type)).toEqual(["page.add", "artboard.add"]);
    expect(seen.doc.pages[0].artboards[0]).toMatchObject({ width: 390, height: 844, name: "Artboard 1" });
    expect(canvas()).toBeInTheDocument();
  });
});

describe("toolbar and shortcuts sheet", () => {
  it("labels every button and marks the current tool as pressed", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    const buttons = within(toolbar).getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(15);
    for (const b of buttons) expect(b).toHaveAccessibleName();
    expect(within(toolbar).getByRole("button", { name: "Select" })).toHaveAttribute("aria-pressed", "true");
    act(() => canvas().focus());
    await user.keyboard("r");
    expect(within(toolbar).getByRole("button", { name: "Rectangle" })).toHaveAttribute("aria-pressed", "true");
    expect(within(toolbar).getByRole("button", { name: "Undo" })).toHaveAttribute("aria-keyshortcuts", "Ctrl+Z");
    expect(within(toolbar).getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("exposes pan, frame, note, shape and pen variants in toolbar menus", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    expect(within(toolbar).getByRole("button", { name: "Pan" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "Frame" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("button", { name: "Note" })).toBeInTheDocument();
    await user.click(within(toolbar).getByLabelText("More shapes"));
    await user.click(screen.getByRole("menuitem", { name: "Ellipse" }));
    expect(within(toolbar).getByRole("button", { name: "Rectangle" })).toHaveAttribute("aria-pressed", "false");
    await user.click(within(toolbar).getByLabelText("Drawing options"));
    await user.click(screen.getByRole("menuitem", { name: "Marker" }));
    expect(within(toolbar).getByRole("button", { name: "Draw" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps page selection visible and lets the zoom menu set a fixed scale", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    expect(within(toolbar).getByRole("combobox", { name: "Page" })).toBeInTheDocument();
    await user.click(within(toolbar).getByLabelText("Zoom: 50%"));
    await user.click(screen.getByRole("menuitem", { name: "200%" }));
    expect(within(toolbar).getByText("200%", { selector: "span" })).toHaveTextContent("200%");
  });

  it("enters presentation and exits it on Escape", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Present" }));
    expect(screen.getByRole("dialog", { name: "Presentation" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Presentation" })).toBeNull();
  });

  it("shows a device-width preview and can reset it", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    await user.click(within(toolbar).getByLabelText("Device preview: Off"));
    await user.click(screen.getByRole("menuitem", { name: "375 px" }));
    expect(canvas().getAttribute("style")).toContain("375px");
  });

  it("toggles comment visibility without deleting comments", async () => {
    const doc = sampleDocument();
    doc.comments = [{ id: "comment-1", artboardId: "board_1", x: 20, y: 20, text: "Check this", author: "Bruno", resolved: false, createdAt: "2026-10-01T00:00:00Z" }];
    const { user, seen } = setup(doc);
    const marker = "Comment 1: Check this";
    expect(screen.getByRole("button", { name: marker })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide comments" }));
    expect(screen.queryByRole("button", { name: marker })).not.toBeInTheDocument();
    expect(seen.doc.comments).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Show comments" }));
    expect(screen.getByRole("button", { name: marker })).toBeInTheDocument();
  });

  it("lists every shortcut when ? is pressed on the canvas", async () => {
    const { user } = setup();
    act(() => canvas().focus());
    await user.keyboard("?");
    const dialog = await screen.findByRole("dialog", { name: "Canvas shortcuts" });
    const rows = within(dialog).getAllByRole("row");
    expect(rows).toHaveLength(DESIGN_SHORTCUTS.length + 1);
    expect(within(dialog).getByText("Select the next element")).toBeInTheDocument();
    expect(within(dialog).getByText("Duplicate")).toBeInTheDocument();
  });
});

describe("narrow containers", () => {
  it("folds the inspector into a toggle", async () => {
    const original = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(private readonly cb: ResizeObserverCallback) {}
      observe(): void {
        this.cb([{ contentRect: { width: 500 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve(): void {}
      disconnect(): void {}
    };
    try {
      const { user } = setup();
      const toggle = await screen.findByRole("button", { name: "Show or hide properties" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("region", { name: "Properties" })).toBeNull();
      await user.click(toggle);
      expect(screen.getByRole("region", { name: "Properties" })).toBeInTheDocument();
    } finally {
      globalThis.ResizeObserver = original;
    }
  });

  it("shows the inspector beside the canvas when there is room", () => {
    setup();
    expect(screen.getByRole("region", { name: "Properties" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show or hide properties" })).toBeNull();
  });
});

describe("menus, zoom, shortcuts and presentation", () => {
  it("runs the pan, frame and note tools from their shortcuts, and shows the keys", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    act(() => canvas().focus());

    await user.keyboard("h");
    expect(within(toolbar).getByRole("button", { name: "Pan" })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("f");
    expect(within(toolbar).getByRole("button", { name: "Frame" })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("s");
    expect(within(toolbar).getByRole("button", { name: "Note" })).toHaveAttribute("aria-pressed", "true");
    expect(within(toolbar).getByRole("button", { name: "Note" })).toHaveAttribute("aria-keyshortcuts", "S");
  });

  it("zooms in, out and to fit from the zoom menu", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    expect(within(toolbar).getByLabelText("Zoom: 50%")).toBeInTheDocument();

    await user.click(within(toolbar).getByLabelText("Zoom: 50%"));
    await user.click(screen.getByRole("menuitem", { name: "Zoom in" }));
    expect(within(toolbar).queryByLabelText("Zoom: 50%")).toBeNull();

    await user.click(within(toolbar).getByRole("button", { name: /^Zoom: / }));
    await user.click(screen.getByRole("menuitem", { name: "Zoom out" }));
    expect(within(toolbar).getByLabelText("Zoom: 50%")).toBeInTheDocument();

    await user.click(within(toolbar).getByLabelText("Zoom: 50%"));
    const fit = screen.getByRole("menuitem", { name: "Fit the page in view" });
    expect(fit).toHaveAttribute("title", "Zoom 50% — fit the page in view");
    await user.click(fit);
    expect(within(toolbar).queryByLabelText("Zoom: 50%")).toBeNull();
  });

  it("zooms with the standalone buttons too", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    await user.click(within(toolbar).getByRole("button", { name: "Zoom in" }));
    expect(within(toolbar).queryByLabelText("Zoom: 50%")).toBeNull();
  });

  it("opens a menu with the keyboard, moves through it, and returns focus on Escape", async () => {
    const { user } = setup();
    const toolbar = screen.getByRole("toolbar", { name: "Design tools" });
    const trigger = within(toolbar).getByLabelText("Drawing options");
    trigger.focus();
    await user.keyboard("{Enter}");
    const pencil = screen.getByRole("menuitem", { name: "Pencil" });
    expect(pencil).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Marker" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("steps through the artboards of a presentation and returns focus when it closes", async () => {
    const { user } = setup();
    const present = screen.getByRole("button", { name: "Present" });
    await user.click(present);

    const dialog = screen.getByRole("dialog", { name: "Presentation" });
    expect(dialog).toHaveFocus();
    expect(within(dialog).getByText("1 of 2")).toBeInTheDocument();
    await user.keyboard("{ArrowRight}");
    expect(within(dialog).getByText("2 of 2")).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}");
    expect(within(dialog).getByText("1 of 2")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Presentation" })).toBeNull();
    expect(present).toHaveFocus();
  });
});
