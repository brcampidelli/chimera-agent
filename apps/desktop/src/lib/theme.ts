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

/** Stamp an explicit value; remove the attribute for the default, so the default is today's page. */
function stamp(attribute: "textSize" | "font" | "fontCode", value: string, fallback: string): void {
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
