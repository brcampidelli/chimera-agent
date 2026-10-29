/**
 * Element ordinals in an artboard's HTML source. The rule is the one in chimera/design/html_source.py,
 * and the fixture both sides are checked against is __fixtures__/html-element-index.json: an element
 * is the ordinal of its opening tag among every start tag, in document order. Comments, declarations,
 * close tags and the content of raw-text elements never count.
 *
 * Pure on purpose: ops.ts applies these edits optimistically, and htmlArtboard.ts annotates the
 * iframe with the same numbers, so neither may import the React-side modules.
 */

const VOID_ELEMENTS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
const RAW_TEXT_ELEMENTS = new Set(["script", "style", "textarea", "title"]);

/** Same patterns as html.parser's, so the same bytes are read as attributes and as tags. */
const TAG_HEAD = /<([a-zA-Z][^\t\n\r\f />\x00]*)(?:\s|\/(?!>))*/y;
const TAG_NAME = /[A-Za-z][^\t\n\r\f \/>\x00]*/y;
const ATTRIBUTE = /((?<=['"\t\n\r\f \/])[^\t\n\r\f \/>][^\t\n\r\f \/=>]*)([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?(?:[\t\n\r\f ]|\/(?!>))*/y;
const END_TAG = /<\/([A-Za-z][^\t\n\r\f \/>\x00]*)[^>]*>/y;

export interface StartTagToken {
  kind: "start";
  tag: string;
  nameEnd: number;
  start: number;
  end: number;
  selfClosing: boolean;
}

export interface EndTagToken {
  kind: "end";
  tag: string;
  start: number;
  end: number;
}

export type TagToken = StartTagToken | EndTagToken;

/** A refusal with the code the operation reports, as ops.ts reports every other refusal. */
export class HtmlEditRefused extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function at(pattern: RegExp, text: string, position: number): RegExpExecArray | null {
  pattern.lastIndex = position;
  return pattern.exec(text);
}

function skipPast(html: string, from: number): number {
  const close = html.indexOf(">", from);
  return close < 0 ? html.length : close + 1;
}

function readStartTag(html: string, open: number): StartTagToken | null {
  const name = at(TAG_NAME, html, open + 1);
  if (!name) return null;
  const head = at(TAG_HEAD, html, open);
  let position = open + (head ? head[0].length : 1 + name[0].length);
  let selfClosing = false;
  let end = -1;
  while (position < html.length) {
    if (html[position] === ">") {
      end = position + 1;
      break;
    }
    if (html[position] === "/" && html[position + 1] === ">") {
      selfClosing = true;
      end = position + 2;
      break;
    }
    const attribute = at(ATTRIBUTE, html, position);
    if (!attribute) {
      position += 1;
      continue;
    }
    position += attribute[0].length;
  }
  if (end < 0) return null;
  const tag = name[0].toLowerCase();
  return {
    kind: "start",
    tag,
    nameEnd: open + 1 + name[0].length,
    start: open,
    end,
    selfClosing: selfClosing || VOID_ELEMENTS.has(tag),
  };
}

/** Every start and end tag in document order, with the raw-text content of script/style/textarea/title skipped. */
export function scanTags(html: string): TagToken[] {
  const tokens: TagToken[] = [];
  let i = 0;
  while (i < html.length) {
    const open = html.indexOf("<", i);
    if (open < 0) break;
    const next = html.charAt(open + 1);
    if (html.startsWith("<!--", open)) {
      const close = html.indexOf("-->", open + 4);
      i = close < 0 ? html.length : close + 3;
    } else if (next === "!" || next === "?") {
      i = skipPast(html, open + 2);
    } else if (next === "/") {
      const close = /[A-Za-z]/.test(html.charAt(open + 2)) ? at(END_TAG, html, open) : null;
      if (close) tokens.push({ kind: "end", tag: close[1].toLowerCase(), start: open, end: open + close[0].length });
      i = close ? open + close[0].length : skipPast(html, open + 2);
    } else if (/[A-Za-z]/.test(next)) {
      const start = readStartTag(html, open);
      if (!start) {
        i = open + 1;
        continue;
      }
      tokens.push(start);
      i = start.end;
      if (RAW_TEXT_ELEMENTS.has(start.tag)) {
        const raw = new RegExp(`</${start.tag}(?=[\\t\\n\\r\\f />])`, "gi");
        raw.lastIndex = start.end;
        const found = raw.exec(html);
        i = found ? found.index : html.length;
      }
    } else {
      i = open + 1;
    }
  }
  return tokens;
}

export interface LocatedElement {
  tag: string;
  tagStart: number;
  tagEnd: number;
  contentEnd: number;
  textOnly: boolean;
  selfClosing: boolean;
  closed: boolean;
}

/** The element whose opening tag is the `index`-th start tag, or null when there is none. */
export function locateElement(html: string, index: number): LocatedElement | null {
  if (!Number.isInteger(index) || index < 0) return null;
  const starts: LocatedElement[] = [];
  const open: LocatedElement[] = [];
  for (const token of scanTags(html)) {
    if (token.kind === "start") {
      if (open.length) open[open.length - 1].textOnly = false;
      const element: LocatedElement = {
        tag: token.tag,
        tagStart: token.start,
        tagEnd: token.end,
        contentEnd: token.end,
        textOnly: true,
        selfClosing: token.selfClosing,
        closed: false,
      };
      starts.push(element);
      if (!token.selfClosing) open.push(element);
    } else {
      for (let position = open.length - 1; position >= 0; position -= 1) {
        if (open[position].tag === token.tag) {
          open[position].closed = true;
          open[position].contentEnd = token.start;
          open.splice(position);
          break;
        }
      }
    }
  }
  const element = starts[index];
  return element ? { ...element } : null;
}

function decodeEntities(text: string): string {
  const named: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", "#39": "'", "#x27": "'" };
  return text.replace(/&(amp|lt|gt|quot|apos|#39|#x27);/g, (_match, name: string) => named[name]);
}

/** The `style` attribute of an opening tag: its span in that tag's text, and its decoded value. */
export function styleAttribute(tagText: string): { start: number; end: number; value: string } | null {
  const head = at(TAG_HEAD, tagText, 0);
  let position = head ? head[0].length : 0;
  while (position < tagText.length) {
    const attribute = at(ATTRIBUTE, tagText, position);
    if (!attribute) break;
    if (attribute[1].toLowerCase() === "style") {
      let raw = attribute[3] ?? "";
      if (raw.length >= 2 && raw[0] === raw[raw.length - 1] && (raw[0] === '"' || raw[0] === "'")) raw = raw.slice(1, -1);
      return {
        start: attribute.index,
        end: attribute.index + attribute[1].length + (attribute[2] ?? "").length,
        value: decodeEntities(raw),
      };
    }
    position = attribute.index + attribute[0].length;
  }
  return null;
}

/** Offset in the tag's text just past its name, where a new attribute goes. */
function tagNameEnd(tagText: string): number {
  const head = at(TAG_HEAD, tagText, 0);
  return head ? 1 + head[1].length : 1;
}

/** Escape for a double-quoted attribute: only the characters that end it or start markup. */
function attributeText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** `name: value` pairs; a `;` inside a quoted string does not split. Mirrors `_split_declarations`. */
function splitDeclarations(style: string): [string, string][] {
  const parts: string[] = [];
  let current = "";
  let quote = "";
  for (const char of style) {
    if (quote) {
      current += char;
      if (char === quote) quote = "";
    } else if (char === '"' || char === "'") {
      quote = char;
      current += char;
    } else if (char === ";") {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  const pairs: [string, string][] = [];
  for (const part of parts) {
    const colon = part.indexOf(":");
    if (colon < 0) continue;
    const name = part.slice(0, colon).trim().toLowerCase();
    if (name) pairs.push([name, part.slice(colon + 1).trim()]);
  }
  return pairs;
}

/** Set (or, with an empty value, remove) one CSS property of one element. Mirrors `_artboard_set_style`. */
export function setStyleInSource(source: string, index: number, property: string, value: string): string {
  const located = locateElement(source, index);
  if (!located) throw new HtmlEditRefused("htmlPath");
  const prop = property.trim().toLowerCase();
  if (!/^(--[A-Za-z0-9_-]+|[A-Za-z_-][A-Za-z0-9_-]*)$/.test(prop) || prop === "behavior" || prop === "-moz-binding") {
    throw new HtmlEditRefused("htmlCss");
  }
  if (/[;{}]/.test(value)) throw new HtmlEditRefused("htmlCss");
  const opening = source.slice(located.tagStart, located.tagEnd);
  const found = styleAttribute(opening);
  const declarations = splitDeclarations(found ? found.value : "").filter(([name]) => name !== prop);
  if (value) declarations.push([prop, value.trim()]);
  const style = declarations.map(([name, text]) => `${name}: ${text}`).join("; ");
  let tag = opening;
  if (found) {
    tag = style
      ? opening.slice(0, found.start) + `style="${attributeText(style)}"` + opening.slice(found.end)
      : opening.slice(0, found.start).replace(/\s+$/, "") + opening.slice(found.end);
  } else if (style) {
    const at = tagNameEnd(opening);
    tag = opening.slice(0, at) + ` style="${attributeText(style)}"` + opening.slice(at);
  }
  return source.slice(0, located.tagStart) + tag + source.slice(located.tagEnd);
}

/** Replace the text of a text-only element. Mirrors `_artboard_set_text`. */
export function setTextInSource(source: string, index: number, text: string): string {
  const located = locateElement(source, index);
  if (!located) throw new HtmlEditRefused("htmlPath");
  if (located.selfClosing) throw new HtmlEditRefused("htmlVoid");
  if (!located.closed) throw new HtmlEditRefused("htmlUnclosed");
  if (!located.textOnly) throw new HtmlEditRefused("htmlTextOnly");
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return source.slice(0, located.tagEnd) + escaped + source.slice(located.contentEnd);
}

/** What the inspector shows for the element at `index`, read from the current source. */
export function describeElement(html: string, index: number): { tag: string; inlineStyle: string; text: string | null } | null {
  const located = locateElement(html, index);
  if (!located) return null;
  const style = styleAttribute(html.slice(located.tagStart, located.tagEnd));
  const text = located.textOnly && located.closed && !located.selfClosing
    ? decodeEntities(html.slice(located.tagEnd, located.contentEnd))
    : null;
  return { tag: located.tag, inlineStyle: style ? style.value : "", text };
}
