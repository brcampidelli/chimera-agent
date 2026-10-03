import { useEffect, useRef } from "react";

/** Cmd on macOS, Ctrl everywhere else. */
function chord(e: KeyboardEvent): boolean {
  return e.metaKey || e.ctrlKey;
}

/**
 * True when the keystroke belongs to whatever the user is typing into.
 *
 * Without this, ⌘N inside the composer would discard a half-written message. A shortcut that
 * destroys work is worse than no shortcut.
 */
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
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
}: Hotkeys): void {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (!chord(e)) return;

      if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        onPalette();
        return;
      }

      if (isTyping(e.target)) return;

      // The shifted chords, by physical key like ⌘B above, and before the unshifted ones below.
      if (e.shiftKey && e.code === "KeyF" && onFocusMode) {
        e.preventDefault();
        onFocusMode();
        return;
      }
      if (e.shiftKey && e.code === "KeyM" && onMaximize) {
        e.preventDefault();
        onMaximize();
        return;
      }
      if (e.shiftKey && e.code === "KeyA" && onApproval) {
        e.preventDefault();
        onApproval();
        return;
      }

      if (e.key === ",") {
        e.preventDefault();
        onSettings();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        onNewChat();
      } else if (/^[1-5]$/.test(e.key)) {
        e.preventDefault();
        onNavigate(Number(e.key));
      } else if (e.code === "KeyB" && onToggleRegion) {
        // By the physical key, not the character: on a Brazilian (ABNT2) keyboard Ctrl+Alt is AltGr,
        // and `e.key` then carries whatever AltGr+B produces rather than "b".
        e.preventDefault();
        onToggleRegion(e.altKey ? "right" : "left");
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onPalette, onSettings, onNewChat, onNavigate, onToggleRegion, onFocusMode, onMaximize, onApproval]);
}

/** Hold to dictate: ⌘⇧Space on macOS, Ctrl+Shift+Space elsewhere — by the physical key, like ⌘B.
 *
 *  Not Ctrl+D, the obvious letter: that one is the browser's "bookmark this page", and the app is
 *  also served to a plain browser tab. Not a bare Ctrl+Space either: on Windows that toggles the
 *  Chinese and Japanese input methods. Space is the same key on every layout, ABNT2 included. */
export function isDictateChord(e: KeyboardEvent): boolean {
  return chord(e) && e.shiftKey && !e.altKey && e.code === "Space";
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
