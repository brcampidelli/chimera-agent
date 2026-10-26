/**
 * The design canvas's shortcuts, in one table read by the key handler AND by the help sheet.
 *
 * Same reasoning as `lib/hotkeys.ts`: a help sheet written separately from the listener starts to
 * drift the day a key changes. Rows with no `chords` are pointer gestures — documented here so the
 * sheet lists them, matched by the canvas's pointer code instead.
 *
 * These act only while the canvas has focus (its own `onKeyDown`), and the canvas stops the event
 * when it handles one, so `Mod+Z` here never reaches the app's own shortcuts.
 */
import { formatKeys } from "@/lib/hotkeys";

export type DesignShortcutId =
  | "toolSelect"
  | "toolText"
  | "toolPan"
  | "toolFrame"
  | "toolNote"
  | "toolRectangle"
  | "toolEllipse"
  | "toolLine"
  | "toolDraw"
  | "toolComment"
  | "move"
  | "moveFar"
  | "selectNext"
  | "selectPrevious"
  | "selectAll"
  | "editText"
  | "deselect"
  | "delete"
  | "undo"
  | "redo"
  | "duplicate"
  | "zoomIn"
  | "zoomOut"
  | "zoomFit"
  | "help"
  | "pan"
  | "zoomWheel"
  | "multiSelect";

export interface DesignShortcut {
  id: DesignShortcutId;
  labelKey: string;
  /** Chords that trigger it, `Mod` = ⌘ on a Mac and Ctrl elsewhere. Empty for a gesture. */
  chords: readonly string[];
  /** What the sheet shows when the chords are not the whole story (arrows, gestures). */
  display?: string;
}

export const DESIGN_SHORTCUTS: readonly DesignShortcut[] = [
  { id: "toolSelect", labelKey: "design.tool.select", chords: ["V"] },
  { id: "toolText", labelKey: "design.tool.text", chords: ["T"] },
  { id: "toolPan", labelKey: "design.tool.pan", chords: ["H"] },
  { id: "toolFrame", labelKey: "design.tool.frame", chords: ["F"] },
  { id: "toolNote", labelKey: "design.tool.note", chords: ["S"] },
  { id: "toolRectangle", labelKey: "design.tool.rectangle", chords: ["R"] },
  { id: "toolEllipse", labelKey: "design.tool.ellipse", chords: ["O"] },
  { id: "toolLine", labelKey: "design.tool.line", chords: ["L"] },
  { id: "toolDraw", labelKey: "design.tool.draw", chords: ["P"] },
  { id: "toolComment", labelKey: "design.tool.comment", chords: ["C"] },
  { id: "move", labelKey: "design.keys.move", chords: ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"], display: "← ↑ → ↓" },
  {
    id: "moveFar",
    labelKey: "design.keys.moveFar",
    chords: ["Shift+ArrowUp", "Shift+ArrowDown", "Shift+ArrowLeft", "Shift+ArrowRight"],
    display: "Shift + ← ↑ → ↓",
  },
  { id: "selectNext", labelKey: "design.keys.selectNext", chords: ["N"] },
  { id: "selectPrevious", labelKey: "design.keys.selectPrevious", chords: ["Shift+N"] },
  { id: "selectAll", labelKey: "design.keys.selectAll", chords: ["Mod+A"] },
  { id: "editText", labelKey: "design.keys.editText", chords: ["Enter"] },
  { id: "deselect", labelKey: "design.keys.deselect", chords: ["Escape"] },
  { id: "delete", labelKey: "design.action.delete", chords: ["Delete", "Backspace"] },
  { id: "undo", labelKey: "design.action.undo", chords: ["Mod+Z"] },
  { id: "redo", labelKey: "design.action.redo", chords: ["Mod+Shift+Z", "Mod+Y"] },
  { id: "duplicate", labelKey: "design.action.duplicate", chords: ["Mod+D"] },
  { id: "zoomIn", labelKey: "design.action.zoomIn", chords: ["+", "="] },
  { id: "zoomOut", labelKey: "design.action.zoomOut", chords: ["-"] },
  { id: "zoomFit", labelKey: "design.action.zoomFit", chords: ["0"] },
  { id: "help", labelKey: "design.keys.help", chords: ["?"] },
  { id: "pan", labelKey: "design.keys.pan", chords: [], display: "Space" },
  { id: "zoomWheel", labelKey: "design.keys.zoomWheel", chords: [], display: "Mod" },
  { id: "multiSelect", labelKey: "design.keys.multiSelect", chords: [], display: "Shift" },
];

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/** Characters whose Shift state is part of the character itself (`?` is Shift+/ on most layouts). */
function shiftIsInTheKey(key: string): boolean {
  return key.length === 1 && !/[a-z]/i.test(key);
}

function matches(chord: string, e: KeyLike): boolean {
  // "+" on its own is the key, not a separator.
  const parts = chord === "+" ? ["+"] : chord.split("+");
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  const mod = e.ctrlKey || e.metaKey;
  if (mods.includes("Mod") !== mod || e.altKey) return false;
  if (!shiftIsInTheKey(key) && mods.includes("Shift") !== e.shiftKey) return false;
  return key.length === 1 ? e.key.toLowerCase() === key.toLowerCase() : e.key === key;
}

/** The shortcut a keydown presses, if any. */
export function matchDesignShortcut(e: KeyLike): DesignShortcutId | undefined {
  return DESIGN_SHORTCUTS.find((s) => s.chords.some((chord) => matches(chord, e)))?.id;
}

/** How the help sheet writes a row's keys on this system. */
export function displayKeys(shortcut: DesignShortcut): string {
  if (shortcut.display !== undefined) return shortcut.display.replace("Mod", formatKeys("Mod"));
  return shortcut.chords.map((chord) => (chord === "+" ? "+" : formatKeys(chord))).join(" / ");
}
