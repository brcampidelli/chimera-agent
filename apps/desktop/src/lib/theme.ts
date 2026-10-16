/**
 * Appearance preferences: theme, motion, text size and the two fonts.
 *
 * Each is a small closed set with one default, and each is written to the `<html>` element as a
 * data attribute, because CSS needs to see them and CSS cannot read React state. React reaches them
 * through `AppearanceProvider` (`lib/appearance.tsx`), so the rail's theme button and the rows in
 * Settings › Appearance read and change the same value.
 *
 * The same resolution runs twice: once in the inline script in `index.html` (before first paint, so
 * there is no flash) and once here (so React can change it at runtime). That duplication is
 * deliberate — the alternative is a flash on every launch — but it is also a drift risk, so the
 * storage keys are exported here and `theme.test.ts` reads `index.html` and asserts they match.
 */

import { applyStoredUiZoom, UI_ZOOM_KEY } from "@/lib/uiZoom";

export type Theme = "system" | "light" | "dark";
export type Motion = "system" | "full" | "reduced";

export const THEME_KEY = "chimera.theme";
export const MOTION_KEY = "chimera.motion";

const THEMES: readonly string[] = ["system", "light", "dark"];
const MOTIONS: readonly string[] = ["system", "full", "reduced"];

/** Read a stored preference, falling back when storage is unavailable or holds something unknown. */
function readPreference<T extends string>(key: string, valid: readonly string[], fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value !== null && valid.includes(value) ? (value as T) : fallback;
  } catch {
    // Private mode, disabled storage, or a sandboxed webview. A preference is never worth throwing.
    return fallback;
  }
}

export function readTheme(): Theme {
  return readPreference<Theme>(THEME_KEY, THEMES, "system");
}

export function readMotion(): Motion {
  return readPreference<Motion>(MOTION_KEY, MOTIONS, "system");
}

function writePreference(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Same reasoning: the preference just won't survive a restart.
  }
}

function prefers(query: string): boolean {
  // matchMedia is missing in some test environments and in very old webviews.
  return typeof matchMedia === "function" && matchMedia(query).matches;
}

/** Turn a preference into the concrete theme to paint. `system` asks the OS; the default is dark. */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "light" || theme === "dark") return theme;
  return prefers("(prefers-color-scheme: light)") ? "light" : "dark";
}

/** Whether motion should be suppressed right now, honouring the user override over the OS flag. */
export function resolveReducedMotion(motion: Motion): boolean {
  if (motion === "reduced") return true;
  if (motion === "full") return false;
  return prefers("(prefers-reduced-motion: reduce)");
}

/** Apply and persist the theme. Returns the concrete theme that was painted. */
export function applyTheme(theme: Theme): "light" | "dark" {
  const resolved = resolveTheme(theme);
  document.documentElement.dataset.theme = resolved;
  writePreference(THEME_KEY, theme);
  return resolved;
}

/**
 * Apply and persist the motion preference.
 *
 * `system` *removes* the attribute rather than writing "system": the reduced-motion CSS is authored
 * as `@media (prefers-reduced-motion: reduce)` OR `[data-motion="reduced"]`, so leaving the
 * attribute off is what hands control back to the media query.
 */
export function applyMotion(motion: Motion): void {
  const root = document.documentElement;
  if (motion === "system") delete root.dataset.motion;
  else root.dataset.motion = motion;
  writePreference(MOTION_KEY, motion);
}

/*
 * Text size and fonts: the same mechanism, one attribute each on `<html>`.
 *
 * Each has a default that writes NO attribute, so someone who never opens Settings › Appearance gets
 * exactly the page they had before these existed: the CSS that reacts to them (`index.css`) only
 * matches an explicit value. The inline script in `index.html` stamps them before first paint for the
 * same reason the theme is stamped there — a size or a typeface that changes after the first frame
 * is a visible jump on every launch.
 */

export type TextSize = "small" | "medium" | "large";
export type UiFont = "system" | "serif" | "dyslexic";
export type CodeFont = "default" | "cascadia" | "jetbrains";

export const TEXT_SIZE_KEY = "chimera.textSize";
export const UI_FONT_KEY = "chimera.font";
export const CODE_FONT_KEY = "chimera.codeFont";

/** Exported so `theme.test.ts` can assert the inline script accepts exactly these values. */
export const TEXT_SIZES: readonly TextSize[] = ["small", "medium", "large"];
export const UI_FONTS: readonly UiFont[] = ["system", "serif", "dyslexic"];
export const CODE_FONTS: readonly CodeFont[] = ["default", "cascadia", "jetbrains"];

export function readTextSize(): TextSize {
  return readPreference<TextSize>(TEXT_SIZE_KEY, TEXT_SIZES, "medium");
}

export function readUiFont(): UiFont {
  return readPreference<UiFont>(UI_FONT_KEY, UI_FONTS, "system");
}

export function readCodeFont(): CodeFont {
  return readPreference<CodeFont>(CODE_FONT_KEY, CODE_FONTS, "default");
}

/*
 * Your own theme, on top of light and dark: an accent colour, the tint of the surfaces and a
 * high-contrast option. Same mechanism again, and the same promise: each default stamps nothing, so
 * the two shipped themes are exactly what they were. The values in `index.css` were picked by
 * computing contrast, and `design/own-theme-is-readable.test.ts` recomputes every combination.
 */

export type Accent = "blue" | "cyan" | "green" | "amber" | "rose" | "graphite";
export type Tint = "navy" | "neutral" | "warm";
export type Contrast = "standard" | "high";

export const ACCENT_KEY = "chimera.accent";
export const TINT_KEY = "chimera.tint";
export const CONTRAST_KEY = "chimera.contrast";

/** No purple or indigo: that is a house rule of this design system, not an omission. */
export const ACCENTS: readonly Accent[] = ["blue", "cyan", "green", "amber", "rose", "graphite"];
export const TINTS: readonly Tint[] = ["navy", "neutral", "warm"];
export const CONTRASTS: readonly Contrast[] = ["standard", "high"];

export function readAccent(): Accent {
  return readPreference<Accent>(ACCENT_KEY, ACCENTS, "blue");
}

export function readTint(): Tint {
  return readPreference<Tint>(TINT_KEY, TINTS, "navy");
}

export function readContrast(): Contrast {
  return readPreference<Contrast>(CONTRAST_KEY, CONTRASTS, "standard");
}

type Stamped = "textSize" | "font" | "fontCode" | "accent" | "tint" | "contrast";

/** Stamp an explicit value; remove the attribute for the default, so the default is today's page. */
function stamp(attribute: Stamped, value: string, fallback: string): void {
  const root = document.documentElement;
  if (value === fallback) delete root.dataset[attribute];
  else root.dataset[attribute] = value;
}

/** `data-text-size` scales the rem root, so the five type sizes stay five and all move together. */
export function applyTextSize(size: TextSize): void {
  stamp("textSize", size, "medium");
  writePreference(TEXT_SIZE_KEY, size);
}

/** `data-font` swaps the VALUE of `--font-sans`; nothing that reads the token has to know. */
export function applyUiFont(font: UiFont): void {
  stamp("font", font, "system");
  writePreference(UI_FONT_KEY, font);
}

/** `data-font-code` swaps the value of `--font-mono`, which the editor and code blocks read. */
export function applyCodeFont(font: CodeFont): void {
  stamp("fontCode", font, "default");
  writePreference(CODE_FONT_KEY, font);
}

/** `data-accent` swaps the accent tokens, in each theme's own lightness. */
export function applyAccent(accent: Accent): void {
  stamp("accent", accent, "blue");
  writePreference(ACCENT_KEY, accent);
}

/** `data-tint` swaps the surface tokens: page, cards, fields and the lines between them. */
export function applyTint(tint: Tint): void {
  stamp("tint", tint, "navy");
  writePreference(TINT_KEY, tint);
}

/** `data-contrast` strengthens secondary text and hairlines; it never touches the accent. */
export function applyContrast(contrast: Contrast): void {
  stamp("contrast", contrast, "standard");
  writePreference(CONTRAST_KEY, contrast);
}

const APPEARANCE_KEYS: readonly string[] = [
  THEME_KEY,
  MOTION_KEY,
  TEXT_SIZE_KEY,
  UI_FONT_KEY,
  CODE_FONT_KEY,
  ACCENT_KEY,
  TINT_KEY,
  CONTRAST_KEY,
];

/**
 * Paint every stored preference without writing any of them back.
 *
 * For a window that shows what the main window chose and chooses nothing itself: writing back would
 * make that window a second author of a value it only reads.
 */
export function paintStoredAppearance(): void {
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(readTheme());
  const motion = readMotion();
  if (motion === "system") delete root.dataset.motion;
  else root.dataset.motion = motion;
  stamp("textSize", readTextSize(), "medium");
  stamp("font", readUiFont(), "system");
  stamp("fontCode", readCodeFont(), "default");
  stamp("accent", readAccent(), "blue");
  stamp("tint", readTint(), "navy");
  stamp("contrast", readContrast(), "standard");
}

/**
 * Keep a secondary window (`?conversation=`, `?float=`) in step with the main one.
 *
 * Those windows mount no AppearanceProvider; the inline script paints them once, on load. Without
 * this, a theme, text size or font changed in the main window reached a window already open only when
 * it reloaded — the main window and the conversation beside it in two different sizes. The `storage`
 * event fires in every OTHER window of the origin when a value changes, which is exactly the set that
 * needs to repaint. `key === null` is `localStorage.clear()`: everything went back to its default.
 * Returns the unsubscribe.
 */
export function followStoredAppearance(target: Window = window): () => void {
  // Interface zoom rides along: it is not one of the attribute preferences above (it is a style, owned
  // by src/lib/uiZoom.ts), but a conversation window must follow it the same way.
  applyStoredUiZoom();
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || APPEARANCE_KEYS.includes(event.key)) paintStoredAppearance();
    if (event.key === null || event.key === UI_ZOOM_KEY) applyStoredUiZoom();
  };
  target.addEventListener("storage", onStorage);
  return () => target.removeEventListener("storage", onStorage);
}
