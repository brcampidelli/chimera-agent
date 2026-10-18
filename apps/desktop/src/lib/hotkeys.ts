import { useEffect, useRef } from "react";

/** Cmd on macOS, Ctrl everywhere else. */
function chord(e: KeyLike): boolean {
  return e.metaKey || e.ctrlKey;
}

/**
 * True when the keystroke belongs to whatever the user is typing into.
 *
 * Without this, ⌘N inside the composer would discard a half-written message. A shortcut that
 * destroys work is worse than no shortcut.
 */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/**
 * True when the keystroke lands in a field that holds something — a draft a shortcut could lose.
 *
 * The conversation shortcuts use this rather than `isTyping`, and the difference is what makes them
 * usable at all: the composer takes focus whenever a conversation opens, so under `isTyping` the
 * first Ctrl+Tab worked and every one after it landed in the next conversation's empty composer and
 * did nothing. An EMPTY field is not someone typing, and switching away from it loses nothing — the
 * draft lives in the conversation's own state and goes with it, which is exactly why a field with
 * text in it still stops every one of them.
 */
function holdsDraft(target: EventTarget | null): boolean {
  if (!isTyping(target)) return false;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return target.value !== "";
  return true;
}

/** What a keydown carries that a shortcut is matched on — the DOM event and React's both have it. */
export interface KeyLike {
  key: string;
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

export interface ShortcutDefinition {
  id: string;
  labelKey: string;
  /**
   * The chord, written the way the help sheet shows it. `Mod` is ⌘ on macOS and Ctrl elsewhere;
   * `Ctrl` is Ctrl on every system, for the two chords where ⌘ belongs to the OS (⌘Tab switches
   * applications, so conversations cycle on Ctrl+Tab on a Mac too, as they do in Safari).
   */
  keys: string;
}

/**
 * Every keyboard shortcut the app has, in the order the help sheet lists them.
 *
 * One table, read by three things: the listeners below match on it, the help sheet (⌘;) renders it,
 * and the command palette shows each command's chord from it. Before it, the chords lived as `if`s
 * in the listener and nowhere else, so the only way to learn one was to read this file — and a sheet
 * written separately would have started drifting from the listener the day a key changed.
 */
export const SHORTCUTS: readonly ShortcutDefinition[] = [
  { id: "palette", labelKey: "hotkeys.palette", keys: "Mod+K" },
  { id: "settings", labelKey: "hotkeys.settings", keys: "Mod+," },
  { id: "newChat", labelKey: "hotkeys.newChat", keys: "Mod+N" },
  { id: "navigate1", labelKey: "hotkeys.navigate1", keys: "Mod+1" },
  { id: "navigate2", labelKey: "hotkeys.navigate2", keys: "Mod+2" },
  { id: "navigate3", labelKey: "hotkeys.navigate3", keys: "Mod+3" },
  { id: "navigate4", labelKey: "hotkeys.navigate4", keys: "Mod+4" },
  { id: "navigate5", labelKey: "hotkeys.navigate5", keys: "Mod+5" },
  { id: "reopenConversation", labelKey: "hotkeys.reopenConversation", keys: "Mod+Shift+T" },
  { id: "previousConversation", labelKey: "hotkeys.previousConversation", keys: "Ctrl+Shift+Tab" },
  { id: "nextConversation", labelKey: "hotkeys.nextConversation", keys: "Ctrl+Tab" },
  { id: "find", labelKey: "hotkeys.find", keys: "Mod+F" },
  { id: "findNext", labelKey: "hotkeys.findNext", keys: "F3" },
  { id: "findPrevious", labelKey: "hotkeys.findPrevious", keys: "Shift+F3" },
  { id: "findClose", labelKey: "hotkeys.findClose", keys: "Escape" },
  { id: "focusMode", labelKey: "hotkeys.focusMode", keys: "Mod+Shift+F" },
  { id: "maximize", labelKey: "hotkeys.maximize", keys: "Mod+Shift+M" },
  { id: "approval", labelKey: "hotkeys.approval", keys: "Mod+Shift+A" },
  { id: "toggleLeft", labelKey: "hotkeys.toggleLeft", keys: "Mod+B" },
  { id: "toggleRight", labelKey: "hotkeys.toggleRight", keys: "Mod+Alt+B" },
  { id: "dictate", labelKey: "hotkeys.dictate", keys: "Mod+Shift+Space" },
  // Answered inside the Editor's CodeMirror keymap, and only with a selection: with none, the key
  // keeps CodeMirror's own meaning (Mod-L selects the line, Mod-I the enclosing syntax).
  { id: "editorAsk", labelKey: "hotkeys.editorAsk", keys: "Mod+L" },
  { id: "editorChange", labelKey: "hotkeys.editorChange", keys: "Mod+I" },
  // Answered by the Editor screen itself (it owns the project whose files are listed), and on a
  // capture listener so CodeMirror's own binding for the key does not take it first.
  { id: "editorQuickOpen", labelKey: "hotkeys.editorQuickOpen", keys: "Mod+P" },
  { id: "zoomIn", labelKey: "hotkeys.zoomIn", keys: "Mod+=" },
  { id: "zoomOut", labelKey: "hotkeys.zoomOut", keys: "Mod+-" },
  { id: "zoomReset", labelKey: "hotkeys.zoomReset", keys: "Mod+0" },
  { id: "help", labelKey: "hotkeys.help", keys: "Mod+;" },
];

function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
}

/** A chord as this system names its keys: `Mod+Shift+T` reads ⌘+Shift+T on a Mac, Ctrl+Shift+T elsewhere. */
export function formatKeys(keys: string): string {
  return keys
    .split("+")
    .map((part) => (part === "Mod" ? (isMac() ? "⌘" : "Ctrl") : part === "Escape" ? "Esc" : part))
    .join("+");
}

/** The displayed chord of one shortcut, or undefined for an id the table does not have. */
export function shortcutLabel(id: string): string | undefined {
  const keys = SHORTCUTS.find((shortcut) => shortcut.id === id)?.keys;
  return keys === undefined ? undefined : formatKeys(keys);
}

/**
 * Does this keydown press shortcut `id`? Modifiers are matched EXACTLY, so ⌘⇧F (focus mode) is
 * never also ⌘F (find), and ⌘B never also ⌘⌥B.
 *
 * Letters and Space go by the physical key (`code`): on a Brazilian (ABNT2) keyboard Ctrl+Alt is
 * AltGr, and `key` then carries whatever AltGr+B produces rather than "b". Digits and punctuation go
 * by the character, because that is where those keys move between layouts.
 */
export function matchesShortcut(e: KeyLike, id: string): boolean {
  const definition = SHORTCUTS.find((shortcut) => shortcut.id === id);
  if (!definition) return false;
  const parts = definition.keys.split("+");
  const key = parts[parts.length - 1];
  const mods = new Set(parts.slice(0, -1));
  if (mods.has("Mod")) {
    if (!chord(e)) return false;
  } else if (mods.has("Ctrl")) {
    if (!e.ctrlKey || e.metaKey) return false;
  } else if (e.ctrlKey || e.metaKey) {
    return false;
  }
  // "=" lives under "+" on most layouts, and Ctrl++ is what people type to zoom in.
  const shiftOptional = key === "=";
  if (!shiftOptional && mods.has("Shift") !== e.shiftKey) return false;
  if (mods.has("Alt") !== e.altKey) return false;
  if (/^[A-Z]$/.test(key)) return e.code === `Key${key}`;
  if (key === "Space") return e.code === "Space";
  if (key === "=") return e.key === "=" || e.key === "+";
  return e.key === key;
}

export interface Hotkeys {
  onPalette: () => void;
  onSettings: () => void;
  onNewChat: () => void;
  /** 1-based rail position. */
  onNavigate: (index: number) => void;
  /** Hide or show a side region: ⌘B the left, ⌘⌥B the right. Optional so a screen can leave it out. */
  onToggleRegion?: (side: "left" | "right") => void;
  /** ⌘⇧F: focus mode on and off. */
  onFocusMode?: () => void;
  /** ⌘⇧M: maximise the panel that holds focus, or restore the one that is maximised. */
  onMaximize?: () => void;
  /** ⌘⇧A: go to the approval waiting in the conversation. */
  onApproval?: () => void;
  /** ⌘;: the sheet that lists this table. */
  onHelp?: () => void;
  /** ⌘= / ⌘- / ⌘0: interface zoom, one step up or down, or back to 100%. */
  onZoom?: (direction: "in" | "out" | "reset") => void;
}

/**
 * Application shortcuts.
 *
 * ⌘K is the only one that fires while typing — a palette exists precisely to be reachable without
 * moving your hands, and it opens over the field rather than acting on it.
 */
export function useHotkeys({
  onPalette,
  onSettings,
  onNewChat,
  onNavigate,
  onToggleRegion,
  onFocusMode,
  onMaximize,
  onApproval,
  onHelp,
  onZoom,
}: Hotkeys): void {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (!chord(e)) return;

      if (matchesShortcut(e, "palette")) {
        e.preventDefault();
        onPalette();
        return;
      }

      if (isTyping(e.target)) return;

      const bindings: [string, (() => void) | undefined][] = [
        ["settings", onSettings],
        ["newChat", onNewChat],
        ["focusMode", onFocusMode],
        ["maximize", onMaximize],
        ["approval", onApproval],
        ["toggleLeft", onToggleRegion && (() => onToggleRegion("left"))],
        ["toggleRight", onToggleRegion && (() => onToggleRegion("right"))],
        ["help", onHelp],
        ["zoomIn", onZoom && (() => onZoom("in"))],
        ["zoomOut", onZoom && (() => onZoom("out"))],
        ["zoomReset", onZoom && (() => onZoom("reset"))],
        ...[1, 2, 3, 4, 5].map((n): [string, () => void] => [`navigate${n}`, () => onNavigate(n)]),
      ];
      for (const [id, run] of bindings) {
        if (run && matchesShortcut(e, id)) {
          e.preventDefault();
          run();
          return;
        }
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onPalette, onSettings, onNewChat, onNavigate, onToggleRegion, onFocusMode, onMaximize, onApproval, onHelp, onZoom]);
}

export interface ConversationHotkeys {
  /** ⌘⇧T: the conversation most recently left, back on screen. */
  onReopen?: () => void;
  /** Ctrl+⇧Tab / Ctrl+Tab: the conversation above or below this one in the sidebar. */
  onPrevious?: () => void;
  onNext?: () => void;
  /** ⌘F: find in this conversation. */
  onFind?: () => void;
  /** F3 / ⇧F3: the next or previous match, while the find bar is open. */
  onFindNext?: () => void;
  onFindPrevious?: () => void;
}

/**
 * The conversation screen's shortcuts. Installed by that screen, so they exist only while it is
 * mounted: ⌘F anywhere else is the WebView's own, and is left alone.
 *
 * None fires in a field that holds text (see `holdsDraft`): Ctrl+Tab over a half-written message
 * does nothing at all rather than swapping the conversation out from under it. And Ctrl+Tab is
 * never Tab, so moving focus with Tab and Shift+Tab is untouched. The find bar's own field answers
 * F3, Enter and Escape itself.
 */
export function useConversationHotkeys(handlers: ConversationHotkeys): void {
  const ref = useRef(handlers);
  ref.current = handlers;
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (holdsDraft(e.target)) return;
      const h = ref.current;
      const bindings: [string, (() => void) | undefined][] = [
        ["reopenConversation", h.onReopen],
        ["previousConversation", h.onPrevious],
        ["nextConversation", h.onNext],
        ["find", h.onFind],
        ["findNext", h.onFindNext],
        ["findPrevious", h.onFindPrevious],
      ];
      for (const [id, run] of bindings) {
        if (run && matchesShortcut(e, id)) {
          e.preventDefault();
          run();
          return;
        }
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}


/** Hold to dictate: ⌘⇧Space on macOS, Ctrl+Shift+Space elsewhere — by the physical key, like ⌘B.
 *
 *  Not Ctrl+D, the obvious letter: that one is the browser's "bookmark this page", and the app is
 *  also served to a plain browser tab. Not a bare Ctrl+Space either: on Windows that toggles the
 *  Chinese and Japanese input methods. Space is the same key on every layout, ABNT2 included. */
export function isDictateChord(e: KeyboardEvent): boolean {
  return matchesShortcut(e, "dictate");
}

/** The keys whose release ends a hold: the space bar or any modifier of the chord. A modifier
 *  counts because macOS sends no keyup for a key released while ⌘ is down — letting go of ⌘ first
 *  is the only release the window ever hears. */
function releasesDictation(e: KeyboardEvent): boolean {
  return e.code === "Space" || e.key === "Shift" || e.key === "Control" || e.key === "Meta";
}

/**
 * Push-to-talk inside the window: `onPress` when the dictation chord goes down, `onRelease` when
 * any key of it comes up — or when the window loses focus mid-hold, because the keyup then goes
 * to another application and a recording would otherwise run until someone noticed the button.
 *
 * The one shortcut besides ⌘K that works while typing, and on purpose: the composer is where
 * someone is when they want to dictate into it, and dictation appends to the draft rather than
 * acting on it, so it can destroy nothing. A held key repeats; only the first keydown is a press.
 *
 * The handlers are kept in refs so the listener is installed once: re-installing it on every
 * render would forget, mid-hold, that a hold was in progress — and the release would be lost.
 */
export function useDictateKey(onPress: () => void, onRelease: () => void): void {
  const press = useRef(onPress);
  const release = useRef(onRelease);
  press.current = onPress;
  release.current = onRelease;
  useEffect(() => {
    let held = false;
    function end() {
      if (!held) return;
      held = false;
      release.current();
    }
    function down(e: KeyboardEvent) {
      if (!isDictateChord(e)) return;
      e.preventDefault();
      if (e.repeat || held) return;
      held = true;
      press.current();
    }
    function up(e: KeyboardEvent) {
      if (held && releasesDictation(e)) end();
    }
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", end);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", end);
    };
  }, []);
}
