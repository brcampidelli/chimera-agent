import { getUiZoom, setUiZoom, UI_ZOOM_VALUES, type UiZoom } from "@/lib/uiZoom";

/**
 * One zoom step from `current`: up or down the list the Settings row offers, or back to 100%.
 * Stops at either end rather than wrapping — ⌘= held down must not jump from 120% to 80%.
 */
export function steppedZoom(current: number, direction: "in" | "out" | "reset"): UiZoom {
  if (direction === "reset") return 100;
  const at = UI_ZOOM_VALUES.findIndex((v) => v === current);
  const from = at < 0 ? UI_ZOOM_VALUES.indexOf(100) : at;
  const to = Math.max(0, Math.min(UI_ZOOM_VALUES.length - 1, from + (direction === "in" ? 1 : -1)));
  return UI_ZOOM_VALUES[to];
}

/**
 * What ⌘= / ⌘- / ⌘0 do: step the stored zoom and write it back, through `src/lib/uiZoom.ts` — the
 * module the Appearance › Interface zoom row writes with. Read at each press rather than held, so a
 * value chosen in Settings, or in another window, is the one the next press steps from; neither
 * writer keeps a copy that could disagree with the other.
 */
export function stepUiZoom(direction: "in" | "out" | "reset"): UiZoom {
  const next = steppedZoom(getUiZoom(), direction);
  setUiZoom(next);
  return next;
}
