/**
 * Geometry for the design canvas: where things are, what is under the pointer, how they align.
 *
 * Coordinates come in three spaces, and every function says which it takes:
 *  - **screen** — pixels inside the canvas element, as a pointer reports them;
 *  - **world** — the page's coordinates, where artboards sit side by side;
 *  - **local** — an artboard's own coordinates, where node frames live (the wire format).
 *
 * Pure functions only; the canvas owns the state.
 */
import type { Artboard, DesignDocument, DesignNode, DesignPage, Rect } from "@/lib/design/model";

export interface Point {
  x: number;
  y: number;
}

/** Pan offset in screen pixels and the zoom factor. world = (screen - pan) / zoom. */
export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 8;
export const ARTBOARD_GAP = 80;
export const SNAP_THRESHOLD = 6;

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

export function toWorld(point: Point, vp: Viewport): Point {
  return { x: (point.x - vp.x) / vp.zoom, y: (point.y - vp.y) / vp.zoom };
}

export function toScreen(point: Point, vp: Viewport): Point {
  return { x: point.x * vp.zoom + vp.x, y: point.y * vp.zoom + vp.y };
}

/** Zoom by `factor`, keeping the world point under `anchor` (screen) where it is. */
export function zoomAt(vp: Viewport, factor: number, anchor: Point): Viewport {
  const zoom = clampZoom(vp.zoom * factor);
  const world = toWorld(anchor, vp);
  return { zoom, x: anchor.x - world.x * zoom, y: anchor.y - world.y * zoom };
}

export function pan(vp: Viewport, dx: number, dy: number): Viewport {
  return { ...vp, x: vp.x + dx, y: vp.y + dy };
}

/** A viewport that shows `bounds` (world) inside a `width`×`height` screen box, with a margin. */
export function fitViewport(bounds: Rect, width: number, height: number, margin = 40): Viewport {
  if (width <= 0 || height <= 0 || bounds.width <= 0 || bounds.height <= 0) return { x: margin, y: margin, zoom: 1 };
  const zoom = clampZoom(Math.min((width - margin * 2) / bounds.width, (height - margin * 2) / bounds.height, 1));
  return {
    zoom,
    x: (width - bounds.width * zoom) / 2 - bounds.x * zoom,
    y: (height - bounds.height * zoom) / 2 - bounds.y * zoom,
  };
}

/** Artboards laid out left to right with a fixed gap, tops aligned. World rects by artboard id. */
export function layoutArtboards(page: DesignPage | undefined): Map<string, Rect> {
  const out = new Map<string, Rect>();
  let x = 0;
  for (const board of page?.artboards ?? []) {
    out.set(board.id, { x, y: 0, width: board.width, height: board.height });
    x += board.width + ARTBOARD_GAP;
  }
  return out;
}

export function contains(rect: Rect, point: Point): boolean {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
}

export function intersects(a: Rect, b: Rect): boolean {
  return a.x <= b.x + b.width && b.x <= a.x + a.width && a.y <= b.y + b.height && b.y <= a.y + a.height;
}

/** A rect from two corners, in either order. */
export function rectFromPoints(a: Point, b: Point): Rect {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) };
}

export function boundsOf(rects: readonly Rect[]): Rect | undefined {
  if (rects.length === 0) return undefined;
  const x0 = Math.min(...rects.map((r) => r.x));
  const y0 = Math.min(...rects.map((r) => r.y));
  const x1 = Math.max(...rects.map((r) => r.x + r.width));
  const y1 = Math.max(...rects.map((r) => r.y + r.height));
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function offset(rect: Rect, dx: number, dy: number): Rect {
  return { ...rect, x: rect.x + dx, y: rect.y + dy };
}

/** Is `point` (node-local, i.e. relative to the frame) on the node's painted area? */
function hitsShape(node: DesignNode, local: Point): boolean {
  const { width: w, height: h } = node.frame;
  if (node.type === "shape" && node.shape === "ellipse") {
    const rx = w / 2;
    const ry = h / 2;
    const dx = (local.x - rx) / rx;
    const dy = (local.y - ry) / ry;
    return dx * dx + dy * dy <= 1;
  }
  return local.x >= 0 && local.x <= w && local.y >= 0 && local.y <= h;
}

export interface Hit {
  artboardId: string;
  nodeId?: string;
}

/**
 * What is under a world point: the topmost visible node, else the artboard, else nothing.
 * Nodes later in an artboard's list paint on top, so they are tested first.
 */
export function hitTest(doc: DesignDocument, page: DesignPage | undefined, world: Point): Hit | undefined {
  if (!page) return undefined;
  const layout = layoutArtboards(page);
  for (let b = page.artboards.length - 1; b >= 0; b--) {
    const board = page.artboards[b];
    const rect = layout.get(board.id);
    if (!rect) continue;
    for (let i = board.nodeIds.length - 1; i >= 0; i--) {
      const node = doc.nodes[board.nodeIds[i]];
      if (!node || !node.visible) continue;
      const local = { x: world.x - rect.x - node.frame.x, y: world.y - rect.y - node.frame.y };
      if (hitsShape(node, local)) return { artboardId: board.id, nodeId: node.id };
    }
    if (contains(rect, world)) return { artboardId: board.id };
  }
  return undefined;
}

/** The artboard a world point falls in, or the nearest one when it falls between them. */
export function artboardAt(page: DesignPage | undefined, world: Point): Artboard | undefined {
  if (!page || page.artboards.length === 0) return undefined;
  const layout = layoutArtboards(page);
  let best: Artboard | undefined;
  let bestDistance = Infinity;
  for (const board of page.artboards) {
    const r = layout.get(board.id);
    if (!r) continue;
    const dx = Math.max(r.x - world.x, 0, world.x - (r.x + r.width));
    const dy = Math.max(r.y - world.y, 0, world.y - (r.y + r.height));
    const d = Math.hypot(dx, dy);
    if (d < bestDistance) {
      bestDistance = d;
      best = board;
    }
  }
  return best;
}

/** Ids of the visible nodes whose WORLD rect meets `marquee` (world). */
export function nodesInMarquee(doc: DesignDocument, page: DesignPage | undefined, marquee: Rect): string[] {
  if (!page) return [];
  const layout = layoutArtboards(page);
  const out: string[] = [];
  for (const board of page.artboards) {
    const origin = layout.get(board.id);
    if (!origin) continue;
    for (const id of board.nodeIds) {
      const node = doc.nodes[id];
      if (node?.visible && intersects(offset(node.frame, origin.x, origin.y), marquee)) out.push(id);
    }
  }
  return out;
}

export type AlignMode = "left" | "center" | "right" | "top" | "middle" | "bottom";

/**
 * Align frames to each other, or — when there is only one — to `container` (its artboard, in the
 * same local space). Returns new frames in the same order.
 */
export function align(frames: readonly Rect[], mode: AlignMode, container?: Rect): Rect[] {
  const target = frames.length === 1 && container ? container : boundsOf(frames);
  if (!target) return [];
  return frames.map((f) => {
    switch (mode) {
      case "left":
        return { ...f, x: target.x };
      case "center":
        return { ...f, x: target.x + (target.width - f.width) / 2 };
      case "right":
        return { ...f, x: target.x + target.width - f.width };
      case "top":
        return { ...f, y: target.y };
      case "middle":
        return { ...f, y: target.y + (target.height - f.height) / 2 };
      case "bottom":
        return { ...f, y: target.y + target.height - f.height };
    }
  });
}

export interface Guide {
  axis: "x" | "y";
  /** Position of the guide line on its axis (same space as the rects). */
  at: number;
}

export interface SnapResult {
  dx: number;
  dy: number;
  guides: Guide[];
}

function edges(rect: Rect, axis: "x" | "y"): number[] {
  return axis === "x"
    ? [rect.x, rect.x + rect.width / 2, rect.x + rect.width]
    : [rect.y, rect.y + rect.height / 2, rect.y + rect.height];
}

function snapAxis(moving: Rect, targets: readonly Rect[], axis: "x" | "y", threshold: number): { delta: number; at?: number } {
  let best: { delta: number; at?: number } = { delta: 0 };
  let bestAbs = threshold + Number.EPSILON;
  for (const t of targets) {
    for (const m of edges(moving, axis)) {
      for (const e of edges(t, axis)) {
        const d = e - m;
        if (Math.abs(d) < bestAbs) {
          bestAbs = Math.abs(d);
          best = { delta: d, at: e };
        }
      }
    }
  }
  return best;
}

/**
 * Snap `moving` to the edges and centres of `targets` (same space) when within `threshold`.
 * Returns the correction to add and the guides to draw. Each axis snaps independently.
 */
export function snap(moving: Rect, targets: readonly Rect[], threshold = SNAP_THRESHOLD): SnapResult {
  const x = snapAxis(moving, targets, "x", threshold);
  const y = snapAxis(moving, targets, "y", threshold);
  const guides: Guide[] = [];
  if (x.at !== undefined) guides.push({ axis: "x", at: x.at });
  if (y.at !== undefined) guides.push({ axis: "y", at: y.at });
  return { dx: x.delta, dy: y.delta, guides };
}

export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
export const HANDLES: readonly Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** Where each handle sits on a rect, as fractions of width and height. */
export function handlePoint(rect: Rect, handle: Handle): Point {
  const fx = handle.includes("w") ? 0 : handle.includes("e") ? 1 : 0.5;
  const fy = handle.includes("n") ? 0 : handle.includes("s") ? 1 : 0.5;
  return { x: rect.x + rect.width * fx, y: rect.y + rect.height * fy };
}

/** Drag `handle` by (dx, dy); the opposite edge stays put and the size never drops below 1. */
export function resize(frame: Rect, handle: Handle, dx: number, dy: number): Rect {
  let { x, y, width, height } = frame;
  if (handle.includes("e")) width = Math.max(1, width + dx);
  if (handle.includes("s")) height = Math.max(1, height + dy);
  if (handle.includes("w")) {
    const w = Math.max(1, width - dx);
    x += width - w;
    width = w;
  }
  if (handle.includes("n")) {
    const h = Math.max(1, height - dy);
    y += height - h;
    height = h;
  }
  return { ...frame, x, y, width, height };
}

/** Round a frame to whole pixels, which is what a person typing in the inspector expects to see. */
export function roundFrame(frame: Rect): Rect {
  return {
    ...frame,
    x: Math.round(frame.x),
    y: Math.round(frame.y),
    width: Math.max(1, Math.round(frame.width)),
    height: Math.max(1, Math.round(frame.height)),
  };
}
