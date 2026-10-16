/**
 * Interface zoom: one value, one place to read it, one place to write it.
 *
 * The Settings row (Appearance › Interface zoom) and the keyboard zoom shortcuts (task C4, landing
 * separately) both go through this module. Two writers with their own copies of "the current zoom" is
 * how a shortcut ends up at 110% while the select says 100%, so neither keeps one: they call
 * `getUiZoom` / `setUiZoom` and listen for `UI_ZOOM_EVENT`.
 *
 * Zoom is not Text size. Text size moves the rem root and keeps the five-size type scale; zoom scales
 * the whole page — borders, icons, hit targets — with CSS `zoom` on `<html>`. The two multiply and
 * neither writes the other's attribute.
 *
 * The key is mirrored in the inline script in `index.html`, which applies it before first paint;
 * `theme.test.ts` asserts the two agree, including that the script compares STRINGS — storage hands
 * back "110", and a numeric list there silently never matched.
 */
export const UI_ZOOM_KEY = "chimera.uiZoom";
export const UI_ZOOM_VALUES = [80, 90, 100, 110, 120] as const;
export type UiZoom = (typeof UI_ZOOM_VALUES)[number];
/** Fired on `window` in the window that changed the zoom; other windows hear `storage`. */
export const UI_ZOOM_EVENT = "chimera:ui-zoom";

function isUiZoom(value: number): value is UiZoom {
  return (UI_ZOOM_VALUES as readonly number[]).includes(value);
}

export function getUiZoom(): UiZoom {
  try {
    const stored = localStorage.getItem(UI_ZOOM_KEY);
    if (stored === null) return 100;
    const value = Number(stored);
    return isUiZoom(value) ? value : 100;
  } catch {
    // Private mode or storage disabled: the page is drawn at its own size.
    return 100;
  }
}

/** Writes the zoom onto `<html>`. 100 clears it, so the default stamps nothing, like the other rows. */
export function applyUiZoom(value: UiZoom): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.zoom = value === 100 ? "" : `${value}%`;
}

export function setUiZoom(value: UiZoom): void {
  if (!isUiZoom(value)) return;
  try {
    localStorage.setItem(UI_ZOOM_KEY, String(value));
  } catch {
    // The zoom still applies to this window; it just will not survive a restart.
  }
  applyUiZoom(value);
  if (typeof window !== "undefined") window.dispatchEvent(new Event(UI_ZOOM_EVENT));
}

/** Applies the stored zoom. Called at startup (after the inline script, for windows it did not run in)
 *  and when another window changes the value. */
export function applyStoredUiZoom(): void {
  applyUiZoom(getUiZoom());
}
