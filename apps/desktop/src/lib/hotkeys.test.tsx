import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useHotkeys, type Hotkeys } from "@/lib/hotkeys";

function Harness(handlers: Hotkeys) {
  useHotkeys(handlers);
  return <textarea aria-label="composer" />;
}

function setup(overrides: Partial<Hotkeys> = {}) {
  const handlers: Hotkeys = {
    onPalette: vi.fn(),
    onSettings: vi.fn(),
    onNewChat: vi.fn(),
    onNavigate: vi.fn(),
    ...overrides,
  };
  render(<Harness {...handlers} />);
  return { user: userEvent.setup(), ...handlers };
}

describe("useHotkeys", () => {
  it("opens the palette with the platform chord", async () => {
    const { user, onPalette } = setup();
    await user.keyboard("{Control>}k{/Control}");
    expect(onPalette).toHaveBeenCalledOnce();
  });

  it("jumps to a rail position by number", async () => {
    const { user, onNavigate } = setup();
    await user.keyboard("{Control>}3{/Control}");
    expect(onNavigate).toHaveBeenCalledWith(3);
  });

  it("ignores a bare key with no modifier", async () => {
    const { user, onNewChat } = setup();
    await user.keyboard("n");
    expect(onNewChat).not.toHaveBeenCalled();
  });

  it("does not fire destructive shortcuts while you are typing", async () => {
    // The one that matters. Ctrl+N inside the composer would discard a half-written message, and a
    // shortcut that destroys work is worse than no shortcut.
    const { user, onNewChat } = setup();
    await user.click(screen.getByLabelText("composer"));
    await user.keyboard("{Control>}n{/Control}");
    expect(onNewChat).not.toHaveBeenCalled();
  });

  it("still opens the palette while typing", async () => {
    // The exception, and deliberate: a palette exists to be reachable without moving your hands,
    // and it opens OVER the field rather than acting on it.
    const { user, onPalette } = setup();
    await user.click(screen.getByLabelText("composer"));
    await user.keyboard("{Control>}k{/Control}");
    expect(onPalette).toHaveBeenCalledOnce();
  });

  it("hides the left region with the chord and B, and the right one with the chord, Alt and B", async () => {
    const onToggleRegion = vi.fn();
    const { user } = setup({ onToggleRegion });

    await user.keyboard("{Control>}b{/Control}");
    await user.keyboard("{Control>}{Alt>}b{/Alt}{/Control}");

    expect(onToggleRegion.mock.calls).toEqual([["left"], ["right"]]);
  });

  it("reads B by the physical key, so AltGr on a Brazilian keyboard still means the right region", () => {
    const onToggleRegion = vi.fn();
    setup({ onToggleRegion });

    // Ctrl+Alt+B on ABNT2: the character is not "b", the key is still KeyB.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "KeyB", ctrlKey: true, altKey: true }));

    expect(onToggleRegion).toHaveBeenCalledWith("right");
  });

  it("does not hide a region while the user is typing", async () => {
    const onToggleRegion = vi.fn();
    const { user } = setup({ onToggleRegion });

    await user.click(screen.getByRole("textbox", { name: "composer" }));
    await user.keyboard("{Control>}b{/Control}");

    expect(onToggleRegion).not.toHaveBeenCalled();
  });
});
