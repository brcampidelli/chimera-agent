import { leadingDoctypeEnd } from "@/components/code/HtmlPreview";
import { isHexColour, type DesignTokens } from "@/lib/design/model";
import { scanTags } from "@/lib/design/htmlElements";

// What contains a prancheta is the frame's own policy plus the app page's `frame-src` (index.html and
// the backend's page policy), which governs the frame's own navigation. The network is NOT sealed by
// this string: the stylesheet and font hosts below stay reachable, and a script can add a <link> to
// them. `connect-src 'none'` closes fetch, XHR and WebSocket; nothing else here reaches another host.
const ARTBOARD_CSP_BASE = "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'";
const STATIC_ARTBOARD_CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'";

export const HTML_ARTBOARD_RUNTIME = `(()=>{for(const k of ['RTCPeerConnection','webkitRTCPeerConnection','fetch','XMLHttpRequest','WebSocket','open']){try{Object.defineProperty(window,k,{value:undefined,writable:false,configurable:false})}catch{}}window.__chimeraComponentClass=null;Object.defineProperty(window,'__chimeraRegister',{value:c=>{window.__chimeraComponentClass=c},writable:false,configurable:false});window.addEventListener('DOMContentLoaded',()=>{if(window.__chimeraComponentClass)new window.__chimeraComponentClass().render({})},{once:true})})();`;

export function base64Sha256(text: string): string {
  // Synchronous SHA-256 keeps srcdoc construction deterministic before the iframe is mounted.
  const constants = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const state = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const bytes = Array.from(new TextEncoder().encode(text));
  const bitLength = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  for (let shift = 56; shift >= 0; shift -= 8) bytes.push(Math.floor(bitLength / 2 ** shift) & 255);
  const rotate = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));
  for (let offset = 0; offset < bytes.length; offset += 64) {
    const words = new Array<number>(64);
    for (let i = 0; i < 16; i++) words[i] = ((bytes[offset+i*4] << 24) | (bytes[offset+i*4+1] << 16) | (bytes[offset+i*4+2] << 8) | bytes[offset+i*4+3]) >>> 0;
    for (let i = 16; i < 64; i++) {
      const x = words[i-15], y = words[i-2];
      const s0 = rotate(x,7) ^ rotate(x,18) ^ (x >>> 3), s1 = rotate(y,17) ^ rotate(y,19) ^ (y >>> 10);
      words[i] = (words[i-16] + s0 + words[i-7] + s1) >>> 0;
    }
    let [a,b,c,d,e,f,g,h] = state;
    for (let i = 0; i < 64; i++) {
      const s1 = rotate(e,6) ^ rotate(e,11) ^ rotate(e,25), choose = (e & f) ^ (~e & g);
      const t1 = (h + s1 + choose + constants[i] + words[i]) >>> 0;
      const s0 = rotate(a,2) ^ rotate(a,13) ^ rotate(a,22), majority = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + majority) >>> 0;
      [h,g,f,e,d,c,b,a] = [g,f,e,(d+t1)>>>0,c,b,a,(t1+t2)>>>0];
    }
    [a,b,c,d,e,f,g,h].forEach((value,index) => { state[index] = (state[index] + value) >>> 0; });
  }
  const digest = state.map((value) => [value >>> 24, (value >>> 16) & 255, (value >>> 8) & 255, value & 255]).flat();
  return btoa(String.fromCharCode(...digest));
}

const MODEL_SCRIPT = /<script\s+type\s*=\s*(["'])text\/x-chimera\1\s*>([\s\S]*?)<\/script\s*>/i;

/**
 * The HTML parser turns CRLF and a lone CR into LF before it hashes or runs a script, so the code is
 * normalised the same way, both where it is hashed and where it is emitted. Otherwise a block written
 * with Windows line endings hashes to a value no browser will ever match.
 */
function normalisedNewlines(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

export function hasModelScript(html: string): boolean {
  return MODEL_SCRIPT.test(html);
}

export function removeModelScript(html: string): string {
  return html.replace(MODEL_SCRIPT, "");
}

/** Static canvas/thumbnail path: model code is removed before injection and script execution is off. */
export function staticArtboardSrcDoc(html: string, tokens: DesignTokens): string {
  return artboardSrcDoc(removeModelScript(html), tokens, false, true);
}

function executableModelScript(html: string): string {
  return html.replace(MODEL_SCRIPT, (_whole, _quote: string, code: string) => {
    const executable = `${normalisedNewlines(code)}\nwindow.__chimeraRegister(Component);`;
    return `<script>${executable}</script>`;
  });
}

/**
 * How many ordinals `annotateForEditing` hands out: one per start tag, the model script's included,
 * so an index the frame receives is valid only when it is below this count.
 */
export function annotatedElementCount(html: string): number {
  let count = 0;
  for (const token of scanTags(html)) if (token.kind === "start") count += 1;
  return count;
}

/**
 * Runs only in edit mode. A click reports the ordinal the editor stamped on the element (or on its
 * nearest stamped ancestor), never a path: the browser rebuilds the DOM, so DOM positions do not
 * match the source, while the ordinal of an opening tag does.
 */
export const HTML_ARTBOARD_SELECTION_SCRIPT = `(()=>{let selected=null;const style=document.createElement('style');style.textContent='.chimera-design-selected{outline:2px solid var(--ds-color-ink)!important;outline-offset:2px!important}';document.head.append(style);function clear(){selected?.classList.remove('chimera-design-selected');selected=null}function select(el){if(!el)return;selected=el;selected.classList.add('chimera-design-selected');const r=el.getBoundingClientRect();parent.postMessage({type:'chimera-design-select',index:Number(el.getAttribute('data-chimera-i')),tag:el.tagName.toLowerCase(),rect:{x:r.x,y:r.y,width:r.width,height:r.height},inlineStyle:el.getAttribute('style')||'',text:el.children.length===0?el.textContent||null:null},'*')}addEventListener('message',e=>{if(e.source!==parent||!e.data||e.data.type!=='chimera-design-restore-selection'||!Number.isInteger(e.data.index))return;const el=document.querySelector('[data-chimera-i="'+e.data.index+'"]');if(selected&&selected!==el)selected.classList.remove('chimera-design-selected');select(el)});document.addEventListener('click',e=>{const target=e.target instanceof Element?e.target:null;if(!target)return;e.preventDefault();e.stopPropagation();clear();const el=target.closest('[data-chimera-i]');if(!el){parent.postMessage({type:'chimera-design-select',index:null},'*');return}select(el)},true);document.addEventListener('keydown',e=>{if(e.key==='Escape'){clear();parent.postMessage({type:'chimera-design-select',index:null},'*')}})})();`;

export const HTML_ARTBOARD_SELECTION_HASH = "GvImHoiiq9JR/gaG3pdyj3tacV5NfpStWT3F0zZgq1A=";
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
    if (token.tag !== "script") out += html.slice(last, token.nameEnd) + ` data-chimera-i="${index}"`;
    else out += html.slice(last, token.nameEnd);
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

export interface HtmlTweak {
  name: string;
  label: string;
  kind: "color" | "number" | "choice";
  value: string;
  start: number;
  end: number;
  minimum?: number;
  maximum?: number;
  step?: number;
  options: string[];
}

export function extractHtmlTweaks(source: string): HtmlTweak[] {
  const result: HtmlTweak[] = [];
  const stylePattern = /<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi;
  const pattern = /\/\*\s*@tweak\s+([A-Za-z][A-Za-z0-9_-]{0,63})\s+(color|number|choice)([^*]*?)\*\/\s*(--tw-[A-Za-z][A-Za-z0-9_-]{0,63})\s*:\s*([^;{}]+);/gi;
  for (const style of source.matchAll(stylePattern)) {
    const body = style[1] ?? "";
    const offset = (style.index ?? 0) + style[0].indexOf(body);
    for (const match of body.matchAll(pattern)) {
      const [, name, kindRaw, args, variable, raw] = match;
      if (!name || variable !== `--tw-${name}` || !raw) continue;
      const value = raw.trim();
      if (/[;}]/.test(value) || /url\s*\(|expression|@import/i.test(value)) continue;
      const strings = [...(args ?? "").matchAll(/"([^"\r\n]{1,80})"/g)].map((m) => m[1] ?? "");
      const label = strings[0];
      if (!label) continue;
      const kind = kindRaw?.toLowerCase();
      const metadata: Partial<HtmlTweak> = {};
      if (kind === "color") {
        if (!/^#[\da-f]{6}(?:[\da-f]{2})?$/i.test(value)) continue;
      } else if (kind === "number") {
        const nums = [...(args ?? "").matchAll(/(?<![\w.])-?\d+(?:\.\d+)?/g)].map((m) => Number(m[0]));
        const number = Number(value);
        if (nums.length < 3 || !Number.isFinite(number) || number < nums[0]! || number > nums[1]! || nums[0]! >= nums[1]! || nums[2]! <= 0) continue;
        Object.assign(metadata, { minimum: nums[0], maximum: nums[1], step: nums[2] });
      } else if (kind === "choice") {
        const options = strings.slice(1);
        if (options.length === 0 || !options.includes(value)) continue;
        Object.assign(metadata, { options });
      } else continue;
      const rawOffset = (match.index ?? 0) + match[0].lastIndexOf(raw);
      result.push({ name, label, kind, value, start: offset + rawOffset + raw.length - raw.trimStart().length, end: offset + rawOffset + raw.length - raw.trimStart().length + value.length, options: [], ...metadata });
    }
  }
  return result;
}

export function setHtmlTweak(source: string, name: string, value: string): string {
  const tweak = extractHtmlTweaks(source).find((item) => item.name === name);
  if (!tweak || /[;}]/.test(value) || /url\s*\(|expression|@import/i.test(value)) throw new Error("unsafe tweak");
  if (tweak.kind === "color" && !/^#[\da-f]{6}(?:[\da-f]{2})?$/i.test(value)) throw new Error("invalid color");
  if (tweak.kind === "number") {
    const number = Number(value);
    if (!Number.isFinite(number) || number < tweak.minimum! || number > tweak.maximum!) throw new Error("invalid number");
  }
  if (tweak.kind === "choice" && !tweak.options.includes(value)) throw new Error("invalid choice");
  return source.slice(0, tweak.start) + value + source.slice(tweak.end);
}

/**
 * Inject policy and tokens before untrusted markup, after a leading doctype when present.
 * The policy must come before the model's first tag: a meta policy governs only what follows it.
 */
export function artboardSrcDoc(html: string, tokens: DesignTokens, editing = false, staticOnly = false): string {
  const hasModelScript = MODEL_SCRIPT.test(html);
  const authored = hasModelScript ? executableModelScript(html) : html;
  const source = editing ? annotateForEditing(authored) : authored;
  const bomOffset = source.startsWith("﻿") ? 1 : 0;
  const insertionPoint = bomOffset + leadingDoctypeEnd(source.slice(bomOffset));
  const hashes = [
    hasModelScript ? `sha256-${base64Sha256(HTML_ARTBOARD_RUNTIME)}` : "",
    hasModelScript ? `sha256-${base64Sha256(`${normalisedNewlines(html.match(MODEL_SCRIPT)?.[2] ?? "")}\nwindow.__chimeraRegister(Component);`)}` : "",
    editing ? `sha256-${HTML_ARTBOARD_SELECTION_HASH}` : "",
  ].filter(Boolean);
  const scriptPolicy = staticOnly ? "script-src 'none'" : hashes.length ? `script-src ${hashes.map((hash) => `'${hash}'`).join(" ")}` : "script-src 'none'";
  // The model's inert block becomes a hash-authorised normal inline script that registers Component;
  // the preceding runtime removes capabilities first. No eval/new Function; opaque origin is the lock.
  const runtime = hasModelScript ? `<script>${HTML_ARTBOARD_RUNTIME}</script>` : "";
  const injected = `<meta http-equiv="Content-Security-Policy" content="${staticOnly ? STATIC_ARTBOARD_CSP : ARTBOARD_CSP_BASE}; ${staticOnly ? "" : scriptPolicy}"><style>${tokensCss(tokens)}${editing ? ".chimera-design-selected{outline:2px solid var(--ds-color-ink)!important;outline-offset:2px!important}" : ""}</style>${runtime}${editing ? `<script>${HTML_ARTBOARD_SELECTION_SCRIPT}</script>` : ""}`;
  return source.slice(0, insertionPoint) + injected + source.slice(insertionPoint);
}
