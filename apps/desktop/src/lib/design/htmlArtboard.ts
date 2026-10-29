import { leadingDoctypeEnd } from "@/components/code/HtmlPreview";
import { isHexColour, type DesignTokens } from "@/lib/design/model";
import { scanTags } from "@/lib/design/htmlElements";

const ARTBOARD_CSP_BASE = "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'";

/**
 * Runs only in edit mode. A click reports the ordinal the editor stamped on the element (or on its
 * nearest stamped ancestor), never a path: the browser rebuilds the DOM, so DOM positions do not
 * match the source, while the ordinal of an opening tag does.
 */
export const HTML_ARTBOARD_SELECTION_SCRIPT = `(()=>{let selected=null;const style=document.createElement('style');style.textContent='.chimera-design-selected{outline:2px solid var(--ds-color-ink)!important;outline-offset:2px!important}';document.head.append(style);function clear(){selected?.classList.remove('chimera-design-selected');selected=null}document.addEventListener('click',e=>{const target=e.target instanceof Element?e.target:null;if(!target)return;e.preventDefault();e.stopPropagation();clear();const el=target.closest('[data-chimera-i]');if(!el){parent.postMessage({type:'chimera-design-select',index:null},'*');return}selected=el;selected.classList.add('chimera-design-selected');const r=el.getBoundingClientRect();parent.postMessage({type:'chimera-design-select',index:Number(el.getAttribute('data-chimera-i')),tag:el.tagName.toLowerCase(),rect:{x:r.x,y:r.y,width:r.width,height:r.height},inlineStyle:el.getAttribute('style')||'',text:el.children.length===0?el.textContent||null:null},'*')},true);document.addEventListener('keydown',e=>{if(e.key==='Escape'){clear();parent.postMessage({type:'chimera-design-select',index:null},'*')}})})();`;

export const HTML_ARTBOARD_SELECTION_HASH = "Bkk/4/RAGKGfrJFiKx+qb4gn1Yx5ELwP4ZOo0tA3UxM=";
export async function calculateSelectionScriptHash(): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(HTML_ARTBOARD_SELECTION_SCRIPT));
  return btoa(String.fromCharCode(...new Uint8Array(digest)));
}

/**
 * Stamp `data-chimera-i="N"` on every start tag, N counted in source order (see htmlElements.ts).
 * The stamp goes right after the tag name, so `<br/>` and SVG children are stamped too. Elements the
 * browser creates on its own (html, head, body, tbody) carry no stamp and are never selected.
 */
export function annotateForEditing(html: string): string {
  let out = "";
  let last = 0;
  let index = 0;
  for (const token of scanTags(html)) {
    if (token.kind !== "start") continue;
    out += html.slice(last, token.nameEnd) + ` data-chimera-i="${index}"`;
    last = token.nameEnd;
    index += 1;
  }
  return out + html.slice(last);
}

/** Names become part of a custom property name, so only identifier characters may pass. */
const SAFE_TOKEN_NAME = /^[A-Za-z0-9_-]+$/;

/**
 * The validated document is still data that may come from the model. These checks run here, not
 * only in the model layer, because `tokensCss` writes into a `<style>` element: one `</style>` in a
 * name or family would end the element and let the rest of the value parse as markup.
 */
function safeNames(record: object): string[] {
  return Object.keys(record).filter((name) => SAFE_TOKEN_NAME.test(name)).sort();
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * A family may be quoted, so `"` and `\` are allowed: `cssString` escapes them. Markup and rule
 * delimiters are not, nor are control characters.
 */
function isSafeFamily(family: unknown): family is string {
  if (typeof family !== "string" || /[<>;{}]/.test(family)) return false;
  for (const character of family) {
    const code = character.codePointAt(0) ?? 0;
    if (code < 0x20 || code === 0x7f) return false;
  }
  return true;
}

function cssString(value: string): string {
  return value.replace(/[\\"\n\r\f]/g, (character) => {
    if (character === "\\" || character === '"') return `\\${character}`;
    return `\\${character.codePointAt(0)?.toString(16) ?? "0"} `;
  });
}

/**
 * CSS custom properties supplied by the validated design document, in stable token order.
 * An invalid entry is dropped rather than escaped: a dropped token falls back to the page's own
 * CSS, while an escaped one could still change what the page means.
 */
export function tokensCss(tokens: DesignTokens): string {
  const declarations: string[] = [];
  for (const name of safeNames(tokens.colors)) {
    const value = tokens.colors[name];
    if (isHexColour(value)) declarations.push(`--ds-color-${name}: ${value}`);
  }
  for (const name of safeNames(tokens.typography)) {
    const token = tokens.typography[name];
    if (isSafeFamily(token.family)) declarations.push(`--ds-font-${name}-family: "${cssString(token.family)}"`);
    if (isFiniteNumber(token.size)) declarations.push(`--ds-font-${name}-size: ${token.size}px`);
    if (isFiniteNumber(token.weight)) declarations.push(`--ds-font-${name}-weight: ${token.weight}`);
    if (token.lineHeight !== undefined && isFiniteNumber(token.lineHeight)) {
      declarations.push(`--ds-font-${name}-line-height: ${token.lineHeight}`);
    }
  }
  for (const name of safeNames(tokens.spacing)) {
    const value = tokens.spacing[name];
    if (isFiniteNumber(value)) declarations.push(`--ds-space-${name}: ${value}px`);
  }
  for (const name of safeNames(tokens.radii)) {
    const value = tokens.radii[name];
    if (isFiniteNumber(value)) declarations.push(`--ds-radius-${name}: ${value}px`);
  }
  return `:root{${declarations.join(";")}}`;
}

/**
 * Inject policy and tokens before untrusted markup, after a leading doctype when present.
 * The policy must come before the model's first tag: a meta policy governs only what follows it.
 */
export function artboardSrcDoc(html: string, tokens: DesignTokens, editing = false): string {
  const source = editing ? annotateForEditing(html) : html;
  const bomOffset = source.startsWith("﻿") ? 1 : 0;
  const insertionPoint = bomOffset + leadingDoctypeEnd(source.slice(bomOffset));
  const scriptPolicy = editing ? `script-src 'sha256-${HTML_ARTBOARD_SELECTION_HASH}'` : "script-src 'none'";
  const injected = `<meta http-equiv="Content-Security-Policy" content="${ARTBOARD_CSP_BASE}; ${scriptPolicy}"><style>${tokensCss(tokens)}${editing ? ".chimera-design-selected{outline:2px solid var(--ds-color-ink)!important;outline-offset:2px!important}" : ""}</style>${editing ? `<script>${HTML_ARTBOARD_SELECTION_SCRIPT}</script>` : ""}`;
  return source.slice(0, insertionPoint) + injected + source.slice(insertionPoint);
}
