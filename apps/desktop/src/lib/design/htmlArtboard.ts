import { leadingDoctypeEnd } from "@/components/code/HtmlPreview";
import { isHexColour, type DesignTokens } from "@/lib/design/model";

const ARTBOARD_CSP = "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src data: blob:; script-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'";

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
export function artboardSrcDoc(html: string, tokens: DesignTokens): string {
  const bomOffset = html.startsWith("﻿") ? 1 : 0;
  const insertionPoint = bomOffset + leadingDoctypeEnd(html.slice(bomOffset));
  const injected = `<meta http-equiv="Content-Security-Policy" content="${ARTBOARD_CSP}"><style>${tokensCss(tokens)}</style>`;
  return html.slice(0, insertionPoint) + injected + html.slice(insertionPoint);
}
