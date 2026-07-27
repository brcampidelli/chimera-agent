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
}

/**
 * Application shortcuts.
 *
 * ⌘K is the only one that fires while typing — a palette exists precisely to be reachable without
 * moving your hands, and it opens over the field rather than acting on it.
 */
export function useHotkeys({ onPalette, onSettings, onNewChat, onNavigate }: Hotkeys): void {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (!chord(e)) return;

      if (e.key.toLowerCase() === "k") {
        e.preventDefault();
        onPalette();
        return;
      }

      if (isTyping(e.target)) return;

      if (e.key === ",") {
        e.preventDefault();
        onSettings();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        onNewChat();
      } else if (/^[1-5]$/.test(e.key)) {
        e.preventDefault();
        onNavigate(Number(e.key));
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onPalette, onSettings, onNewChat, onNavigate]);
}
