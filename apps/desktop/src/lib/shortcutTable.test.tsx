import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  matchesShortcut,
  SHORTCUTS,
  useConversationHotkeys,
  useHotkeys,
  type ConversationHotkeys,
  type Hotkeys,
  type KeyLike,
} from "@/lib/hotkeys";

/** The keydown a person makes for `keys`, on a US layout, with Ctrl as the platform chord. */
function eventFor(keys: string): KeyLike {
  const parts = keys.split("+");
  const key = parts[parts.length - 1];
  const has = (m: string) => parts.slice(0, -1).includes(m);
  const code = /^[A-Z]$/.test(key) ? `Key${key}` : key === "Space" ? "Space" : /^\d$/.test(key) ? `Digit${key}` : key;
  return {
    key: key === "Space" ? " " : /^[A-Z]$/.test(key) ? key.toLowerCase() : key,
    code,
    ctrlKey: has("Mod") || has("Ctrl"),
    metaKey: false,
    shiftKey: has("Shift"),
    altKey: has("Alt"),
  };
}

function Harness({ app, conversation }: { app: Hotkeys; conversation: ConversationHotkeys }) {
  useHotkeys(app);
  useConversationHotkeys(conversation);
  return (
    <>
      <textarea aria-label="composer" />
      <button type="button">first</button>
      <button type="button">second</button>
    </>
  );
}

function setup() {
  const app = {
    onPalette: vi.fn(),
    onSettings: vi.fn(),
    onNewChat: vi.fn(),
    onNavigate: vi.fn(),
    onToggleRegion: vi.fn(),
    onFocusMode: vi.fn(),
    onMaximize: vi.fn(),
    onApproval: vi.fn(),
    onHelp: vi.fn(),
    onZoom: vi.fn(),
  };
  const conversation = {
    onReopen: vi.fn(),
    onPrevious: vi.fn(),
    onNext: vi.fn(),
    onFind: vi.fn(),
    onFindNext: vi.fn(),
    onFindPrevious: vi.fn(),
  };
  render(<Harness app={app} conversation={conversation} />);
  const all = [...Object.values(app), ...Object.values(conversation)];
  return { app, conversation, calls: () => all.reduce((n, f) => n + f.mock.calls.length, 0) };
}

function press(target: Element | Window, keys: string) {
  return fireEvent.keyDown(target, eventFor(SHORTCUTS.find((s) => s.keys === keys)?.keys ?? keys));
}

/**
 * One table of shortcuts, read by the listeners, the help sheet and the palette (C4, study of the
 * Claude desktop app, 16/10/2026). These pin the two things a table can get wrong on its own: two
 * rows answering the same keys, and a row that nothing answers.
 */
describe("the shortcut table", () => {
  it("gives every shortcut a chord no other shortcut answers", () => {
    // ⌘F (find) and ⌘⇧F (focus mode) are one Shift apart; matching modifiers loosely made one key
    // two commands. Every row against every row.
    for (const row of SHORTCUTS) {
      const e = eventFor(row.keys);
      expect(matchesShortcut(e, row.id), `${row.id} does not answer its own keys`).toBe(true);
      const others = SHORTCUTS.filter((o) => o.id !== row.id && matchesShortcut(e, o.id)).map((o) => o.id);
      expect(others, `${row.keys} also answers`).toEqual([]);
    }
  });

  it("calls the command each row names", () => {
    const { app, conversation } = setup();
    const expected: Record<string, () => void> = {
      palette: () => expect(app.onPalette).toHaveBeenCalledOnce(),
      settings: () => expect(app.onSettings).toHaveBeenCalledOnce(),
      newChat: () => expect(app.onNewChat).toHaveBeenCalledOnce(),
      navigate1: () => expect(app.onNavigate).toHaveBeenLastCalledWith(1),
      navigate2: () => expect(app.onNavigate).toHaveBeenLastCalledWith(2),
      navigate3: () => expect(app.onNavigate).toHaveBeenLastCalledWith(3),
      navigate4: () => expect(app.onNavigate).toHaveBeenLastCalledWith(4),
      navigate5: () => expect(app.onNavigate).toHaveBeenLastCalledWith(5),
      reopenConversation: () => expect(conversation.onReopen).toHaveBeenCalledOnce(),
      previousConversation: () => expect(conversation.onPrevious).toHaveBeenCalledOnce(),
      nextConversation: () => expect(conversation.onNext).toHaveBeenCalledOnce(),
      find: () => expect(conversation.onFind).toHaveBeenCalledOnce(),
      findNext: () => expect(conversation.onFindNext).toHaveBeenCalledOnce(),
      findPrevious: () => expect(conversation.onFindPrevious).toHaveBeenCalledOnce(),
      focusMode: () => expect(app.onFocusMode).toHaveBeenCalledOnce(),
      maximize: () => expect(app.onMaximize).toHaveBeenCalledOnce(),
      approval: () => expect(app.onApproval).toHaveBeenCalledOnce(),
      toggleLeft: () => expect(app.onToggleRegion).toHaveBeenLastCalledWith("left"),
      toggleRight: () => expect(app.onToggleRegion).toHaveBeenLastCalledWith("right"),
      zoomIn: () => expect(app.onZoom).toHaveBeenLastCalledWith("in"),
      zoomOut: () => expect(app.onZoom).toHaveBeenLastCalledWith("out"),
      zoomReset: () => expect(app.onZoom).toHaveBeenLastCalledWith("reset"),
      help: () => expect(app.onHelp).toHaveBeenCalledOnce(),
    };
    // Answered elsewhere, and tested there: Escape by the find bar's own field (FindBar), the
    // dictation chord by useDictateKey, which needs the keyup too, and the two selection chords by
    // the Editor's CodeMirror keymap, only with a selection (Edit.selection.test.tsx).
    const elsewhere = new Set(["findClose", "dictate", "editorAsk", "editorChange"]);
    expect(SHORTCUTS.map((s) => s.id).filter((id) => !elsewhere.has(id)).sort()).toEqual(Object.keys(expected).sort());
    for (const row of SHORTCUTS) {
      if (elsewhere.has(row.id)) continue;
      const e = press(window, row.keys);
      expect(e, `${row.keys} left its default action`).toBe(false); // fireEvent returns !defaultPrevented
      expected[row.id]();
    }
  });

  it("fires nothing while the user is typing, except the palette", async () => {
    const { app, calls } = setup();
    const composer = screen.getByLabelText("composer");
    await userEvent.setup().type(composer, "half a message");
    for (const row of SHORTCUTS) {
      if (row.id === "palette") continue;
      // Not prevented either: ⌘F, ⌘0 or Ctrl+Tab in a field belong to the field and the WebView.
      expect(press(composer, row.keys), `${row.keys} was taken from the composer`).toBe(true);
    }
    expect(calls()).toBe(0);
    press(composer, "Mod+K");
    expect(app.onPalette).toHaveBeenCalledOnce();
  });

  it("lets the conversation's shortcuts act from an EMPTY field, and never the app's", () => {
    // The composer takes focus whenever a conversation opens. An empty one is nobody typing, and
    // there is nothing in it to lose, so Ctrl+Tab from it walks on; ⌘N and the rest still wait.
    const { app, conversation } = setup();
    const composer = screen.getByLabelText("composer");
    press(composer, "Ctrl+Tab");
    press(composer, "Mod+N");
    press(composer, "Mod+0");
    expect(conversation.onNext).toHaveBeenCalledOnce();
    expect(app.onNewChat).not.toHaveBeenCalled();
    expect(app.onZoom).not.toHaveBeenCalled();
  });

  it("leaves Tab and Shift+Tab to move focus, and Ctrl+Tab is not ⌘Tab", async () => {
    const { conversation } = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "first" }));
    await user.tab();
    expect(screen.getByRole("button", { name: "second" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "first" })).toHaveFocus();
    expect(conversation.onNext).not.toHaveBeenCalled();
    expect(conversation.onPrevious).not.toHaveBeenCalled();
    // ⌘Tab is the Mac's application switcher; the conversation cycle is Ctrl+Tab there too.
    fireEvent.keyDown(window, { key: "Tab", code: "Tab", metaKey: true });
    expect(conversation.onNext).not.toHaveBeenCalled();
  });

  it("answers ⌘ as the chord on a Mac and Ctrl+Shift++ as zoom in", () => {
    const { app, conversation } = setup();
    fireEvent.keyDown(window, { key: "t", code: "KeyT", metaKey: true, shiftKey: true });
    expect(conversation.onReopen).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: "+", code: "Equal", ctrlKey: true, shiftKey: true });
    expect(app.onZoom).toHaveBeenLastCalledWith("in");
  });

  it("does not take ⌘F from the WebView where no conversation screen answers it", () => {
    render(<Harness app={{ onPalette: vi.fn(), onSettings: vi.fn(), onNewChat: vi.fn(), onNavigate: vi.fn() }} conversation={{}} />);
    expect(fireEvent.keyDown(window, { key: "f", code: "KeyF", ctrlKey: true })).toBe(true);
  });
});
