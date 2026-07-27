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
});
