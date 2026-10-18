import { useEffect, useRef } from "react";

import type { TurnContextItem } from "@/lib/types";

/**
 * What a Code turn carries beside its message: the things the person pointed at.
 *
 * One model for every sender. The composer's `@` list adds files, folders, designs and conversations;
 * anything outside the conversation screen — the Editor's "send the selection to the agent" — adds a
 * file with a line range through {@link requestAddContext}, the same way the palette asks the screen
 * to open a conversation. The composer shows each as a chip and sends them all as `context` with the
 * next turn, where the server decides what each becomes (`chimera/api/turn_context.py`).
 *
 * The wire item is the server's type, generated from its schema, so the two cannot drift. `label` is
 * the screen's own: a design or a conversation travels by id, and a chip reading an id would tell
 * the person nothing.
 */
export interface ContextEntry {
  item: TurnContextItem;
  /** What the chip says: a path, a design's title, a conversation's title. */
  label: string;
}

/** How many a message may carry. The server refuses more (`MAX_CONTEXT_ITEMS`). */
export const MAX_CONTEXT_ITEMS = 20;

/** Whether `path` names something inside the project: relative, no drive, no `..` step.
 *
 *  The server refuses the same paths, and checks again where they resolve; this is so a chip that
 *  would be refused is never drawn as if it were going to be sent. */
export function isProjectPath(path: string): boolean {
  const text = path.trim().replace(/\\/g, "/");
  if (!text || text.startsWith("/") || /^[A-Za-z]:/.test(text)) return false;
  return !text.split("/").includes("..");
}

/** One key per thing, so the same file mentioned twice is one chip. */
export function contextKey(item: TurnContextItem): string {
  switch (item.kind) {
    case "file":
      return `file:${item.path}:${item.start_line ?? ""}-${item.end_line ?? ""}`;
    case "folder":
      return `folder:${item.path}`;
    case "design":
      return `design:${item.id}`;
    case "conversation":
      return `conversation:${item.id}`;
  }
}

/** Why `entry` cannot join `current`, or null when it can. */
export function refuseEntry(
  current: readonly ContextEntry[],
  entry: ContextEntry,
): "outside" | "full" | null {
  const { item } = entry;
  if ((item.kind === "file" || item.kind === "folder") && !isProjectPath(item.path)) return "outside";
  const key = contextKey(item);
  if (current.some((e) => contextKey(e.item) === key)) return null;
  return current.length >= MAX_CONTEXT_ITEMS ? "full" : null;
}

/** `current` with `entry` added once, at the end; unchanged when it is already there. */
export function addEntry(current: readonly ContextEntry[], entry: ContextEntry): ContextEntry[] {
  const key = contextKey(entry.item);
  return current.some((e) => contextKey(e.item) === key) ? [...current] : [...current, entry];
}

export const ADD_CONTEXT_EVENT = "chimera:add-context";

/** Ask the conversation screen to attach `entry` to the next message. Nothing happens when no
 *  conversation screen is mounted — the same rule as the palette's requests. */
export function requestAddContext(entry: ContextEntry): void {
  window.dispatchEvent(new CustomEvent<ContextEntry>(ADD_CONTEXT_EVENT, { detail: entry }));
}

/** Take the entries other screens ask to attach, while the calling component is mounted. */
export function useAddContextRequest(run: (entry: ContextEntry) => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function onAdd(event: Event) {
      const detail = (event as CustomEvent<ContextEntry>).detail;
      if (detail && typeof detail.label === "string" && detail.item) ref.current(detail);
    }
    window.addEventListener(ADD_CONTEXT_EVENT, onAdd);
    return () => window.removeEventListener(ADD_CONTEXT_EVENT, onAdd);
  }, []);
}
