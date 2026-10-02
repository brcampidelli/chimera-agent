import { useEffect } from "react";

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
