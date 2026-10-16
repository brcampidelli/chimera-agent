/**
 * Find in the conversation (⌘F): where the matches are, and how they are painted.
 *
 * Painted with the CSS Custom Highlight API rather than by wrapping matches in `<mark>`. The
 * transcript is React's DOM and it is still being written while a turn streams: splitting its text
 * nodes to insert marks would either be undone by the next render or, worse, leave React patching
 * nodes that are no longer where it put them. A highlight is a list of Ranges the page paints over
 * the text without touching it, so clearing it is clearing the list and nothing else.
 *
 * Where the API is missing (an old WebView), the bar still counts and scrolls to each match; only
 * the colour is lost.
 */
export const FIND_HIGHLIGHT = "chimera-find";
export const FIND_CURRENT = "chimera-find-current";

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Every case-insensitive occurrence of `query` in `root`'s text, in document order.
 *
 * The text nodes are searched as ONE string, so a phrase that crosses a bold or a code span still
 * matches; a regular expression with the `i` flag rather than `toLowerCase()`, because lower-casing
 * can change a string's length ("İ" becomes two characters) and the offsets would then point at the
 * wrong letters.
 */
export function findRanges(root: Node, query: string): Range[] {
  const needle = query.trim();
  if (!needle) return [];
  const doc = root.ownerDocument ?? document;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: { node: Text; start: number }[] = [];
  let text = "";
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const value = (n as Text).data;
    if (!value) continue;
    nodes.push({ node: n as Text, start: text.length });
    text += value;
  }
  const ranges: Range[] = [];
  let i = 0;
  for (const match of text.matchAll(new RegExp(escapeRegExp(needle), "gi"))) {
    const from = match.index;
    const to = from + match[0].length;
    while (i < nodes.length - 1 && nodes[i + 1].start <= from) i += 1;
    let j = i;
    while (j < nodes.length - 1 && nodes[j + 1].start < to) j += 1;
    const range = doc.createRange();
    range.setStart(nodes[i].node, from - nodes[i].start);
    range.setEnd(nodes[j].node, to - nodes[j].start);
    ranges.push(range);
  }
  return ranges;
}

interface HighlightRegistryLike {
  set(name: string, highlight: unknown): void;
  delete(name: string): void;
}

function registry(): { highlights: HighlightRegistryLike; make: (ranges: Range[]) => unknown } | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistryLike } }).CSS;
  const ctor = (globalThis as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight;
  if (!css?.highlights || !ctor) return null;
  return { highlights: css.highlights, make: (ranges) => new ctor(...ranges) };
}

/** Paint every match, and the current one in its own colour on top. */
export function paintMatches(ranges: Range[], current: number): void {
  const api = registry();
  if (!api) return;
  api.highlights.set(FIND_HIGHLIGHT, api.make(ranges));
  const here = ranges[current];
  if (here) api.highlights.set(FIND_CURRENT, api.make([here]));
  else api.highlights.delete(FIND_CURRENT);
}

export function clearMatches(): void {
  const api = registry();
  if (!api) return;
  api.highlights.delete(FIND_HIGHLIGHT);
  api.highlights.delete(FIND_CURRENT);
}

/** Bring a match into view. Not smooth: a jump is what reduced motion asks for, and it is instant
 *  for everyone else too, which is what pressing F3 repeatedly wants. */
export function revealMatch(range: Range | undefined): void {
  const el = range?.startContainer.parentElement;
  if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center" });
}
