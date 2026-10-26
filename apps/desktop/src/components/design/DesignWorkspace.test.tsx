import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DesignWorkspace } from "@/components/design/DesignWorkspace";
import { DESIGN_SHORTCUTS } from "@/lib/design/shortcuts";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import { emptyDocument, sampleDocument } from "@/test/design-fixture";
import { renderWithProviders } from "@/test/utils";

/** The owner the workspace expects: keeps the applied document, as the Code viewer will. */
function setup(initial: DesignDocument = sampleDocument()) {
  const seen: { doc: DesignDocument; ops: DesignOperation[][] } = { doc: initial, ops: [] };
  function Owner() {
    const [doc, setDoc] = useState(initial);
    return (
      <DesignWorkspace
        document={doc}
        author="Bruno"
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
  return { user, seen, view };
}

function canvas(): HTMLElement {
  return screen.getByRole("application");
}

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
    expect(canvas().textContent).toContain("Check this");
    await user.click(screen.getByRole("button", { name: "Hide comments" }));
    expect(canvas().textContent).not.toContain("Check this");
    expect(seen.doc.comments).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Show comments" }));
    expect(canvas().textContent).toContain("Check this");
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
