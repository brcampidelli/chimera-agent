/**
 * The Editor talking to the agent: what a selection is, what the agent proposed, and what changed.
 *
 * Pure functions, kept apart from the screen so the parts that decide what is sent and what is
 * written can be tested without a CodeMirror view or a server.
 */

/** Lines selected in the Editor, whole lines, as they are sent with a turn. */
export interface LineSelection {
  path: string;
  /** 1-based and inclusive, as the gutter numbers them. */
  startLine: number;
  endLine: number;
  /** The text of those lines, without the final line break. */
  text: string;
}

/** What the agent is asked to do with the selection. */
export type SelectionIntent = "ask" | "change";

/**
 * The whole lines a selection touches, or null for an empty one.
 *
 * Whole lines, not the characters: "change this" replaces lines, and a proposal for half a line
 * would have to be spliced into the middle of the person's text — the place an automatic edit is
 * hardest to read in a diff. A selection that ends at the very start of a line (a drag that stopped
 * on the next line's first column) does not take that line.
 */
export function wholeLines(
  doc: string,
  from: number,
  to: number,
  path: string,
): LineSelection | null {
  if (from === to) return null;
  const lo = Math.min(from, to);
  let hi = Math.max(from, to);
  if (hi > lo && doc[hi - 1] === "\n") hi -= 1;
  const startLine = lineAt(doc, lo);
  const endLine = Math.max(startLine, lineAt(doc, hi));
  const lines = doc.split("\n");
  return { path, startLine, endLine, text: lines.slice(startLine - 1, endLine).join("\n") };
}

/** The 1-based line number at `offset`. */
function lineAt(doc: string, offset: number): number {
  let n = 1;
  for (let i = 0; i < offset && i < doc.length; i++) if (doc[i] === "\n") n++;
  return n;
}

/** The text of lines `start`..`end` (1-based, inclusive) of `doc`. */
export function linesOf(doc: string, start: number, end: number): string {
  return doc.split("\n").slice(start - 1, end).join("\n");
}

/** `doc` with lines `start`..`end` replaced by `replacement`. */
export function replaceLines(doc: string, start: number, end: number, replacement: string): string {
  const lines = doc.split("\n");
  return [...lines.slice(0, start - 1), ...replacement.split("\n"), ...lines.slice(end)].join("\n");
}

/**
 * The replacement a "change this" answer proposes: the body of its first fenced code block.
 *
 * Null when there is none. The server asks for exactly one block (`chimera/api/proposed_change.py`)
 * and the screen does not guess beyond that: an answer of prose with no block is shown as prose, and
 * nothing is offered to apply — lifting "the code part" out of free text is how an editor ends up
 * writing a sentence into someone's file.
 */
export function proposalOf(answer: string): string | null {
  const match = /```[^\n`]*\n([\s\S]*?)\n?```/.exec(answer);
  return match ? match[1] : null;
}

/** One line of a diff: kept, taken out, or put in. */
export interface DiffLine {
  kind: "same" | "del" | "add";
  text: string;
}

/** Above this many lines on either side the middle is shown as all-out, all-in rather than aligned. */
const ALIGN_LIMIT = 1500;

/**
 * A line diff of `before` → `after`, for reading, not for patching.
 *
 * The common head and tail are trimmed first — an agent's edit to a large file is usually a few
 * lines in the middle — and only what is left is aligned (longest common subsequence). A middle too
 * large to align cheaply is shown as removed-then-added, which is longer to read but never wrong.
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);
  const out: DiffLine[] = a.slice(0, head).map((text) => ({ kind: "same", text }));
  out.push(...alignMiddle(midA, midB));
  out.push(...a.slice(a.length - tail).map((text): DiffLine => ({ kind: "same", text })));
  return out;
}

function alignMiddle(a: string[], b: string[]): DiffLine[] {
  if (a.length > ALIGN_LIMIT || b.length > ALIGN_LIMIT) {
    return [...a.map((text): DiffLine => ({ kind: "del", text })), ...b.map((text): DiffLine => ({ kind: "add", text }))];
  }
  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:].
  const lcs = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "del", text: a[i++] });
    } else {
      out.push({ kind: "add", text: b[j++] });
    }
  }
  while (i < a.length) out.push({ kind: "del", text: a[i++] });
  while (j < b.length) out.push({ kind: "add", text: b[j++] });
  return out;
}

/** Only the changed lines and `context` lines around each, with `null` where lines were skipped. */
export function hunks(diff: readonly DiffLine[], context = 2): (DiffLine | null)[] {
  const keep = diff.map(() => false);
  diff.forEach((line, i) => {
    if (line.kind === "same") return;
    for (let k = Math.max(0, i - context); k <= Math.min(diff.length - 1, i + context); k++) keep[k] = true;
  });
  const out: (DiffLine | null)[] = [];
  diff.forEach((line, i) => {
    if (keep[i]) out.push(line);
    else if (out.length > 0 && out[out.length - 1] !== null) out.push(null);
  });
  if (out[out.length - 1] === null) out.pop();
  return out;
}

/** Equal as files are equal on screen: a CRLF file read back after a save is not "changed". */
export function sameText(a: string, b: string): boolean {
  return a.replace(/\r\n/g, "\n") === b.replace(/\r\n/g, "\n");
}
