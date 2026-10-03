import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useDictateKey, useHotkeys, type Hotkeys } from "@/lib/hotkeys";

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

  it("toggles focus mode, maximises and goes to the approval with the shifted chords", async () => {
    const onFocusMode = vi.fn();
    const onMaximize = vi.fn();
    const onApproval = vi.fn();
    const { user } = setup({ onFocusMode, onMaximize, onApproval });

    await user.keyboard("{Control>}{Shift>}f{/Shift}{/Control}");
    await user.keyboard("{Control>}{Shift>}m{/Shift}{/Control}");
    await user.keyboard("{Control>}{Shift>}a{/Shift}{/Control}");

    expect(onFocusMode).toHaveBeenCalledOnce();
    expect(onMaximize).toHaveBeenCalledOnce();
    expect(onApproval).toHaveBeenCalledOnce();
  });

  it("does not take the shifted chords while the user is typing", async () => {
    const onFocusMode = vi.fn();
    const { user } = setup({ onFocusMode });

    await user.click(screen.getByRole("textbox", { name: "composer" }));
    await user.keyboard("{Control>}{Shift>}f{/Shift}{/Control}");

    expect(onFocusMode).not.toHaveBeenCalled();
  });

  it("leaves the unshifted chords alone: Ctrl+A still selects, Ctrl+F still finds, Ctrl+M is untouched", async () => {
    const onFocusMode = vi.fn();
    const onMaximize = vi.fn();
    const onApproval = vi.fn();
    const { user } = setup({ onFocusMode, onMaximize, onApproval });

    await user.keyboard("{Control>}a{/Control}");
    await user.keyboard("{Control>}f{/Control}");
    await user.keyboard("{Control>}m{/Control}");

    expect(onApproval).not.toHaveBeenCalled();
    expect(onFocusMode).not.toHaveBeenCalled();
    expect(onMaximize).not.toHaveBeenCalled();
  });
});

function DictateHarness({ onPress, onRelease }: { onPress: () => void; onRelease: () => void }) {
  useDictateKey(onPress, onRelease);
  return <textarea aria-label="composer" />;
}

function setupDictate() {
  const onPress = vi.fn();
  const onRelease = vi.fn();
  const view = render(<DictateHarness onPress={onPress} onRelease={onRelease} />);
  return { user: userEvent.setup(), onPress, onRelease, view };
}

function key(type: "keydown" | "keyup", init: KeyboardEventInit) {
  const e = new KeyboardEvent(type, { cancelable: true, ...init });
  act(() => void window.dispatchEvent(e));
  return e;
}

describe("useDictateKey", () => {
  it("presses on the chord with Space and releases when Space comes up", async () => {
    const { user, onPress, onRelease } = setupDictate();

    await user.keyboard("{Control>}{Shift>}[Space>]");
    expect(onPress).toHaveBeenCalledOnce();
    expect(onRelease).not.toHaveBeenCalled();
    await user.keyboard("[/Space]");
    expect(onRelease).toHaveBeenCalledOnce();
    // The modifiers coming up after are the same release, not a second one.
    await user.keyboard("{/Shift}{/Control}");
    expect(onRelease).toHaveBeenCalledOnce();
  });

  it("is one press however long the key is held, and the default action is taken away", () => {
    const { onPress } = setupDictate();
    const first = key("keydown", { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
    key("keydown", { code: "Space", key: " ", ctrlKey: true, shiftKey: true, repeat: true });
    key("keydown", { code: "Space", key: " ", ctrlKey: true, shiftKey: true, repeat: true });
    expect(onPress).toHaveBeenCalledOnce();
    expect(first.defaultPrevented).toBe(true);
  });

  it("releases when the Mac's ⌘ comes up first, which is the only keyup macOS sends", () => {
    const { onPress, onRelease } = setupDictate();
    key("keydown", { code: "Space", key: " ", metaKey: true, shiftKey: true });
    expect(onPress).toHaveBeenCalledOnce();
    key("keyup", { code: "MetaLeft", key: "Meta", shiftKey: true });
    expect(onRelease).toHaveBeenCalledOnce();
  });

  it("releases when the window loses focus mid-hold, where the keyup never arrives", () => {
    const { onRelease } = setupDictate();
    key("keydown", { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
    act(() => void window.dispatchEvent(new Event("blur")));
    expect(onRelease).toHaveBeenCalledOnce();
    // A second blur is not a second release.
    act(() => void window.dispatchEvent(new Event("blur")));
    expect(onRelease).toHaveBeenCalledOnce();
  });

  it("works while typing in the composer, which is where someone is when they dictate", async () => {
    const { user, onPress } = setupDictate();
    await user.click(screen.getByLabelText("composer"));
    await user.keyboard("{Control>}{Shift>}[Space]{/Shift}{/Control}");
    expect(onPress).toHaveBeenCalledOnce();
    // and typed nothing into it
    expect(screen.getByLabelText("composer")).toHaveValue("");
  });

  it("is not Ctrl+D, Ctrl+Space, Shift+Space or a bare Space, and a keyup with no press releases nothing", async () => {
    const { user, onPress, onRelease } = setupDictate();
    await user.keyboard("{Control>}d{/Control}");
    await user.keyboard("{Control>}[Space]{/Control}");
    await user.keyboard("{Shift>}[Space]{/Shift}");
    await user.keyboard("[Space]");
    // AltGr on a Brazilian keyboard is Ctrl+Alt: not the chord either.
    key("keydown", { code: "Space", key: " ", ctrlKey: true, altKey: true, shiftKey: true });
    expect(onPress).not.toHaveBeenCalled();
    expect(onRelease).not.toHaveBeenCalled();
  });

  it("keeps a hold across a re-render with new handlers, so the release is not lost", () => {
    const { onPress, view } = setupDictate();
    key("keydown", { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
    expect(onPress).toHaveBeenCalledOnce();
    const nextRelease = vi.fn();
    view.rerender(<DictateHarness onPress={vi.fn()} onRelease={nextRelease} />);
    key("keyup", { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
    expect(nextRelease).toHaveBeenCalledOnce();
  });
});
