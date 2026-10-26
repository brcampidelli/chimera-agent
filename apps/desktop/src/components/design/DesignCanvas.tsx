import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { CanvasTextEditor } from "@/components/design/CanvasTextEditor";
import { NodeShape, textMetrics } from "@/components/design/NodeShape";
import { useCanvasPointer, type Dispatch, type Gesture } from "@/components/design/useCanvasPointer";
import { focusRing } from "@/components/ui/focus";
import { moveOps, pageOrder, stepSelection } from "@/lib/design/edits";
import {
  HANDLES,
  boundsOf,
  handlePoint,
  layoutArtboards,
  offset,
  rectFromPoints,
  toScreen,
  zoomAt,
  type Point,
  type Viewport,
} from "@/lib/design/geometry";
import { newId, safeColour, type DesignDocument, type DesignNode, type Rect } from "@/lib/design/model";
import { matchDesignShortcut } from "@/lib/design/shortcuts";
import { isCommand, toolForShortcut, type DesignCommand, type DesignTool } from "@/lib/design/tools";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** An open text or comment editor. A new text element exists only here until it is committed. */
type Editing =
  | { kind: "text"; nodeId: string; artboardId: string; frame: Rect; initial: string; isNew: boolean }
  | { kind: "comment"; artboardId: string; at: Point; nodeId?: string };

export interface DesignCanvasProps {
  document: DesignDocument;
  pageId: string | undefined;
  selection: readonly string[];
  onSelectionChange: (ids: string[]) => void;
  tool: DesignTool;
  onToolChange: (tool: DesignTool) => void;
  viewport: Viewport;
  onViewportChange: (vp: Viewport) => void;
  dispatch: Dispatch;
  onCommand: (command: DesignCommand) => void;
  announce: (message: string) => void;
  /** Who a new comment is from. */
  author: string;
}

/** The frame a node is drawn at right now: its stored frame, or the live preview of a gesture. */
function liveFrame(node: DesignNode, gesture: Gesture | null): Rect {
  if (gesture?.kind === "move" && gesture.ids.includes(node.id)) return offset(node.frame, gesture.delta.x, gesture.delta.y);
  if (gesture?.kind === "resize" && gesture.id === node.id) return gesture.frame;
  return node.frame;
}

/**
 * The design canvas: an SVG of the current page's artboards and elements, with selection,
 * direct manipulation and a keyboard path through all of it.
 *
 * Controlled. The document, selection, tool and viewport come from the workspace; every edit goes
 * out through `dispatch` as an operation batch, so persistence and the agent can sit behind it
 * later without this file changing.
 */
export function DesignCanvas(props: DesignCanvasProps) {
  const { document: doc, pageId, selection, viewport, tool } = props;
  const t = useT();
  const svgRef = useRef<SVGSVGElement>(null);
  const spaceHeld = useRef(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [pointerAt, setPointerAt] = useState<Point | null>(null);
  const page = doc.pages.find((p) => p.id === pageId);
  const layout = layoutArtboards(page);

  function select(ids: string[]): void {
    props.onSelectionChange(ids);
    if (ids.length === 0) props.announce(t("design.announce.none"));
    else if (ids.length === 1) props.announce(t("design.announce.selected", { name: doc.nodes[ids[0]]?.name ?? "" }));
    else props.announce(t("design.announce.selectedMany", { n: ids.length }));
  }

  const pointer = useCanvasPointer({
    svgRef, doc, page, viewport, setViewport: props.onViewportChange, selection, select, tool,
    setTool: props.onToolChange, dispatch: props.dispatch, spaceHeld, t,
    onNewText: (artboardId, at) =>
      setEditing({ kind: "text", nodeId: newId("node"), artboardId, frame: { x: at.x, y: at.y, width: 240, height: 40 }, initial: "", isNew: true }),
    onNewComment: (artboardId, at, nodeId) => setEditing({ kind: "comment", artboardId, at, nodeId }),
  });
  const { gesture } = pointer;

  // Wheel: Mod+wheel (and a trackpad pinch, which browsers report as ctrl+wheel) zooms at the
  // pointer; a plain wheel or two-finger scroll pans. Native and non-passive, because React's
  // wheel listener is passive and could not stop the page from scrolling or zooming instead.
  const live = useRef({ viewport, onViewportChange: props.onViewportChange });
  live.current = { viewport, onViewportChange: props.onViewportChange };
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { viewport: vp, onViewportChange } = live.current;
      const r = svg.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) onViewportChange(zoomAt(vp, Math.exp(-e.deltaY / 300), { x: e.clientX - r.left, y: e.clientY - r.top }));
      else onViewportChange({ ...vp, x: vp.x - e.deltaX, y: vp.y - e.deltaY });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  function startTextEdit(id: string): void {
    const node = doc.nodes[id];
    const board = page?.artboards.find((b) => b.nodeIds.includes(id));
    if (node?.type !== "text" || !board || node.locked) return;
    setEditing({ kind: "text", nodeId: id, artboardId: board.id, frame: node.frame, initial: node.text, isNew: false });
  }

  function commitText(e: Extract<Editing, { kind: "text" }>, value: string): void {
    setEditing(null);
    svgRef.current?.focus();
    if (e.isNew) {
      if (value.trim() === "" || !page) {
        props.onToolChange("select");
        return;
      }
      const lines = value.split("\n");
      const size = textMetrics(doc, { type: "text" } as DesignNode).size;
      const node: DesignNode = {
        id: e.nodeId, type: "text", name: lines[0].slice(0, 60) || t("design.tool.text"), text: value, visible: true,
        frame: { ...e.frame, width: Math.max(e.frame.width, 1), height: Math.max(1, Math.ceil(lines.length * size * 1.25)) },
      };
      if (props.dispatch([{ type: "node.add", pageId: page.id, artboardId: e.artboardId, node }], { label: "design.did.add" })) {
        select([node.id]);
      }
      props.onToolChange("select");
      return;
    }
    const node = doc.nodes[e.nodeId];
    if (node?.type === "text" && node.text !== value) {
      props.dispatch([{ type: "node.update", nodeId: node.id, patch: { text: value } }], { label: "design.did.editText" });
    }
  }

  function commitComment(e: Extract<Editing, { kind: "comment" }>, value: string): void {
    setEditing(null);
    svgRef.current?.focus();
    if (value.trim() === "") return;
    const id = newId("comment");
    if (props.dispatch(
      [{
        type: "comment.add",
        comment: {
          id, artboardId: e.artboardId, nodeId: e.nodeId, x: Math.round(e.at.x), y: Math.round(e.at.y),
          text: value.trim(), author: props.author, resolved: false, createdAt: new Date().toISOString(),
        },
      }],
      { label: "design.did.comment" },
    )) {
      setThreadId(id);
      props.onToolChange("select");
    }
  }

  function onKeyDown(e: ReactKeyboardEvent<SVGSVGElement>): void {
    if (e.key === " ") {
      spaceHeld.current = true;
      e.preventDefault();
      return;
    }
    const id = matchDesignShortcut(e);
    if (!id) return;
    const handled = runShortcut(id, e.key);
    if (handled) {
      e.preventDefault();
      e.stopPropagation();
    }
  }

  function runShortcut(id: NonNullable<ReturnType<typeof matchDesignShortcut>>, key: string): boolean {
    const asTool = toolForShortcut(id);
    if (asTool) {
      props.onToolChange(asTool);
      props.announce(t("design.announce.tool", { tool: t(`design.tool.${asTool}`) }));
      return true;
    }
    if (isCommand(id)) {
      props.onCommand(id);
      return true;
    }
    switch (id) {
      case "move":
      case "moveFar": {
        const step = id === "moveFar" ? 10 : 1;
        const dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
        const dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
        const ops = moveOps(doc, selection, dx, dy);
        if (ops.length && props.dispatch(ops, { label: "design.did.move", coalesceKey: `nudge:${[...selection].sort().join(",")}` })) {
          const f = doc.nodes[selection[0]]?.frame;
          if (f) props.announce(t("design.announce.position", { x: Math.round(f.x + dx), y: Math.round(f.y + dy) }));
        }
        return selection.length > 0;
      }
      case "selectNext":
      case "selectPrevious": {
        const next = stepSelection(pageOrder(doc, pageId), selection, id === "selectNext" ? 1 : -1);
        if (next) select([next]);
        return true;
      }
      case "selectAll":
        select(pageOrder(doc, pageId));
        return true;
      case "editText":
        if (selection.length !== 1) return false;
        startTextEdit(selection[0]);
        return true;
      case "deselect":
        if (selection.length > 0) select([]);
        else if (tool !== "select") props.onToolChange("select");
        return true;
      default:
        return false;
    }
  }

  function onDoubleClick(): void {
    if (selection.length === 1) startTextEdit(selection[0]);
  }

  const selected = selection.map((id) => doc.nodes[id]).filter((n): n is DesignNode => n !== undefined);
  const selectedWorld = selected.flatMap((node) => {
    const board = page?.artboards.find((b) => b.nodeIds.includes(node.id));
    const o = board ? layout.get(board.id) : undefined;
    return o ? [offset(liveFrame(node, gesture), o.x, o.y)] : [];
  });
  const box = boundsOf(selectedWorld);
  const screenBox = box && (() => {
    const a = toScreen(box, viewport);
    return { x: a.x, y: a.y, width: box.width * viewport.zoom, height: box.height * viewport.zoom };
  })();
  const comments = doc.comments ?? [];
  const anchoredNode = editing?.kind === "comment" && editing.nodeId ? doc.nodes[editing.nodeId] : undefined;
  const anchoredArtboard = editing?.kind === "comment" ? page?.artboards.find((board) => board.id === editing.artboardId) : undefined;
  const anchoredOrigin = anchoredArtboard ? layout.get(anchoredArtboard.id) : undefined;
  const commentScreenPoint = editing?.kind === "comment" && anchoredOrigin
    ? toScreen(anchoredNode ? {
        x: anchoredOrigin.x + anchoredNode.frame.x + anchoredNode.frame.width,
        y: anchoredOrigin.y + anchoredNode.frame.y + anchoredNode.frame.height,
      } : { x: anchoredOrigin.x + editing.at.x, y: anchoredOrigin.y + editing.at.y }, viewport)
    : null;
  const activeThread = comments.find((comment) => comment.id === threadId);
  const threadPage = activeThread && doc.pages.find((candidate) => candidate.artboards.some((board) => board.id === activeThread.artboardId));
  const activeBoard = activeThread && threadPage?.artboards.find((board) => board.id === activeThread.artboardId);
  const activeOrigin = activeBoard && threadPage ? layoutArtboards(threadPage).get(activeBoard.id) : undefined;
  const activePoint = activeThread && activeOrigin
    ? toScreen({ x: activeOrigin.x + activeThread.x, y: activeOrigin.y + activeThread.y }, viewport)
    : null;

  return (
    <div className="relative h-full w-full overflow-hidden bg-surface-2">
      <svg
        ref={svgRef}
        role="application"
        aria-roledescription={t("design.canvas.role")}
        aria-label={t("design.canvas.label", { title: doc.title })}
        aria-describedby="design-canvas-hint"
        tabIndex={0}
        className={cn("h-full w-full touch-none select-none", focusRing, tool === "select" ? "cursor-default" : "cursor-crosshair")}
        onPointerDown={(e) => {
          svgRef.current?.focus();
          pointer.onPointerDown(e);
        }}
        onPointerMove={(e) => {
          pointer.onPointerMove(e);
          const bounds = svgRef.current?.getBoundingClientRect();
          if (bounds) setPointerAt({ x: e.clientX - bounds.left, y: e.clientY - bounds.top });
        }}
        onPointerUp={pointer.onPointerUp}
        onPointerCancel={pointer.onPointerCancel}
        onDoubleClick={onDoubleClick}
        onKeyDown={onKeyDown}
        onKeyUp={(e) => {
          if (e.key === " ") spaceHeld.current = false;
        }}
      >
        <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
          {page?.artboards.map((board) => {
            const o = layout.get(board.id);
            if (!o) return null;
            return (
              <g key={board.id} transform={`translate(${o.x} ${o.y})`} data-artboard-id={board.id}>
                <text x={0} y={-8 / viewport.zoom} fontSize={12 / viewport.zoom} className="fill-muted-foreground">
                  {board.name}
                </text>
                <rect width={board.width} height={board.height} fill={safeColour(board.background)} className="stroke-hairline" strokeWidth={1 / viewport.zoom} />
                <svg width={board.width} height={board.height} overflow="hidden">
                  {board.nodeIds.map((id) => {
                    const node = doc.nodes[id];
                    if (!node) return null;
                    const hidden = editing?.kind === "text" && editing.nodeId === id;
                    return <NodeShape key={id} doc={doc} node={node} frame={liveFrame(node, gesture)} hidden={hidden} />;
                  })}
                </svg>
                {comments.filter((c) => c.artboardId === board.id).map((c, index) => (
                  <g
                    key={c.id}
                    role="button"
                    tabIndex={0}
                    aria-label={t("design.comment.marker", { n: index + 1, text: c.text })}
                    aria-pressed={threadId === c.id}
                    onClick={(e) => { e.stopPropagation(); setThreadId(threadId === c.id ? null : c.id); }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); setThreadId(threadId === c.id ? null : c.id); }
                    }}
                    className="cursor-pointer"
                  >
                    <circle cx={c.x} cy={c.y} r={10 / viewport.zoom} className={c.resolved ? "fill-muted-foreground" : "fill-accent"} />
                    <text x={c.x} y={c.y + 3 / viewport.zoom} textAnchor="middle" fontSize={9 / viewport.zoom} className="fill-accent-ink pointer-events-none">{index + 1}</text>
                  </g>
                ))}
              </g>
            );
          })}
          <GestureOverlay gesture={gesture} layout={layout} zoom={viewport.zoom} doc={doc} />
        </g>
        {screenBox && (
          <g aria-hidden="true">
            <rect {...screenBox} fill="none" className="stroke-accent" strokeWidth={1} />
            {selected.length === 1 && !selected[0].locked &&
              HANDLES.map((h) => {
                const p = handlePoint(screenBox, h);
                return <rect key={h} data-handle={h} x={p.x - 4} y={p.y - 4} width={8} height={8} className="fill-card stroke-accent" strokeWidth={1} />;
              })}
          </g>
        )}
      </svg>
      {tool === "comment" && !editing && (
        <div
          className="pointer-events-none absolute left-3 top-3 z-10 rounded-lg border border-hairline bg-card px-3 py-2 text-sm text-foreground shadow-elev-lg"
          style={pointerAt ? { transform: `translate(${Math.min(Math.max(pointerAt.x - 120, 0), (svgRef.current?.clientWidth ?? 300) - 260)}px, ${Math.max(pointerAt.y - 40, 0)}px)` } : undefined}
        >{t("design.comment.selectElement")}</div>
      )}
      {activeThread && activePoint && (
        <div className="absolute z-20 flex w-64 flex-col gap-2 rounded-lg border border-hairline bg-card p-3 text-sm shadow-elev-lg" style={{ left: Math.min(activePoint.x + 14, Math.max(8, (svgRef.current?.clientWidth ?? 300) - 272)), top: Math.max(8, activePoint.y - 12) }}>
          <p className="break-words">{activeThread.text}</p>
          <p className="text-xs text-muted-foreground">{activeThread.author === props.author ? t("design.comment.you") : activeThread.author} · {new Date(activeThread.createdAt).toLocaleDateString()}</p>
          <button type="button" onClick={() => props.dispatch([{ type: "comment.resolve", commentId: activeThread.id, resolved: !activeThread.resolved }], { label: "design.did.comment" })} className={cn("self-start rounded-lg px-2 py-1 text-accent-ink hover:bg-surface-hover", focusRing)}>{t(activeThread.resolved ? "design.comment.reopen" : "design.comment.resolve")}</button>
        </div>
      )}
      <p id="design-canvas-hint" className="sr-only">{t("design.canvas.hint")}</p>
      <p id="design-editor-hint" className="sr-only">{t("design.editor.hint")}</p>
      {editing && <EditorOverlay editing={editing} doc={doc} layout={layout} viewport={viewport} onText={commitText} onComment={commitComment} commentPoint={commentScreenPoint} />}
    </div>
  );
}

function EditorOverlay({
  editing, doc, layout, viewport, onText, onComment, commentPoint,
}: {
  editing: Editing;
  doc: DesignDocument;
  layout: Map<string, Rect>;
  viewport: Viewport;
  onText: (e: Extract<Editing, { kind: "text" }>, value: string) => void;
  onComment: (e: Extract<Editing, { kind: "comment" }>, value: string) => void;
  commentPoint: Point | null;
}) {
  const t = useT();
  const o = layout.get(editing.artboardId) ?? { x: 0, y: 0 };
  if (editing.kind === "comment") {
    const p = toScreen({ x: o.x + editing.at.x, y: o.y + editing.at.y }, viewport);
    return (
      <CanvasTextEditor
        initial=""
        label={t("design.editor.comment")}
        style={{ left: (commentPoint?.x ?? p.x) + 10, top: (commentPoint?.y ?? p.y) - 10, width: 240, height: 72 }}
        enterCommits
        onCommit={(v) => onComment(editing, v)}
      />
    );
  }
  const node = doc.nodes[editing.nodeId];
  const m = textMetrics(doc, node ?? ({ type: "text" } as DesignNode));
  const p = toScreen({ x: o.x + editing.frame.x, y: o.y + editing.frame.y }, viewport);
  return (
    <CanvasTextEditor
      initial={editing.initial}
      label={t("design.editor.text")}
      style={{
        left: p.x,
        top: p.y,
        width: Math.max(120, editing.frame.width * viewport.zoom),
        height: Math.max(m.size * m.lineHeight * viewport.zoom + 8, editing.frame.height * viewport.zoom),
        fontSize: m.size * viewport.zoom,
        lineHeight: m.lineHeight,
        fontFamily: m.family,
        fontWeight: m.weight,
      }}
      onCommit={(v) => onText(editing, v)}
    />
  );
}

/** Guides, the marquee and the shape being drawn — drawn in world space, chrome colours only. */
function GestureOverlay({ gesture, layout, zoom, doc }: { gesture: Gesture | null; layout: Map<string, Rect>; zoom: number; doc: DesignDocument }) {
  if (!gesture) return null;
  const hair = 1 / zoom;
  if (gesture.kind === "marquee") {
    const r = rectFromPoints(gesture.start, gesture.current);
    return <rect data-testid="design-marquee" {...r} className="fill-accent/10 stroke-accent" strokeWidth={hair} />;
  }
  if (gesture.kind === "move" && gesture.guides.length) {
    const o = layout.get(gesture.artboardId);
    const board = doc.pages.flatMap((p) => p.artboards).find((b) => b.id === gesture.artboardId);
    if (!o || !board) return null;
    return (
      <g transform={`translate(${o.x} ${o.y})`} className="stroke-accent" strokeWidth={hair}>
        {gesture.guides.map((g, i) =>
          g.axis === "x"
            ? <line key={i} x1={g.at} x2={g.at} y1={0} y2={board.height} />
            : <line key={i} y1={g.at} y2={g.at} x1={0} x2={board.width} />,
        )}
      </g>
    );
  }
  if (gesture.kind === "create" || gesture.kind === "draw") {
    const o = layout.get(gesture.artboardId) ?? { x: 0, y: 0 };
    if (gesture.kind === "draw") {
      return <polyline transform={`translate(${o.x} ${o.y})`} points={gesture.points.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" className="stroke-accent" strokeWidth={2 * hair} />;
    }
    const r = rectFromPoints(gesture.start, gesture.current);
    return (
      <g transform={`translate(${o.x} ${o.y})`} className="stroke-accent" strokeWidth={hair} fill="none">
        {gesture.tool === "line" ? <line x1={gesture.start.x} y1={gesture.start.y} x2={gesture.current.x} y2={gesture.current.y} />
          : gesture.tool === "ellipse" ? <ellipse cx={r.x + r.width / 2} cy={r.y + r.height / 2} rx={r.width / 2} ry={r.height / 2} />
          : <rect {...r} />}
      </g>
    );
  }
  return null;
}
