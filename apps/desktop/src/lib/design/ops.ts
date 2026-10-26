/**
 * Client-side operations: the optimistic half of every edit.
 *
 * The canvas applies a batch here first, so the person sees the change at once and gets back the
 * inverse batch that undoes it. The server repeats every check (it owns the document); these exist
 * so an invalid edit is refused BEFORE it reaches the screen — in particular, so a colour that is
 * not a strict hex never reaches an SVG attribute.
 *
 * Same operation names and shapes as the domain (`model.ts` lists the additions). A batch is
 * atomic: it applies to a clone and the first refusal throws, leaving the input untouched.
 */
import {
  LIMITS,
  findArtboard,
  isHexColour,
  isSafeId,
  parentGroup,
  placeOf,
  subtreeIds,
  type ArtboardPatch,
  type DesignDocument,
  type DesignNode,
  type DesignOperation,
  type DesignTokens,
  type NodePatch,
  type Rect,
} from "@/lib/design/model";

/** Why a batch was refused. `code` is stable; the message is for logs, not for the screen. */
export class DesignOpError extends Error {
  constructor(readonly code: string, message: string = code) {
    super(message);
    this.name = "DesignOpError";
  }
}

export interface ApplyResult {
  document: DesignDocument;
  /** The batch that undoes this one, already in the order it must be applied. */
  inverse: DesignOperation[];
}

export interface ApplyOptions {
  now?: () => Date;
  maxOperations?: number;
}

function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw new DesignOpError(code);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function finite(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

function checkDimension(n: unknown): void {
  check(finite(n) && n >= LIMITS.minDimension && n <= LIMITS.dimension, "dimension");
}

export function checkFrame(frame: Rect): void {
  check(frame !== null && typeof frame === "object", "frame");
  check(finite(frame.x) && finite(frame.y), "position");
  checkDimension(frame.width);
  checkDimension(frame.height);
  if (frame.rotation !== undefined) check(finite(frame.rotation), "rotation");
}

function checkText(text: unknown, max: number): void {
  check(typeof text === "string" && text.length <= max, "text");
}

function checkOptionalColour(value: unknown): void {
  if (value !== undefined) check(isHexColour(value), "colour");
}

function checkStyle(node: DesignNode): void {
  const style = node.style;
  if (style === undefined) return;
  check(typeof style === "object" && style !== null, "style");
  checkOptionalColour(style.fill);
  checkOptionalColour(style.stroke);
  if (style.opacity !== undefined) {
    check(finite(style.opacity) && style.opacity >= 0 && style.opacity <= 1, "opacity");
  }
  if (style.strokeWidth !== undefined) {
    check(finite(style.strokeWidth) && style.strokeWidth >= 0 && style.strokeWidth <= 1_000, "strokeWidth");
  }
  if (style.radius !== undefined) {
    check(finite(style.radius) && style.radius >= 0 && style.radius <= LIMITS.dimension, "radius");
  }
}

const SHAPES = new Set(["rectangle", "ellipse", "line", "path"]);

export function checkNode(node: DesignNode): void {
  check(isSafeId(node.id), "id");
  if (node.linkTo !== undefined) check(isSafeId(node.linkTo), "linkTo");
  checkText(node.name, LIMITS.nodeName);
  checkFrame(node.frame);
  check(typeof node.visible === "boolean", "visible");
  if (node.locked !== undefined) check(typeof node.locked === "boolean", "locked");
  checkStyle(node);
  switch (node.type) {
    case "text":
      checkText(node.text, LIMITS.text);
      if (node.align !== undefined) check(["left", "center", "right"].includes(node.align), "align");
      break;
    case "shape":
      check(SHAPES.has(node.shape), "shape");
      if (node.points !== undefined) {
        check(Array.isArray(node.points) && node.points.length % 2 === 0, "points");
        check(node.points.length <= LIMITS.pathPoints * 2 && node.points.every(finite), "points");
      }
      break;
    case "image":
      check(isSafeId(node.assetId), "assetId");
      checkText(node.alt, 1_000);
      check(node.fit === "cover" || node.fit === "contain", "fit");
      break;
    case "group":
      check(Array.isArray(node.childIds) && node.childIds.every(isSafeId), "childIds");
      break;
    default:
      throw new DesignOpError("nodeType");
  }
}

export function checkTokens(tokens: DesignTokens): void {
  check(tokens !== null && typeof tokens === "object", "tokens");
  for (const [name, value] of Object.entries(tokens.colors ?? {})) {
    checkText(name, 100);
    check(isHexColour(value), "colour");
  }
  for (const [name, value] of Object.entries(tokens.typography ?? {})) {
    checkText(name, 100);
    checkText(value.family, 200);
    check(finite(value.size) && value.size > 0 && value.size <= 256, "fontSize");
    check(Number.isInteger(value.weight) && value.weight >= 100 && value.weight <= 900, "fontWeight");
  }
  for (const [name, value] of [...Object.entries(tokens.spacing ?? {}), ...Object.entries(tokens.radii ?? {})]) {
    checkText(name, 100);
    check(finite(value) && value >= 0 && value <= LIMITS.dimension, "tokenNumber");
  }
}

const COMMON_KEYS = ["name", "frame", "parentId", "locked", "visible", "style", "linkTo"];
const PATCH_KEYS: Record<DesignNode["type"], readonly string[]> = {
  text: [...COMMON_KEYS, "text", "fontToken", "colorToken", "align"],
  shape: [...COMMON_KEYS, "shape", "points"],
  image: [...COMMON_KEYS, "assetId", "alt", "fit"],
  group: [...COMMON_KEYS, "childIds"],
};
/** Required fields a patch may change but never remove. */
const REQUIRED = new Set(["name", "frame", "visible", "text", "shape", "assetId", "alt", "fit", "childIds"]);

function allArtboardIds(doc: DesignDocument): Set<string> {
  return new Set(doc.pages.flatMap((page) => page.artboards.map((board) => board.id)));
}

/**
 * Admit a new element and, for a group, the descendants it brings in `children`: every id fresh,
 * every node valid, and the group's `childIds` reaching exactly the nodes supplied — no stray child
 * left placed nowhere, none borrowed from elsewhere in the document. The server's `_admit_subtree`.
 */
function admitSubtree(doc: DesignDocument, root: DesignNode, children: readonly DesignNode[], created: Set<string>): DesignNode[] {
  const incoming = new Map<string, DesignNode>();
  for (const node of [root, ...children]) {
    checkNode(node);
    check(!doc.nodes[node.id] && !created.has(node.id) && !incoming.has(node.id), "duplicateNode");
    incoming.set(node.id, node);
  }
  const reached = new Set<string>();
  const stack = [root.id];
  while (stack.length > 0) {
    const id = stack.pop() as string;
    const node = incoming.get(id);
    check(node && !reached.has(id), "childIds");
    reached.add(id);
    if (node.type === "group") stack.push(...node.childIds);
  }
  check(reached.size === incoming.size, "childIds");
  return [...incoming.values()];
}

function applyNodeUpdate(doc: DesignDocument, nodeId: string, patch: NodePatch): DesignOperation {
  const node = doc.nodes[nodeId];
  check(node, "nodeNotFound");
  check(!node.locked || Object.keys(patch).every((k) => k === "locked"), "locked");
  check(patch !== null && typeof patch === "object", "patch");
  const allowed = PATCH_KEYS[node.type];
  const record = node as unknown as Record<string, unknown>;
  const before: NodePatch = {};
  for (const [key, value] of Object.entries(patch)) {
    check(allowed.includes(key), "patchField");
    check(!(value === null && REQUIRED.has(key)), "patchRemovesRequired");
    before[key] = key in record ? clone(record[key]) : null;
    if (value === null) delete record[key];
    else record[key] = clone(value);
  }
  checkNode(node);
  return { type: "node.update", nodeId, patch: before };
}

function applyOne(doc: DesignDocument, op: DesignOperation, created: Set<string>): DesignOperation {
  switch (op.type) {
    case "document.rename": {
      checkText(op.title, LIMITS.title);
      check(op.title.trim().length > 0, "emptyTitle");
      const before = doc.title;
      doc.title = op.title.trim();
      return { type: "document.rename", title: before };
    }
    case "page.add": {
      check(isSafeId(op.page.id), "id");
      check(!doc.pages.some((page) => page.id === op.page.id), "duplicatePage");
      checkText(op.page.name, LIMITS.nodeName);
      const existing = allArtboardIds(doc);
      for (const board of op.page.artboards) {
        check(isSafeId(board.id) && !existing.has(board.id), "duplicateArtboard");
        checkDimension(board.width);
        checkDimension(board.height);
        check(isHexColour(board.background), "colour");
        check(board.nodeIds.length === 0, "artboardNotEmpty");
      }
      doc.pages.push(clone(op.page));
      return { type: "page.delete", pageId: op.page.id };
    }
    case "page.delete": {
      const index = doc.pages.findIndex((page) => page.id === op.pageId);
      check(index >= 0, "pageNotFound");
      const page = doc.pages[index];
      check(page.artboards.every((board) => board.nodeIds.length === 0), "pageNotEmpty");
      doc.pages.splice(index, 1);
      return { type: "page.add", page };
    }
    case "artboard.add": {
      const page = doc.pages.find((item) => item.id === op.pageId);
      check(page, "pageNotFound");
      check(isSafeId(op.artboard.id) && !allArtboardIds(doc).has(op.artboard.id), "duplicateArtboard");
      checkText(op.artboard.name, LIMITS.nodeName);
      checkDimension(op.artboard.width);
      checkDimension(op.artboard.height);
      check(isHexColour(op.artboard.background), "colour");
      check(op.artboard.nodeIds.length === 0, "artboardNotEmpty");
      page.artboards.push(clone(op.artboard));
      return { type: "artboard.delete", pageId: page.id, artboardId: op.artboard.id };
    }
    case "artboard.update": {
      const found = findArtboard(doc, op.artboardId);
      check(found && found.page.id === op.pageId, "artboardNotFound");
      const { artboard } = found;
      const before: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(op.patch)) {
        check(["name", "width", "height", "background"].includes(key), "patchField");
        if (key === "name") checkText(value, LIMITS.nodeName);
        if (key === "width" || key === "height") checkDimension(value);
        if (key === "background") check(isHexColour(value), "colour");
        const record = artboard as unknown as Record<string, unknown>;
        before[key] = record[key];
        record[key] = value;
      }
      return { type: "artboard.update", pageId: op.pageId, artboardId: op.artboardId, patch: before as ArtboardPatch };
    }
    case "artboard.delete": {
      const page = doc.pages.find((item) => item.id === op.pageId);
      check(page, "pageNotFound");
      const index = page.artboards.findIndex((board) => board.id === op.artboardId);
      check(index >= 0, "artboardNotFound");
      const artboard = page.artboards[index];
      check(artboard.nodeIds.length === 0, "artboardNotEmpty");
      page.artboards.splice(index, 1);
      return { type: "artboard.add", pageId: page.id, artboard };
    }
    case "node.add": {
      const found = findArtboard(doc, op.artboardId);
      check(found && found.page.id === op.pageId, "artboardNotFound");
      const nodes = admitSubtree(doc, op.node, op.children ?? [], created);
      let ids = found.artboard.nodeIds;
      if (op.parentId !== undefined) {
        // Into a group, as the server allows: it must be a group, on this artboard, and unlocked.
        const parent = doc.nodes[op.parentId];
        check(parent && parent.type === "group", "parentNotGroup");
        check(placeOf(doc, parent.id)?.artboard.id === op.artboardId, "parentNotOnArtboard");
        check(!parent.locked, "locked");
        ids = parent.childIds;
      }
      for (const node of nodes) {
        created.add(node.id);
        doc.nodes[node.id] = clone(node);
      }
      const slot = op.index === undefined ? ids.length : Math.max(0, Math.min(ids.length, Math.trunc(op.index)));
      ids.splice(slot, 0, op.node.id);
      return { type: "node.delete", nodeId: op.node.id };
    }
    case "node.update":
      return applyNodeUpdate(doc, op.nodeId, op.patch);
    case "node.move": {
      const node = doc.nodes[op.nodeId];
      check(node, "nodeNotFound");
      check(!node.locked, "locked");
      checkFrame(op.frame);
      const before = clone(node.frame);
      node.frame = clone(op.frame);
      return { type: "node.move", nodeId: node.id, frame: before };
    }
    case "node.delete": {
      // As on the server: a group goes with everything inside it, and an element inside a group can
      // be deleted on its own. The inverse carries the slot, the parent group and the descendants,
      // so undo puts the whole subtree back exactly where it was.
      const node = doc.nodes[op.nodeId];
      check(node, "nodeNotFound");
      const ids = subtreeIds(doc, node.id);
      check(ids.every((id) => !doc.nodes[id]?.locked), "locked");
      const place = placeOf(doc, node.id);
      check(place, "nodeNotPlaced");
      const parent = parentGroup(doc, node.id);
      const siblings = parent ? parent.childIds : place.artboard.nodeIds;
      const index = siblings.indexOf(node.id);
      siblings.splice(index, 1);
      const removed = ids.map((id) => {
        const snapshot = clone(doc.nodes[id]);
        delete doc.nodes[id];
        return snapshot;
      });
      const inverse: Extract<DesignOperation, { type: "node.add" }> = { type: "node.add", pageId: place.page.id, artboardId: place.artboard.id, node: removed[0], index };
      if (parent) inverse.parentId = parent.id;
      if (removed.length > 1) inverse.children = removed.slice(1);
      return inverse;
    }
    case "tokens.replace": {
      checkTokens(op.tokens);
      const before = clone(doc.tokens);
      doc.tokens = clone(op.tokens);
      return { type: "tokens.replace", tokens: before };
    }
    case "comment.add": {
      const c = op.comment;
      check(isSafeId(c.id) && !doc.comments.some((x) => x.id === c.id), "duplicateComment");
      check(findArtboard(doc, c.artboardId), "artboardNotFound");
      if (c.nodeId !== undefined) check(isSafeId(c.nodeId), "id");
      check(finite(c.x) && finite(c.y), "position");
      checkText(c.text, LIMITS.comment);
      check(c.text.trim().length > 0, "emptyComment");
      checkText(c.author, 200);
      check(typeof c.resolved === "boolean", "resolved");
      doc.comments.push(clone(c));
      return { type: "comment.delete", commentId: c.id };
    }
    case "comment.resolve": {
      const comment = doc.comments.find((c) => c.id === op.commentId);
      check(comment, "commentNotFound");
      check(typeof op.resolved === "boolean", "resolved");
      const before = comment.resolved;
      comment.resolved = op.resolved;
      return { type: "comment.resolve", commentId: comment.id, resolved: before };
    }
    case "comment.delete": {
      const index = doc.comments.findIndex((c) => c.id === op.commentId);
      check(index >= 0, "commentNotFound");
      const [comment] = doc.comments.splice(index, 1);
      return { type: "comment.add", comment };
    }
    default:
      throw new DesignOpError("unknownOperation");
  }
}

/** A link or thumbnail may only point at an artboard that exists after the batch. */
function checkReferences(doc: DesignDocument): void {
  const boardIds = allArtboardIds(doc);
  for (const node of Object.values(doc.nodes)) {
    if (node.linkTo !== undefined) check(boardIds.has(node.linkTo), "linkTo");
  }
  for (const page of doc.pages) {
    for (const board of page.artboards) {
      if (board.thumbnailOf !== undefined) check(boardIds.has(board.thumbnailOf), "thumbnailOf");
    }
  }
}

/** Apply a batch atomically. Throws `DesignOpError` and leaves `source` untouched on refusal. */
export function applyOps(
  source: DesignDocument,
  ops: readonly DesignOperation[],
  options: ApplyOptions = {},
): ApplyResult {
  check(Array.isArray(ops), "batch");
  check(ops.length <= (options.maxOperations ?? LIMITS.operations), "tooManyOperations");
  const doc = clone(source);
  doc.comments ??= [];
  const inverse: DesignOperation[] = [];
  const created = new Set<string>();
  for (const op of ops) inverse.unshift(applyOne(doc, op, created));
  checkReferences(doc);
  if (ops.length > 0) {
    doc.revision += 1;
    doc.updatedAt = (options.now ?? (() => new Date()))().toISOString();
  }
  return { document: doc, inverse };
}
