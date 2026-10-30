import { describe, expect, it } from "vitest";

import { applyOps, DesignOpError } from "@/lib/design/ops";
import { isHexColour, isIndexPage, type DesignDocument, type DesignOperation } from "@/lib/design/model";
import { BRAND, INK, sampleDocument } from "@/test/design-fixture";

const NOW = () => new Date("2026-10-17T12:00:00.000Z");

/** Apply, then apply the inverse: the document must come back exactly, revision aside. */
function roundTrip(doc: DesignDocument, ops: DesignOperation[]) {
  const forward = applyOps(doc, ops, { now: NOW });
  const back = applyOps(forward.document, forward.inverse, { now: NOW });
  const strip = (d: DesignDocument) => ({ ...d, revision: 0, updatedAt: "" });
  expect(strip(back.document)).toEqual(strip(doc));
  return forward;
}

function refused(doc: DesignDocument, ops: DesignOperation[]): string {
  try {
    applyOps(doc, ops);
  } catch (error) {
    expect(error).toBeInstanceOf(DesignOpError);
    return (error as DesignOpError).code;
  }
  throw new Error("expected the batch to be refused");
}

describe("applyOps round trips through its inverse", () => {
  const doc = sampleDocument();
  const cases: [string, DesignOperation[]][] = [
    ["document.rename", [{ type: "document.rename", title: "Renamed" }]],
    ["page.add", [{ type: "page.add", page: { id: "page_2", name: "Two", artboards: [] } }]],
    ["artboard.add", [{ type: "artboard.add", pageId: "page_1", artboard: { id: "board_3", name: "New", width: 100, height: 100, background: INK, nodeIds: [] } }]],
    ["artboard.update", [{ type: "artboard.update", pageId: "page_1", artboardId: "board_1", patch: { name: "Wide", width: 1600, background: INK } }]],
    ["artboard.delete", [{ type: "artboard.delete", pageId: "page_1", artboardId: "board_2" }]],
    ["node.add", [{ type: "node.add", pageId: "page_1", artboardId: "board_2", node: { id: "n2", type: "shape", shape: "path", name: "Scribble", frame: { x: 0, y: 0, width: 10, height: 10 }, points: [0, 0, 1, 1], visible: true } }]],
    ["node.update", [{ type: "node.update", nodeId: "title", patch: { text: "Changed", align: "center" } }]],
    ["node.update removing a field", [{ type: "node.update", nodeId: "title", patch: { fontToken: null } }]],
    ["node.move", [{ type: "node.move", nodeId: "box", frame: { x: 5, y: 6, width: 7, height: 8 } }]],
    ["node.delete", [{ type: "node.delete", nodeId: "title" }]],
    ["tokens.replace", [{ type: "tokens.replace", tokens: { colors: { only: INK }, typography: {}, spacing: {}, radii: {} } }]],
    ["comment.add", [{ type: "comment.add", comment: { id: "c1", artboardId: "board_1", x: 1, y: 2, text: "Bigger?", author: "Bruno", resolved: false, createdAt: "2026-10-17T00:00:00.000Z" } }]],
    ["several at once", [
      { type: "node.move", nodeId: "box", frame: { x: 1, y: 1, width: 50, height: 50 } },
      { type: "node.update", nodeId: "box", patch: { style: { fill: INK, opacity: 0.5 } } },
      { type: "node.delete", nodeId: "dot" },
    ]],
  ];
  it.each(cases)("%s", (_, ops) => {
    roundTrip(doc, ops);
  });

  it("puts a deleted element back in its z-order slot, not on top", () => {
    const { document } = roundTrip(doc, [{ type: "node.delete", nodeId: "title" }]);
    expect(document.pages[0].artboards[0].nodeIds).toEqual(["box", "dot"]);
    const back = applyOps(document, [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: doc.nodes.title, index: 0 }]);
    expect(back.document.pages[0].artboards[0].nodeIds).toEqual(["title", "box", "dot"]);
  });

  it("sets and edits HTML artboards with exact inverses", () => {
    const set = roundTrip(doc, [{ type: "artboard.set_html", pageId: "page_1", artboardId: "board_1", html: "<main>Launch</main>" }]);
    expect(set.document.pages[0].artboards[0].html).toBe("<main>Launch</main>");
    const edited = roundTrip(set.document, [{ type: "artboard.edit_html", pageId: "page_1", artboardId: "board_1", old: "Launch", new: "Start" }]);
    expect(edited.document.pages[0].artboards[0].html).toBe("<main>Start</main>");
  });

  it("styles and edits text in HTML with exact inverses", () => {
    const html = applyOps(doc, [{ type: "artboard.set_html", pageId: "page_1", artboardId: "board_1", html: "<main><p>Hello</p><p style='color: red'>Bye</p></main>" }]).document;
    const added = roundTrip(html, [{ type: "artboard.set_style", pageId: "page_1", artboardId: "board_1", index: 1, property: "padding", value: "4px" }]);
    expect(added.document.pages[0].artboards[0].html).toContain("<p style=\"padding: 4px\">Hello</p>");
    const changed = applyOps(html, [{ type: "artboard.set_style", pageId: "page_1", artboardId: "board_1", index: 2, property: "color", value: "blue" }]);
    expect(changed.document.pages[0].artboards[0].html).toContain("color: blue");
    const removed = applyOps(changed.document, [{ type: "artboard.set_style", pageId: "page_1", artboardId: "board_1", index: 2, property: "color", value: "" }]);
    expect(removed.document.pages[0].artboards[0].html).not.toContain("color:");
    const text = roundTrip(html, [{ type: "artboard.set_text", pageId: "page_1", artboardId: "board_1", index: 1, text: "<Start & go>" }]);
    expect(text.document.pages[0].artboards[0].html).toContain("&lt;Start &amp; go&gt;");
    expect(refused(html, [{ type: "artboard.set_text", pageId: "page_1", artboardId: "board_1", index: 0, text: "no" }])).toBe("htmlTextOnly");
  });

  it("refuses HTML edits with zero or multiple matches", () => {
    const html = applyOps(doc, [{ type: "artboard.set_html", pageId: "page_1", artboardId: "board_1", html: "x x" }]).document;
    expect(refused(html, [{ type: "artboard.edit_html", pageId: "page_1", artboardId: "board_1", old: "missing", new: "y" }])).toBe("htmlEditOccurrences");
    expect(refused(html, [{ type: "artboard.edit_html", pageId: "page_1", artboardId: "board_1", old: "x", new: "y" }])).toBe("htmlEditOccurrences");
  });

  it("resolves and deletes comments, each undoable", () => {
    const added = applyOps(doc, [{ type: "comment.add", comment: { id: "c1", artboardId: "board_1", x: 1, y: 2, text: "Hi", author: "B", resolved: false, createdAt: "x" } }]);
    roundTrip(added.document, [{ type: "comment.resolve", commentId: "c1", resolved: true }]);
    roundTrip(added.document, [{ type: "comment.delete", commentId: "c1" }]);
  });

  it("bumps the revision once per batch and leaves the input untouched", () => {
    const before = JSON.stringify(doc);
    const { document } = applyOps(doc, [{ type: "document.rename", title: "A" }, { type: "document.rename", title: "B" }], { now: NOW });
    expect(document.revision).toBe(doc.revision + 1);
    expect(document.updatedAt).toBe("2026-10-17T12:00:00.000Z");
    expect(JSON.stringify(doc)).toBe(before);
  });
});

describe("applyOps refuses what would make the document invalid", () => {
  const doc = sampleDocument();

  it("refuses a colour that is not a strict hex, wherever it appears", () => {
    expect(isHexColour(BRAND)).toBe(true);
    const evil = "red; background: url(javascript:alert(1))";
    expect(refused(doc, [{ type: "node.update", nodeId: "box", patch: { style: { fill: evil } } }])).toBe("colour");
    expect(refused(doc, [{ type: "artboard.update", pageId: "page_1", artboardId: "board_1", patch: { background: "blue" } }])).toBe("colour");
    expect(refused(doc, [{ type: "tokens.replace", tokens: { colors: { x: "url(#a)" }, typography: {}, spacing: {}, radii: {} } }])).toBe("colour");
    expect(refused(doc, [{ type: "artboard.add", pageId: "page_1", artboard: { id: "b9", name: "x", width: 1, height: 1, background: "#12345", nodeIds: [] } }])).toBe("colour");
  });

  it("is atomic: a refusal halfway leaves nothing applied", () => {
    const before = JSON.stringify(doc);
    refused(doc, [
      { type: "document.rename", title: "Applied first" },
      { type: "node.move", nodeId: "box", frame: { x: 0, y: 0, width: 0, height: 10 } },
    ]);
    expect(JSON.stringify(doc)).toBe(before);
  });

  it.each([
    ["an id with unsafe characters", [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: { ...sampleDocument().nodes.box, id: "../x" } }], "id"],
    ["a duplicate element", [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: sampleDocument().nodes.box }], "duplicateNode"],
    ["a zero-width frame", [{ type: "node.move", nodeId: "box", frame: { x: 0, y: 0, width: 0, height: 1 } }], "dimension"],
    ["a non-finite position", [{ type: "node.move", nodeId: "box", frame: { x: Number.NaN, y: 0, width: 1, height: 1 } }], "position"],
    ["changing an element's type", [{ type: "node.update", nodeId: "box", patch: { type: "text" } }], "patchField"],
    ["removing a required field", [{ type: "node.update", nodeId: "title", patch: { text: null } }], "patchRemovesRequired"],
    ["an artboard of another page", [{ type: "node.add", pageId: "page_x", artboardId: "board_1", node: { ...sampleDocument().nodes.box, id: "fresh" } }], "artboardNotFound"],
    ["deleting an artboard that still has elements", [{ type: "artboard.delete", pageId: "page_1", artboardId: "board_1" }], "artboardNotEmpty"],
    ["an odd number of path coordinates", [{ type: "node.update", nodeId: "box", patch: { points: [0, 1, 2] } }], "points"],
    ["an empty comment", [{ type: "comment.add", comment: { id: "c", artboardId: "board_1", x: 0, y: 0, text: "  ", author: "a", resolved: false, createdAt: "x" } }], "emptyComment"],
    ["an operation nobody defined", [{ type: "history.rewrite" }], "unknownOperation"],
  ] as unknown as [string, DesignOperation[], string][])("refuses %s", (_, ops, code) => {
    expect(refused(doc, ops)).toBe(code);
  });

  it("refuses edits to a locked element but lets it be unlocked", () => {
    const locked = applyOps(doc, [{ type: "node.update", nodeId: "box", patch: { locked: true } }]).document;
    expect(refused(locked, [{ type: "node.move", nodeId: "box", frame: { x: 0, y: 0, width: 1, height: 1 } }])).toBe("locked");
    expect(refused(locked, [{ type: "node.delete", nodeId: "box" }])).toBe("locked");
    expect(applyOps(locked, [{ type: "node.update", nodeId: "box", patch: { locked: false } }]).document.nodes.box.locked).toBe(false);
  });

  it("caps the batch size", () => {
    const many = Array.from({ length: 3 }, () => ({ type: "document.rename", title: "x" }) as DesignOperation);
    expect(() => applyOps(doc, many, { maxOperations: 2 })).toThrow(DesignOpError);
  });
});

describe("links and index entries must point at an artboard that exists", () => {
  const doc = sampleDocument();

  it("refuses a link to a missing artboard, and accepts a link to a real one", () => {
    expect(refused(doc, [{ type: "node.update", nodeId: "title", patch: { linkTo: "nowhere" } }])).toBe("linkTo");
    const linked = applyOps(doc, [{ type: "node.update", nodeId: "title", patch: { linkTo: "board_2" } }]);
    expect(linked.document.nodes.title.linkTo).toBe("board_2");
  });

  it("refuses an index entry whose thumbnail names a missing artboard", () => {
    const ops: DesignOperation[] = [{
      type: "artboard.add", pageId: "page_1",
      artboard: { id: "thumb", name: "Desktop", width: 320, height: 220, background: INK, nodeIds: [], thumbnailOf: "nowhere" },
    }];
    expect(refused(doc, ops)).toBe("thumbnailOf");
  });

  it("refuses a batch whose index entry goes stale when its source artboard is deleted in the same batch", () => {
    const indexed = applyOps(doc, [{
      type: "page.add",
      page: { id: "index", name: "Índice", artboards: [
        { id: "thumb", name: "Phone", width: 320, height: 220, background: INK, nodeIds: [], thumbnailOf: "board_2" },
      ] },
    }]).document;
    expect(refused(indexed, [{ type: "artboard.delete", pageId: "page_1", artboardId: "board_2" }])).toBe("thumbnailOf");
  });
});

describe("documents saved before links and index pages existed", () => {
  it("load and edit unchanged, with the new fields absent", () => {
    const old = sampleDocument();
    expect(old.nodes.title.linkTo).toBeUndefined();
    expect(old.pages[0].artboards[0].thumbnailOf).toBeUndefined();
    expect(old.pages[0].isIndex).toBeUndefined();
    const edited = applyOps(old, [{ type: "document.rename", title: "Renamed" }]);
    expect(edited.document.title).toBe("Renamed");
  });

  it("recognise an index page by its flag, and still by the name older documents gave it", () => {
    expect(isIndexPage({ id: "p", name: "Índice", artboards: [] })).toBe(true);
    expect(isIndexPage({ id: "p", name: "Index", isIndex: true, artboards: [] })).toBe(true);
    expect(isIndexPage({ id: "p", name: "Page 1", artboards: [] })).toBe(false);
  });
});

describe("artboard.set_tweak", () => {
  const SOURCE = `<!doctype html><html><head><style>:root{ /* @tweak accent color "Accent" */ --tw-accent: #0e7490; /* @tweak density choice "Density" "compact" "comfortable" */ --tw-density: comfortable; }</style></head><body></body></html>`;
  const withHtml = () => applyOps(sampleDocument(), [{ type: "artboard.set_html", pageId: "page_1", artboardId: "board_1", html: SOURCE }], { now: NOW }).document;
  const tweak = (name: string, value: string): DesignOperation => ({ type: "artboard.set_tweak", pageId: "page_1", artboardId: "board_1", name, value });

  it("applies a valid value and its inverse restores the document", () => {
    const doc = withHtml();
    const forward = roundTrip(doc, [tweak("accent", "#cc5500")]);
    const board = forward.document.pages[0]!.artboards.find((b) => b.id === "board_1")!;
    expect(board.html).toContain("--tw-accent: #cc5500;");
  });

  it("refuses an invalid value without changing the document", () => {
    const doc = withHtml();
    const before = JSON.stringify(doc);
    expect(refused(doc, [tweak("accent", "red")])).toBe("htmlPath");
    expect(refused(doc, [tweak("density", "loose")])).toBe("htmlPath");
    expect(JSON.stringify(doc)).toBe(before);
  });
});
