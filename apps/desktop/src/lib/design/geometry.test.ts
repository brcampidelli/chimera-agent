import { describe, expect, it } from "vitest";

import { alignOps, duplicateOps, stepSelection } from "@/lib/design/edits";
import {
  ARTBOARD_GAP,
  fillArtboardWidth,
  align,
  hitTest,
  layoutArtboards,
  nodesInMarquee,
  resize,
  snap,
  toScreen,
  toWorld,
  zoomAt,
} from "@/lib/design/geometry";
import { hslTripleToHex, paperColour } from "@/lib/design/defaults";
import { applyOps } from "@/lib/design/ops";
import { emptyDocument, PAPER, sampleDocument } from "@/test/design-fixture";

const frames = [
  { x: 10, y: 10, width: 20, height: 20 },
  { x: 50, y: 40, width: 40, height: 10 },
];

describe("align", () => {
  it.each([
    ["left", [10, 10]],
    ["center", [40, 30]],
    ["right", [70, 50]],
  ] as const)("%s aligns x to the shared bounds", (mode, xs) => {
    expect(align(frames, mode).map((f) => f.x)).toEqual(xs);
  });

  it.each([
    ["top", [10, 10]],
    ["middle", [20, 25]],
    ["bottom", [30, 40]],
  ] as const)("%s aligns y to the shared bounds", (mode, ys) => {
    expect(align(frames, mode).map((f) => f.y)).toEqual(ys);
  });

  it("aligns a single element to its container", () => {
    expect(align([frames[0]], "right", { x: 0, y: 0, width: 100, height: 100 })[0].x).toBe(80);
  });

  it("produces node.move ops for the selection, aligned to the artboard when alone", () => {
    const doc = sampleDocument();
    const [op] = alignOps(doc, ["box"], "center");
    expect(op).toEqual({ type: "node.move", nodeId: "box", frame: { x: 620, y: 300, width: 200, height: 100 } });
  });
});

describe("snap", () => {
  it("pulls an edge within the threshold onto the target's edge, with a guide", () => {
    const r = snap({ x: 103, y: 500, width: 50, height: 50 }, [{ x: 100, y: 0, width: 40, height: 10 }], 6);
    expect(r.dx).toBe(-3);
    expect(r.guides).toContainEqual({ axis: "x", at: 100 });
  });

  it("leaves it alone outside the threshold", () => {
    const r = snap({ x: 120, y: 500, width: 50, height: 50 }, [{ x: 100, y: 0, width: 10, height: 10 }], 6);
    expect(r).toEqual({ dx: 0, dy: 0, guides: [] });
  });

  it("snaps centres too", () => {
    // Target edges 0 / 50 / 100; moving edges 43 / 48 / 53: the nearest pair is centre to centre.
    const r = snap({ x: 0, y: 43, width: 10, height: 10 }, [{ x: 200, y: 0, width: 30, height: 100 }], 6);
    expect(r.dy).toBe(2);
    expect(r.guides).toContainEqual({ axis: "y", at: 50 });
  });
});

describe("hit testing and selection geometry", () => {
  const doc = sampleDocument();
  const page = doc.pages[0];

  it("lays artboards out side by side", () => {
    const layout = layoutArtboards(page);
    expect(layout.get("board_2")?.x).toBe(1440 + ARTBOARD_GAP);
  });

  it("uses available width for fill HTML artboards, with a floor and stored maximum", () => {
    const fluid = { ...page, artboards: [{ ...page.artboards[0], width: 1200, html: "<main/>", mode: "fill" as const }] };
    expect(fillArtboardWidth(fluid.artboards[0], 700)).toBe(700);
    expect(fillArtboardWidth(fluid.artboards[0], 180)).toBe(320);
    expect(fillArtboardWidth(fluid.artboards[0], 1600)).toBe(1200);
    expect(layoutArtboards(fluid, 700).get(fluid.artboards[0].id)?.width).toBe(700);
    expect(layoutArtboards(fluid, 700 / 0.5).get(fluid.artboards[0].id)?.width).toBe(1200);
  });

  it("keeps the stored width on the canvas, which passes no available width", () => {
    const fluid = { ...page, artboards: [{ ...page.artboards[0], width: 1200, html: "<main/>", mode: "fill" as const }] };
    expect(layoutArtboards(fluid).get(fluid.artboards[0].id)?.width).toBe(1200);
  });

  it("finds the topmost element, then the artboard, then nothing", () => {
    expect(hitTest(doc, page, { x: 150, y: 350 })).toEqual({ artboardId: "board_1", nodeId: "box" });
    expect(hitTest(doc, page, { x: 1000, y: 800 })).toEqual({ artboardId: "board_1" });
    expect(hitTest(doc, page, { x: -50, y: -50 })).toBeUndefined();
  });

  it("tests an ellipse by its shape, not its box", () => {
    expect(hitTest(doc, page, { x: 650, y: 350 })?.nodeId).toBe("dot");
    expect(hitTest(doc, page, { x: 602, y: 302 })?.nodeId).toBeUndefined();
  });

  it("selects what a marquee touches", () => {
    expect(nodesInMarquee(doc, page, { x: 90, y: 90, width: 120, height: 220 }).sort()).toEqual(["box", "title"]);
  });

  it("keeps the world point under the cursor fixed while zooming", () => {
    const vp = { x: 10, y: 20, zoom: 1 };
    const anchor = { x: 300, y: 200 };
    const before = toWorld(anchor, vp);
    const after = zoomAt(vp, 2, anchor);
    expect(toScreen(before, after)).toEqual(anchor);
    expect(zoomAt(vp, 1000, anchor).zoom).toBe(8);
  });

  it("resizes from any handle with the opposite edge fixed and a 1px floor", () => {
    const f = { x: 10, y: 10, width: 100, height: 50 };
    expect(resize(f, "se", 10, 5)).toMatchObject({ x: 10, y: 10, width: 110, height: 55 });
    expect(resize(f, "nw", 10, 5)).toMatchObject({ x: 20, y: 15, width: 90, height: 45 });
    expect(resize(f, "w", 500, 0)).toMatchObject({ x: 109, width: 1 });
  });

  it("walks the selection in paint order and wraps", () => {
    expect(stepSelection(["a", "b", "c"], [], 1)).toBe("a");
    expect(stepSelection(["a", "b", "c"], ["c"], 1)).toBe("a");
    expect(stepSelection(["a", "b", "c"], ["a"], -1)).toBe("c");
  });

  it("duplicates next to the original with a fresh id", () => {
    const { ops, ids } = duplicateOps(doc, ["box"]);
    const next = applyOps(doc, ops).document;
    expect(ids[0]).not.toBe("box");
    expect(next.nodes[ids[0]].frame).toMatchObject({ x: 110, y: 310 });
  });
});

describe("defaults come from the document or the theme, never a literal", () => {
  it("takes the artboard background from the document's tokens", () => {
    expect(paperColour(sampleDocument())).toBe(PAPER);
  });

  it("converts the theme's HSL triples", () => {
    expect(hslTripleToHex("0 0% 100%")).toBe("#ffffff");
    expect(hslTripleToHex("222 47% 14%")).toMatch(/^#[0-9a-f]{6}$/);
    expect(hslTripleToHex("not a colour")).toBeUndefined();
  });

  it("still yields a valid colour with no tokens and no theme", () => {
    expect(paperColour({ ...emptyDocument(), tokens: { colors: {}, typography: {}, spacing: {}, radii: {} } })).toMatch(/^#[0-9a-f]{6}$/);
  });
});
