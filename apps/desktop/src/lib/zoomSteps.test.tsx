import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useHotkeys } from "@/lib/hotkeys";
import { getUiZoom, setUiZoom, UI_ZOOM_KEY } from "@/lib/uiZoom";
import { stepUiZoom, steppedZoom } from "@/lib/zoomSteps";

function Harness() {
  useHotkeys({ onPalette: () => {}, onSettings: () => {}, onNewChat: () => {}, onNavigate: () => {}, onZoom: stepUiZoom });
  return null;
}

/**
 * ⌘= / ⌘- / ⌘0 write the zoom through src/lib/uiZoom.ts, the module the Appearance › Interface zoom
 * row writes with (C8). A first draft kept its own small/medium/large under another storage key, so
 * the shortcut and the Settings row each held a zoom, and the root carried both multiplied.
 */
describe("keyboard zoom", () => {
  afterEach(() => setUiZoom(100));

  it("steps along the Settings row's values and stops at the ends", () => {
    expect(steppedZoom(100, "in")).toBe(110);
    expect(steppedZoom(120, "in")).toBe(120);
    expect(steppedZoom(80, "out")).toBe(80);
    expect(steppedZoom(90, "reset")).toBe(100);
    // A value the module never wrote steps from 100 rather than from nowhere.
    expect(steppedZoom(105, "out")).toBe(90);
  });

  it("persists each press, and Ctrl+0 comes back to the page as it was", () => {
    render(<Harness />);
    const zoomIn = { key: "=", code: "Equal", ctrlKey: true };
    fireEvent.keyDown(window, zoomIn);
    fireEvent.keyDown(window, zoomIn);
    expect(localStorage.getItem(UI_ZOOM_KEY)).toBe("120");
    expect(document.documentElement.style.zoom).toBe("120%");
    fireEvent.keyDown(window, { key: "-", code: "Minus", ctrlKey: true });
    expect(getUiZoom()).toBe(110);

    fireEvent.keyDown(window, { key: "0", code: "Digit0", ctrlKey: true });
    expect(localStorage.getItem(UI_ZOOM_KEY)).toBe("100");
    expect(document.documentElement.style.zoom).toBe("");
  });

  it("steps from what Settings chose, not from a copy of its own", () => {
    render(<Harness />);
    setUiZoom(80);
    fireEvent.keyDown(window, { key: "=", code: "Equal", ctrlKey: true });
    expect(getUiZoom()).toBe(90);
  });
});
