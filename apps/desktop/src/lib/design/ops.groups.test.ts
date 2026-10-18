import { describe, expect, it } from "vitest";

import { deleteOps } from "@/lib/design/edits";
import { applyOps, DesignOpError } from "@/lib/design/ops";
import type { DesignDocument, DesignNode, DesignOperation } from "@/lib/design/model";
import { sampleDocument } from "@/test/design-fixture";

/**
 * The client agrees with the server about groups (D6). The domain PR reported three divergences: the
 * client refused to delete an element inside a group (the server allows it), deleting a group left
 * its children in the document placed nowhere (the server deletes them), and the TypeScript model
 * required an `ownerId` the server's model does not have and refuses as an unknown key.
 */

/** The sample document with `box` and `dot` moved into a group `pair` on board_1. */
function grouped(): DesignDocument {
  const doc = sampleDocument();
  const pair: DesignNode = {
    id: "pair",
    type: "group",
    name: "Pair",
    frame: { x: 100, y: 300, width: 600, height: 100 },
    visible: true,
    childIds: ["box", "dot"],
  };
  doc.nodes.pair = pair;
  doc.pages[0].artboards[0].nodeIds = ["title", "pair"];
  return doc;
}

function roundTrip(doc: DesignDocument, ops: DesignOperation[]) {
  const forward = applyOps(doc, ops);
  const back = applyOps(forward.document, forward.inverse);
  const strip = (d: DesignDocument) => ({ ...d, revision: 0, updatedAt: "" });
  expect(strip(back.document)).toEqual(strip(doc));
  return forward;
}

function code(doc: DesignDocument, ops: DesignOperation[]): string {
  try {
    applyOps(doc, ops);
  } catch (error) {
    expect(error).toBeInstanceOf(DesignOpError);
    return (error as DesignOpError).code;
  }
  throw new Error("expected a refusal");
}

describe("groups, as the server has them", () => {
  it("deletes a group with everything inside it, and undo brings the whole subtree back", () => {
    const doc = grouped();
    const { document, inverse } = roundTrip(doc, [{ type: "node.delete", nodeId: "pair" }]);
    expect(document.nodes.pair).toBeUndefined();
    expect(document.nodes.box).toBeUndefined();
    expect(document.nodes.dot).toBeUndefined();
    expect(document.pages[0].artboards[0].nodeIds).toEqual(["title"]);
    // The inverse is the server's shape: the group, its slot, and its descendants in `children`.
    expect(inverse).toEqual([
      expect.objectContaining({ type: "node.add", node: expect.objectContaining({ id: "pair" }), index: 1, children: [doc.nodes.box, doc.nodes.dot] }),
    ]);
  });

  it("deletes an element inside a group, and undo puts it back in the group at its slot", () => {
    const doc = grouped();
    const { document, inverse } = roundTrip(doc, [{ type: "node.delete", nodeId: "box" }]);
    expect(document.nodes.box).toBeUndefined();
    expect(document.nodes.pair).toMatchObject({ childIds: ["dot"] });
    expect(inverse).toEqual([expect.objectContaining({ type: "node.add", parentId: "pair", index: 0 })]);
  });

  it("adds into a group with parentId, and refuses a parent that is not a group or is elsewhere", () => {
    const doc = grouped();
    const fresh: DesignNode = { ...doc.nodes.box, id: "fresh" } as DesignNode;
    const { document } = roundTrip(doc, [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: fresh, parentId: "pair", index: 1 }]);
    expect(document.nodes.pair).toMatchObject({ childIds: ["box", "fresh", "dot"] });
    expect(code(doc, [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: fresh, parentId: "title" }])).toBe("parentNotGroup");
    expect(code(doc, [{ type: "node.add", pageId: "page_1", artboardId: "board_2", node: fresh, parentId: "pair" }])).toBe("parentNotOnArtboard");
  });

  it("refuses a group whose children do not match what it brings", () => {
    const doc = sampleDocument();
    const group: DesignNode = { id: "g", type: "group", name: "G", frame: { x: 0, y: 0, width: 10, height: 10 }, visible: true, childIds: ["a"] };
    const stray: DesignNode = { ...doc.nodes.box, id: "b" } as DesignNode;
    expect(code(doc, [{ type: "node.add", pageId: "page_1", artboardId: "board_1", node: group, children: [stray] }])).toBe("childIds");
  });

  it("refuses to delete a group that holds a locked element, as the server does", () => {
    const doc = grouped();
    (doc.nodes.dot as DesignNode).locked = true;
    expect(code(doc, [{ type: "node.delete", nodeId: "pair" }])).toBe("locked");
  });

  it("deleteOps leaves an element to its selected group, and skips a group with a lock inside", () => {
    const doc = grouped();
    expect(deleteOps(doc, ["pair", "box"])).toEqual([{ type: "node.delete", nodeId: "pair" }]);
    expect(deleteOps(doc, ["box"])).toEqual([{ type: "node.delete", nodeId: "box" }]);
    (doc.nodes.dot as DesignNode).locked = true;
    expect(deleteOps(doc, ["pair"])).toEqual([]);
  });

  it("has no ownerId: the server's model has none and refuses unknown keys", () => {
    expect(Object.keys(sampleDocument())).not.toContain("ownerId");
  });
});
