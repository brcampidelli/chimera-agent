import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { Splitter } from "@/components/shell/Splitter";
import { useLayout } from "@/lib/layout/context";
import { applyLayout, defaultLayout, type Layout } from "@/lib/layout/model";
import { renderWithProviders } from "@/test/utils";

/**
 * Phase 2 of the dynamic screen: the line between two columns changes their widths. The WAI-ARIA
 * window-splitter pattern, with the width in the layout, clamped there, and one drag one step to undo.
 */
let api: ReturnType<typeof useLayout> | null = null;

function Harness({ region, grows }: { region: "left" | "right" | "viewer"; grows: "left" | "right" }) {
  api = useLayout();
  return <Splitter region={region} grows={grows} />;
}

function mount(region: "left" | "right" | "viewer" = "left", grows: "left" | "right" = "right") {
  renderWithProviders(<Harness region={region} grows={grows} />);
  return screen.getByRole("separator");
}

const size = (region: "left" | "right" | "viewer") => api?.layout.regions[region].size;

describe("Splitter", () => {
  beforeEach(() => {
    localStorage.clear();
    api = null;
  });

  it("is a focusable separator that states the width it controls and its range", () => {
    const sep = mount();

    expect(sep).toHaveAttribute("aria-orientation", "vertical");
    expect(sep).toHaveAccessibleName("Resize the left sidebar");
    expect(sep).toHaveAttribute("aria-valuenow", "240");
    expect(sep).toHaveAttribute("aria-valuemin", "180");
    expect(sep).toHaveAttribute("aria-valuemax", "420");
    expect(sep).toHaveAttribute("tabindex", "0");
  });

  it("moves with the arrow keys, and the line follows the arrow for a region on either side", async () => {
    const user = userEvent.setup();
    const left = mount("left", "right");
    left.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(size("left")).toBe(272);
  });

  it("shrinks a region on the right when the line moves right", async () => {
    const user = userEvent.setup();
    const right = mount("right", "left");
    right.focus();
    await user.keyboard("{ArrowRight}");
    expect(size("right")).toBe(272);
  });

  it("goes back to the starting width with Home and with a double click", async () => {
    const user = userEvent.setup();
    const sep = mount();
    sep.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    await user.keyboard("{Home}");
    expect(size("left")).toBe(240);

    await user.keyboard("{ArrowLeft}");
    await user.dblClick(sep);
    expect(size("left")).toBe(240);
  });

  it("follows a drag, clamped at the region's limits", () => {
    const sep = mount("viewer", "left");

    fireEvent.pointerDown(sep, { button: 0, clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(sep, { clientX: 400, pointerId: 1 }); // left: the viewer on the right grows
    expect(size("viewer")).toBe(548);
    fireEvent.pointerMove(sep, { clientX: -5000, pointerId: 1 });
    expect(size("viewer")).toBe(900);
    fireEvent.pointerUp(sep, { pointerId: 1 });

    fireEvent.pointerMove(sep, { clientX: 5000, pointerId: 1 }); // after release: nothing moves
    expect(size("viewer")).toBe(900);
  });

  it("makes one drag one step to undo, and a second drag a second step", () => {
    const sep = mount();

    fireEvent.pointerDown(sep, { button: 0, clientX: 0, pointerId: 1 });
    for (const x of [10, 20, 30, 40]) fireEvent.pointerMove(sep, { clientX: x, pointerId: 1 });
    fireEvent.pointerUp(sep, { pointerId: 1 });
    fireEvent.pointerDown(sep, { button: 0, clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(sep, { clientX: 60, pointerId: 1 });
    fireEvent.pointerUp(sep, { pointerId: 1 });
    expect(size("left")).toBe(340);

    act(() => api?.undo());
    expect(size("left")).toBe(280);
    act(() => api?.undo());
    expect(size("left")).toBe(240);
  });

  it("ignores a button other than the main one", () => {
    const sep = mount();
    fireEvent.pointerDown(sep, { button: 2, clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(sep, { clientX: 100, pointerId: 1 });
    expect(size("left")).toBe(240);
  });
});

describe("the viewer's width in the layout", () => {
  it("is kept and clamped like the side regions, and can never be hidden by the layout", () => {
    const start = defaultLayout();
    expect(start.regions.viewer.size).toBe(448);
    const wide: Layout = applyLayout(start, { type: "resize", region: "viewer", size: 5000 });
    expect(wide.regions.viewer.size).toBe(900);

    // Not reachable through the types; a value that crossed a JSON boundary is refused all the same.
    const forged = { type: "set-region", region: "viewer", visible: false } as unknown as Parameters<typeof applyLayout>[1];
    expect(applyLayout(start, forged)).toBe(start);
  });
});
