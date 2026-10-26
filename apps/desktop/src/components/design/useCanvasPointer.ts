import { useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

import { frameOps } from "@/lib/design/edits";
import { inkColour, shapeFill } from "@/lib/design/defaults";
import {
  HANDLES,
  SNAP_THRESHOLD,
  artboardAt,
  boundsOf,
  handlePoint,
  hitTest,
  layoutArtboards,
  nodesInMarquee,
  offset,
  rectFromPoints,
  resize,
  snap,
  toScreen,
  toWorld,
  zoomAt,
  type Guide,
  type Handle,
  type Point,
  type Viewport,
} from "@/lib/design/geometry";
import { newId, type DesignDocument, type DesignNode, type DesignOperation, type DesignPage, type Rect } from "@/lib/design/model";
import type { DesignTool } from "@/lib/design/tools";

/** What a press is doing until it is released. Live previews read it; release turns it into ops. */
export type Gesture =
  | { kind: "move"; start: Point; ids: string[]; artboardId: string; delta: Point; guides: Guide[] }
  | { kind: "resize"; start: Point; id: string; handle: Handle; frame: Rect }
  | { kind: "marquee"; start: Point; current: Point; base: string[] }
  | { kind: "create"; tool: "rectangle" | "ellipse" | "line"; artboardId: string; start: Point; current: Point }
  | { kind: "draw"; artboardId: string; points: Point[] }
  | { kind: "pan"; last: Point }
  | { kind: "pinch"; distance: number; mid: Point };

export interface Dispatch {
  (ops: DesignOperation[], meta: { label: string; coalesceKey?: string }): boolean;
}

export interface PointerDeps {
  svgRef: RefObject<SVGSVGElement | null>;
  doc: DesignDocument;
  page: DesignPage | undefined;
  viewport: Viewport;
  setViewport: (vp: Viewport) => void;
  selection: readonly string[];
  select: (ids: string[]) => void;
  tool: DesignTool;
  setTool: (tool: DesignTool) => void;
  dispatch: Dispatch;
  spaceHeld: RefObject<boolean>;
  /** Text tool click: open the editor for a new text element at this artboard-local point. */
  onNewText: (artboardId: string, local: Point) => void;
  onNewNote: (artboardId: string, local: Point) => void;
  onNewFrame: () => void;
  /** Comment tool click: open the comment editor here. */
  onNewComment: (artboardId: string, local: Point, nodeId: string | undefined) => void;
  t: (key: string) => string;
}

const HANDLE_HIT = 8;
const DEFAULT_SIZE = { width: 160, height: 100 };

function screenPoint(svg: SVGSVGElement | null, e: { clientX: number; clientY: number }): Point {
  const r = svg?.getBoundingClientRect();
  return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) };
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Points to a frame plus the points normalised to it (what `ShapeNode.points` stores). */
function normalise(points: readonly Point[]): { frame: Rect; flat: number[] } {
  const b = boundsOf(points.map((p) => ({ x: p.x, y: p.y, width: 0, height: 0 }))) ?? { x: 0, y: 0, width: 0, height: 0 };
  const width = Math.max(1, b.width);
  const height = Math.max(1, b.height);
  const flat = points.flatMap((p) => [(p.x - b.x) / width, (p.y - b.y) / height]);
  return { frame: { x: Math.round(b.x), y: Math.round(b.y), width: Math.round(width), height: Math.round(height) }, flat };
}

/**
 * Every pointer interaction of the canvas: select, marquee, move with snapping, resize, create,
 * freehand, pan (space + drag, middle button, two fingers) and pinch zoom. Pointer events, so mouse,
 * pen and touch take the same path. A gesture previews locally and becomes ONE batch on release —
 * which is what makes a drag one step to undo.
 */
export function useCanvasPointer(deps: PointerDeps) {
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const pointers = useRef(new Map<number, Point>());
  const g = useRef<Gesture | null>(null);

  function update(next: Gesture | null): void {
    g.current = next;
    setGesture(next);
  }

  const layout = layoutArtboards(deps.page);

  function local(world: Point, artboardId: string): Point {
    const o = layout.get(artboardId) ?? { x: 0, y: 0 };
    return { x: world.x - o.x, y: world.y - o.y };
  }

  /** The resize handle under a screen point, for a single selected element. */
  function handleAt(screen: Point): Handle | undefined {
    if (deps.selection.length !== 1) return undefined;
    const node = deps.doc.nodes[deps.selection[0]];
    const board = deps.page?.artboards.find((b) => b.nodeIds.includes(deps.selection[0]));
    const origin = board ? layout.get(board.id) : undefined;
    if (!node || !origin || node.locked) return undefined;
    const world = offset(node.frame, origin.x, origin.y);
    return HANDLES.find((h) => distance(toScreen(handlePoint(world, h), deps.viewport), screen) <= HANDLE_HIT);
  }

  function onPointerDown(e: ReactPointerEvent<SVGSVGElement>): void {
    const svg = deps.svgRef.current;
    const screen = screenPoint(svg, e);
    pointers.current.set(e.pointerId, screen);
    svg?.setPointerCapture?.(e.pointerId);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      update({ kind: "pinch", distance: distance(a, b), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } });
      return;
    }
    if (e.button === 1 || deps.spaceHeld.current) {
      e.preventDefault();
      update({ kind: "pan", last: screen });
      return;
    }
    if (e.button !== 0) return;
    if (deps.tool === "pan") { e.preventDefault(); return update({ kind: "pan", last: screen }); }
    if (deps.tool === "frame") return deps.onNewFrame();
    const world = toWorld(screen, deps.viewport);
    const board = artboardAt(deps.page, world);
    if (deps.tool === "select") return beginSelect(e.shiftKey, screen, world);
    if (!board) return;
    const at = local(world, board.id);
    if (deps.tool === "text") return deps.onNewText(board.id, at);
    if (deps.tool === "note") return deps.onNewNote(board.id, at);
    if (deps.tool === "comment") return deps.onNewComment(board.id, at, hitTest(deps.doc, deps.page, world)?.nodeId);

    if (deps.tool === "draw") return update({ kind: "draw", artboardId: board.id, points: [at] });
    if (deps.tool === "rectangle" || deps.tool === "ellipse" || deps.tool === "line")
      update({ kind: "create", tool: deps.tool, artboardId: board.id, start: at, current: at });
  }

  function beginSelect(shift: boolean, screen: Point, world: Point): void {
    const handle = handleAt(screen);
    if (handle) {
      const node = deps.doc.nodes[deps.selection[0]];
      return update({ kind: "resize", start: world, id: node.id, handle, frame: node.frame });
    }
    const hit = hitTest(deps.doc, deps.page, world);
    if (hit?.nodeId) {
      const id = hit.nodeId;
      let ids = deps.selection.includes(id) ? [...deps.selection] : [id];
      if (shift) ids = deps.selection.includes(id) ? deps.selection.filter((x) => x !== id) : [...deps.selection, id];
      deps.select(ids);
      if (ids.includes(id)) {
        update({ kind: "move", start: world, ids, artboardId: hit.artboardId, delta: { x: 0, y: 0 }, guides: [] });
      }
      return;
    }
    if (!shift) deps.select([]);
    update({ kind: "marquee", start: world, current: world, base: shift ? [...deps.selection] : [] });
  }

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>): void {
    const screen = screenPoint(deps.svgRef.current, e);
    const previous = pointers.current.get(e.pointerId);
    if (previous) pointers.current.set(e.pointerId, screen);
    const cur = g.current;
    if (!cur) return;
    if (cur.kind === "pinch") {
      const pts = [...pointers.current.values()];
      if (pts.length < 2) return;
      const d = distance(pts[0], pts[1]);
      const mid = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      const zoomed = zoomAt(deps.viewport, cur.distance > 0 ? d / cur.distance : 1, mid);
      deps.setViewport({ ...zoomed, x: zoomed.x + mid.x - cur.mid.x, y: zoomed.y + mid.y - cur.mid.y });
      return update({ kind: "pinch", distance: d, mid });
    }
    if (cur.kind === "pan") {
      deps.setViewport({ ...deps.viewport, x: deps.viewport.x + screen.x - cur.last.x, y: deps.viewport.y + screen.y - cur.last.y });
      return update({ kind: "pan", last: screen });
    }
    const world = toWorld(screen, deps.viewport);
    switch (cur.kind) {
      case "move":
        return update(previewMove(cur, world));
      case "resize": {
        const node = deps.doc.nodes[cur.id];
        if (!node) return;
        return update({ ...cur, frame: resize(node.frame, cur.handle, world.x - cur.start.x, world.y - cur.start.y) });
      }
      case "marquee":
        return update({ ...cur, current: world });
      case "create":
        return update({ ...cur, current: local(world, cur.artboardId) });
      case "draw":
        return update({ ...cur, points: [...cur.points, local(world, cur.artboardId)] });
    }
  }

  function previewMove(cur: Extract<Gesture, { kind: "move" }>, world: Point): Gesture {
    const raw = { x: world.x - cur.start.x, y: world.y - cur.start.y };
    const board = deps.page?.artboards.find((b) => b.id === cur.artboardId);
    const frames = cur.ids.map((id) => deps.doc.nodes[id]?.frame).filter((f): f is Rect => f !== undefined);
    const moving = boundsOf(frames);
    if (!board || !moving) return { ...cur, delta: raw };
    const targets: Rect[] = [{ x: 0, y: 0, width: board.width, height: board.height }];
    for (const id of board.nodeIds) {
      if (!cur.ids.includes(id) && deps.doc.nodes[id]?.visible) targets.push(deps.doc.nodes[id].frame);
    }
    const s = snap(offset(moving, raw.x, raw.y), targets, SNAP_THRESHOLD / deps.viewport.zoom);
    return { ...cur, delta: { x: raw.x + s.dx, y: raw.y + s.dy }, guides: s.guides };
  }

  function onPointerUp(e: ReactPointerEvent<SVGSVGElement>): void {
    pointers.current.delete(e.pointerId);
    deps.svgRef.current?.releasePointerCapture?.(e.pointerId);
    const cur = g.current;
    update(null);
    if (!cur) return;
    switch (cur.kind) {
      case "move": {
        const frames = new Map(cur.ids.flatMap((id) => {
          const node = deps.doc.nodes[id];
          return node ? [[id, offset(node.frame, cur.delta.x, cur.delta.y)] as const] : [];
        }));
        const ops = frameOps(frames, deps.doc);
        if (ops.length) deps.dispatch(ops, { label: "design.did.move" });
        return;
      }
      case "resize": {
        const ops = frameOps(new Map([[cur.id, cur.frame]]), deps.doc);
        if (ops.length) deps.dispatch(ops, { label: "design.did.resize" });
        return;
      }
      case "marquee": {
        const box = rectFromPoints(cur.start, cur.current);
        if (box.width * deps.viewport.zoom < 3 && box.height * deps.viewport.zoom < 3) return;
        deps.select([...new Set([...cur.base, ...nodesInMarquee(deps.doc, deps.page, box)])]);
        return;
      }
      case "create":
        return finishCreate(cur);
      case "draw":
        return finishDraw(cur);
    }
  }

  function addNode(artboardId: string, node: DesignNode, label: string): void {
    const pageId = deps.page?.id;
    if (!pageId) return;
    if (deps.dispatch([{ type: "node.add", pageId, artboardId, node }], { label })) {
      deps.select([node.id]);
      deps.setTool("select");
    }
  }

  function finishCreate(cur: Extract<Gesture, { kind: "create" }>): void {
    let box = rectFromPoints(cur.start, cur.current);
    const clicked = box.width < 2 && box.height < 2;
    if (clicked) box = { x: cur.start.x, y: cur.start.y, ...DEFAULT_SIZE };
    if (cur.tool === "line") {
      const end = clicked ? { x: cur.start.x + DEFAULT_SIZE.width, y: cur.start.y } : cur.current;
      const { frame, flat } = normalise([cur.start, end]);
      return addNode(cur.artboardId, {
        id: newId("node"), type: "shape", shape: "line", name: deps.t("design.tool.line"), frame, points: flat,
        visible: true, style: { stroke: inkColour(deps.doc), strokeWidth: 2 },
      }, "design.did.add");
    }
    const fill = shapeFill(deps.doc);
    addNode(cur.artboardId, {
      id: newId("node"), type: "shape", shape: cur.tool,
      name: deps.t(cur.tool === "ellipse" ? "design.tool.ellipse" : "design.tool.rectangle"),
      frame: { x: Math.round(box.x), y: Math.round(box.y), width: Math.max(1, Math.round(box.width)), height: Math.max(1, Math.round(box.height)) },
      visible: true, style: fill ? { fill } : undefined,
    }, "design.did.add");
  }

  function finishDraw(cur: Extract<Gesture, { kind: "draw" }>): void {
    if (cur.points.length < 2) return;
    const { frame, flat } = normalise(cur.points);
    addNode(cur.artboardId, {
      id: newId("node"), type: "shape", shape: "path", name: deps.t("design.tool.draw"), frame, points: flat,
      visible: true, style: { stroke: inkColour(deps.doc), strokeWidth: 2 },
    }, "design.did.add");
  }

  function onPointerCancel(e: ReactPointerEvent<SVGSVGElement>): void {
    pointers.current.delete(e.pointerId);
    update(null);
  }

  return { gesture, onPointerDown, onPointerMove, onPointerUp, onPointerCancel };
}
