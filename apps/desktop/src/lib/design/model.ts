/**
 * Chimera Design — the document model, as the desktop sees it.
 *
 * This mirrors the domain the server owns (`chimera/design/`, ported from the handoff's
 * `master-design-core.ts`). The server is the source of truth; the client keeps the same shapes
 * so it can apply an edit optimistically and undo it without a round trip. Field names are the
 * wire names (camelCase, as in the handoff), so a document read from the API needs no mapping.
 *
 * Additions over the handoff core, which the Python side mirrors:
 *  - `ShapeNode.shape` also takes `"path"` (the freehand tool's output), and a shape may carry
 *    `points`: a flat `[x0, y0, x1, y1, …]` list NORMALISED to the frame (0 = left/top edge,
 *    1 = right/bottom edge), so resizing the frame scales the drawing. A `line` uses two points to
 *    keep its direction; without them it runs from the frame's top-left to bottom-right.
 *  - `DesignDocument.comments` and the three `comment.*` operations (comments pinned to an
 *    artboard point and optionally to an element).
 *  - `artboard.update` (rename, resize, change the background).
 *  - `node.add` takes an optional `index` (z-order slot), so undoing a delete puts the element
 *    back where it was rather than on top.
 *  - In a `node.update` patch, `null` removes an optional field. That is how the inverse of a
 *    patch that ADDED a field (say, `fontToken`) takes it away again.
 *
 *  - No `ownerId`. The handoff's core carried one; Chimera serves one owner and the server's model has
 *    no such field (it refuses unknown keys), so a document read from the API has none and one sent
 *    with it would be refused.
 *
 * Colours are DATA. They arrive in the document and are applied as SVG attributes only after
 * `isHexColour` accepts them; nothing in the code base writes a colour literal.
 */

export type NodeId = string;
export type PageId = string;
export type ArtboardId = string;

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
}

export interface TypographyToken {
  family: string;
  size: number;
  weight: number;
  lineHeight?: number;
}

export interface DesignTokens {
  colors: Record<string, string>;
  typography: Record<string, TypographyToken>;
  spacing: Record<string, number>;
  radii: Record<string, number>;
  /** The server's semantic layer (`button.primary -> {fill: "primary"}`), by token name. The desktop
   *  does not edit it; it is carried through untouched so a `tokens.replace` does not drop it. */
  components?: Record<string, Record<string, string>>;
}

export interface NodeStyle {
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  opacity?: number;
  radius?: number;
}

interface NodeBase {
  id: NodeId;
  name: string;
  frame: Rect;
  parentId?: NodeId;
  locked?: boolean;
  visible: boolean;
  style?: NodeStyle;
  linkTo?: ArtboardId;
}

export interface TextNode extends NodeBase {
  type: "text";
  text: string;
  fontToken?: string;
  colorToken?: string;
  align?: "left" | "center" | "right";
}

export type ShapeKind = "rectangle" | "ellipse" | "line" | "path";

export interface ShapeNode extends NodeBase {
  type: "shape";
  shape: ShapeKind;
  /** `[x0, y0, x1, y1, …]` normalised to the frame (0..1); a path's vertices, a line's two ends. */
  points?: number[];
}

export interface ImageNode extends NodeBase {
  type: "image";
  /** An internal asset id, never a URL a model wrote. */
  assetId: string;
  alt: string;
  fit: "contain" | "cover";
}

export interface GroupNode extends NodeBase {
  type: "group";
  childIds: NodeId[];
}

export type DesignNode = TextNode | ShapeNode | ImageNode | GroupNode;
export type NodeType = DesignNode["type"];

export interface Artboard {
  id: ArtboardId;
  name: string;
  width: number;
  height: number;
  background: string;
  nodeIds: NodeId[];
  /** Generated index entry: render a live thumbnail of this source artboard. */
  thumbnailOf?: ArtboardId;
}

export interface DesignPage {
  id: PageId;
  name: string;
  artboards: Artboard[];
  /** The generated index page. Documents saved before this field existed name it `LEGACY_INDEX_NAME`. */
  isIndex?: boolean;
}

/** The name an index page carried before `isIndex` existed. Still recognised, so old designs keep working. */
export const LEGACY_INDEX_NAME = "Índice";

export function isIndexPage(page: DesignPage): boolean {
  return page.isIndex === true || page.name === LEGACY_INDEX_NAME;
}

export interface DesignComment {
  id: string;
  artboardId: ArtboardId;
  /** The element the comment is about, when it is about one. */
  nodeId?: NodeId;
  /** Pin position in the artboard's coordinates. */
  x: number;
  y: number;
  text: string;
  author: string;
  resolved: boolean;
  createdAt: string;
}

export interface DesignDocument {
  schemaVersion: 1;
  id: string;
  title: string;
  revision: number;
  updatedAt: string;
  designSystemId?: string;
  tokens: DesignTokens;
  pages: DesignPage[];
  nodes: Record<NodeId, DesignNode>;
  comments: DesignComment[];
}

/** A node patch: any field but `id` and `type`; `null` removes an optional field. */
export type NodePatch = { [key: string]: unknown };

export type ArtboardPatch = Partial<Pick<Artboard, "name" | "width" | "height" | "background">>;

export type DesignOperation =
  | { type: "document.rename"; title: string }
  | { type: "page.add"; page: DesignPage }
  | { type: "page.delete"; pageId: PageId }
  | { type: "artboard.add"; pageId: PageId; artboard: Artboard }
  | { type: "artboard.update"; pageId: PageId; artboardId: ArtboardId; patch: ArtboardPatch }
  | { type: "artboard.delete"; pageId: PageId; artboardId: ArtboardId }
  | {
      type: "node.add";
      pageId: PageId;
      artboardId: ArtboardId;
      node: DesignNode;
      index?: number;
      /** Place it inside this group (on the same artboard) rather than on the artboard itself. */
      parentId?: NodeId;
      /** A group's descendants, in any order; its `childIds` must reach exactly these. */
      children?: DesignNode[];
    }
  | { type: "node.update"; nodeId: NodeId; patch: NodePatch }
  | { type: "node.move"; nodeId: NodeId; frame: Rect }
  | { type: "node.delete"; nodeId: NodeId }
  | { type: "tokens.replace"; tokens: DesignTokens }
  | { type: "comment.add"; comment: DesignComment }
  | { type: "comment.resolve"; commentId: string; resolved: boolean }
  | { type: "comment.delete"; commentId: string };

export type OperationType = DesignOperation["type"];

export const LIMITS = {
  title: 160,
  nodeName: 120,
  text: 20_000,
  comment: 2_000,
  dimension: 20_000,
  minDimension: 1,
  pathPoints: 4_000,
  operations: 500,
} as const;

const HEX_COLOUR = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/;
const SAFE_ID = /^[A-Za-z0-9_-]{1,100}$/;

/** The one gate a colour passes before it reaches an SVG attribute or a style. */
export function isHexColour(value: unknown): value is string {
  return typeof value === "string" && HEX_COLOUR.test(value);
}

/** The colour if it is a valid hex, otherwise undefined — never the raw string. */
export function safeColour(value: unknown): string | undefined {
  return isHexColour(value) ? value : undefined;
}

export function isSafeId(value: unknown): value is string {
  return typeof value === "string" && SAFE_ID.test(value);
}

/** An id the app generated: a prefix and 12 random base-36 characters. */
export function newId(prefix: string): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += (b % 36).toString(36);
  return `${prefix}_${out}`;
}

export function findArtboard(
  doc: DesignDocument,
  artboardId: ArtboardId,
): { page: DesignPage; artboard: Artboard } | undefined {
  for (const page of doc.pages) {
    const artboard = page.artboards.find((candidate) => candidate.id === artboardId);
    if (artboard) return { page, artboard };
  }
  return undefined;
}

/** The artboard whose top-level list holds `nodeId`, with the node's slot in it. */
export function locateNode(
  doc: DesignDocument,
  nodeId: NodeId,
): { page: DesignPage; artboard: Artboard; index: number } | undefined {
  for (const page of doc.pages) {
    for (const artboard of page.artboards) {
      const index = artboard.nodeIds.indexOf(nodeId);
      if (index >= 0) return { page, artboard, index };
    }
  }
  return undefined;
}

/** The group whose `childIds` holds `nodeId`, if it lives inside one. */
export function parentGroup(doc: DesignDocument, nodeId: NodeId): GroupNode | undefined {
  for (const node of Object.values(doc.nodes)) {
    if (node.type === "group" && node.childIds.includes(nodeId)) return node;
  }
  return undefined;
}

/** The page and artboard an element is on, walking up through the groups that contain it. */
export function placeOf(
  doc: DesignDocument,
  nodeId: NodeId,
): { page: DesignPage; artboard: Artboard } | undefined {
  let current = nodeId;
  for (let hops = 0; hops <= Object.keys(doc.nodes).length; hops += 1) {
    const at = locateNode(doc, current);
    if (at) return { page: at.page, artboard: at.artboard };
    const parent = parentGroup(doc, current);
    if (!parent) return undefined;
    current = parent.id;
  }
  return undefined; // a ring of groups, which the server's validator refuses anyway
}

/** `rootId` and everything inside it, parents before children (the server's `_tree.subtree`). */
export function subtreeIds(doc: DesignDocument, rootId: NodeId): NodeId[] {
  const out: NodeId[] = [];
  const stack = [rootId];
  const seen = new Set<NodeId>();
  while (stack.length > 0) {
    const id = stack.pop() as NodeId;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
    const node = doc.nodes[id];
    if (node?.type === "group") stack.push(...[...node.childIds].reverse());
  }
  return out;
}

/** A colour token's value, validated. */
export function tokenColour(doc: DesignDocument, token: string | undefined): string | undefined {
  if (token === undefined) return undefined;
  return safeColour(doc.tokens.colors[token]);
}

/** The colour tokens that pass validation, in declaration order. */
export function colourTokens(doc: DesignDocument): [string, string][] {
  return Object.entries(doc.tokens.colors).filter(([, value]) => isHexColour(value));
}

/** The first valid colour among the named tokens, for defaults that must come from the document. */
export function pickColour(doc: DesignDocument, names: readonly string[]): string | undefined {
  for (const name of names) {
    const value = tokenColour(doc, name);
    if (value) return value;
  }
  return undefined;
}

export function emptyTokens(): DesignTokens {
  return { colors: {}, typography: {}, spacing: {}, radii: {} };
}
