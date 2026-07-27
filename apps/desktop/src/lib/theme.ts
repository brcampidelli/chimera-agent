/**
 * Theme and motion preferences.
 *
 * Both are three-state (`system` | explicit | explicit) and both are written to the `<html>` element
 * as data attributes, because CSS needs to see them and CSS cannot read React state.
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
