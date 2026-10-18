/**
 * Intent → operation batch. What the toolbar, the inspector and the keyboard ask for, written once
 * so the three cannot drift. Each returns operations only; `applyOps` decides whether they hold.
 *
 * Locked elements are skipped rather than failing the whole batch: "delete the selection" with one
 * locked element in it should delete the rest, not refuse everything.
 */
import { align, roundFrame, type AlignMode } from "@/lib/design/geometry";
import { locateNode, newId, type DesignDocument, type DesignNode, type DesignOperation, type Rect } from "@/lib/design/model";

function editable(doc: DesignDocument, ids: readonly string[]): DesignNode[] {
  return ids.map((id) => doc.nodes[id]).filter((node): node is DesignNode => node !== undefined && !node.locked);
}

export function moveOps(doc: DesignDocument, ids: readonly string[], dx: number, dy: number): DesignOperation[] {
  if (dx === 0 && dy === 0) return [];
  return editable(doc, ids).map((node) => ({
    type: "node.move",
    nodeId: node.id,
    frame: { ...node.frame, x: node.frame.x + dx, y: node.frame.y + dy },
  }));
}

export function frameOps(frames: ReadonlyMap<string, Rect>, doc: DesignDocument): DesignOperation[] {
  const ops: DesignOperation[] = [];
  for (const [id, frame] of frames) {
    const node = doc.nodes[id];
    if (!node || node.locked) continue;
    const next = roundFrame(frame);
    const f = node.frame;
    if (next.x === f.x && next.y === f.y && next.width === f.width && next.height === f.height) continue;
    ops.push({ type: "node.move", nodeId: id, frame: next });
  }
  return ops;
}

export function deleteOps(doc: DesignDocument, ids: readonly string[]): DesignOperation[] {
  return editable(doc, ids)
    .filter((node) => locateNode(doc, node.id) !== undefined)
    .map((node) => ({ type: "node.delete", nodeId: node.id }));
}

/** Copies placed 10px down and right, right above each original. Returns the copies' ids too. */
export function duplicateOps(doc: DesignDocument, ids: readonly string[]): { ops: DesignOperation[]; ids: string[] } {
  const ops: DesignOperation[] = [];
  const copies: string[] = [];
  for (const id of ids) {
    const node = doc.nodes[id];
    const where = locateNode(doc, id);
    if (!node || !where || node.type === "group") continue;
    const copy = JSON.parse(JSON.stringify(node)) as DesignNode;
    copy.id = newId("node");
    // A copy of a locked element starts unlocked: duplicating is how a person gets an editable one.
    delete copy.locked;
    copy.frame = { ...copy.frame, x: copy.frame.x + 10, y: copy.frame.y + 10 };
    ops.push({ type: "node.add", pageId: where.page.id, artboardId: where.artboard.id, node: copy });
    copies.push(copy.id);
  }
  return { ops, ids: copies };
}

/**
 * Align the selection. One element aligns to its artboard; several align to their shared bounds,
 * and only when they sit on the same artboard (frames are artboard-local, so comparing frames from
 * two artboards would align them to a box that exists on neither).
 */
export function alignOps(doc: DesignDocument, ids: readonly string[], mode: AlignMode): DesignOperation[] {
  const nodes = editable(doc, ids);
  if (nodes.length === 0) return [];
  const boards = new Set(nodes.map((node) => locateNode(doc, node.id)?.artboard.id));
  if (boards.size !== 1) return [];
  const board = locateNode(doc, nodes[0].id)?.artboard;
  const container = board ? { x: 0, y: 0, width: board.width, height: board.height } : undefined;
  const aligned = align(nodes.map((node) => node.frame), mode, container);
  return frameOps(new Map(nodes.map((node, i) => [node.id, aligned[i]])), doc);
}

/** Every top-level node on the page, in paint order — the order keyboard selection walks. */
export function pageOrder(doc: DesignDocument, pageId: string | undefined): string[] {
  const page = doc.pages.find((p) => p.id === pageId);
  return page ? page.artboards.flatMap((board) => board.nodeIds.filter((id) => doc.nodes[id]?.visible)) : [];
}

/** The next (or previous) node after the current selection, wrapping around. */
export function stepSelection(order: readonly string[], selection: readonly string[], direction: 1 | -1): string | undefined {
  if (order.length === 0) return undefined;
  const anchor = direction === 1 ? selection[selection.length - 1] : selection[0];
  const at = anchor === undefined ? -1 : order.indexOf(anchor);
  if (at < 0) return direction === 1 ? order[0] : order[order.length - 1];
  return order[(at + direction + order.length) % order.length];
}
