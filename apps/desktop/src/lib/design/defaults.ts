/**
 * Defaults for things the person creates on the canvas: artboard sizes, and the colours a new
 * artboard or shape starts with.
 *
 * No colour is written here. A new artboard takes its background from the document's own tokens
 * (`background`, `surface`, …); a document with none takes the app's current card colour, read
 * from the theme at the moment of creation and stored as hex, because the document must stay
 * readable outside this app. Only when neither exists (no theme stylesheet, as in a unit test)
 * does it fall back to plain white, built from its channels.
 */
import { newId, pickColour, type Artboard, type DesignDocument, type DesignPage } from "@/lib/design/model";

export interface ArtboardPreset {
  id: string;
  /** i18n key for the preset's purpose. */
  labelKey: string;
  width: number;
  height: number;
}

export const ARTBOARD_PRESETS: readonly ArtboardPreset[] = [
  { id: "desktop", labelKey: "design.preset.desktop", width: 1440, height: 900 },
  { id: "square", labelKey: "design.preset.square", width: 1080, height: 1080 },
  { id: "slide", labelKey: "design.preset.slide", width: 1920, height: 1080 },
  { id: "phone", labelKey: "design.preset.phone", width: 390, height: 844 },
];

function hex2(n: number): string {
  return Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, "0");
}

export function channelsToHex(r: number, g: number, b: number): string {
  return `#${hex2(r)}${hex2(g)}${hex2(b)}`;
}

/** `h s% l%` (the format of this app's CSS variables) to hex. */
export function hslTripleToHex(triple: string): string | undefined {
  const m = triple.trim().match(/^(-?[\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/);
  if (!m) return undefined;
  const h = ((Number(m[1]) % 360) + 360) % 360;
  const s = Number(m[2]) / 100;
  const l = Number(m[3]) / 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return 255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)));
  };
  return channelsToHex(f(0), f(8), f(4));
}

function themeColour(variable: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(variable);
  return raw ? hslTripleToHex(raw) : undefined;
}

/** The background a new artboard gets in this document. */
export function paperColour(doc: DesignDocument): string {
  return (
    pickColour(doc, ["background", "surface", "paper", "canvas"]) ??
    themeColour("--card") ??
    channelsToHex(255, 255, 255)
  );
}

/** The fill a new shape gets: the document's primary colour, or none (drawn as an outline). */
export function shapeFill(doc: DesignDocument): string | undefined {
  return pickColour(doc, ["primary", "accent", "brand"]);
}

/** The ink a new line or drawing gets: the document's text colour, or the theme's. */
export function inkColour(doc: DesignDocument): string {
  return (
    pickColour(doc, ["text", "foreground", "ink", "primary"]) ??
    themeColour("--foreground") ??
    channelsToHex(0, 0, 0)
  );
}

export function newPage(name: string): DesignPage {
  return { id: newId("page"), name, artboards: [] };
}

export function newArtboard(doc: DesignDocument, preset: ArtboardPreset, name: string): Artboard {
  return {
    id: newId("board"),
    name,
    width: preset.width,
    height: preset.height,
    background: paperColour(doc),
    nodeIds: [],
  };
}
