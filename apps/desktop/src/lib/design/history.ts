/**
 * Undo and redo as two stacks of operation batches.
 *
 * Each entry keeps the batch that was applied (`forward`) and the batch that undoes it
 * (`inverse`, from `applyOps`). Undo applies `inverse`; the inverse of THAT is the new redo entry,
 * which is why `undo`/`redo` hand the ops back instead of a document: the caller applies them with
 * `applyOps` and records what came back via `afterUndo` / `afterRedo`.
 *
 * Coalescing: a drag, or a run of arrow-key nudges, is many batches and should be one step to
 * undo. Entries recorded with the same `coalesceKey` within `COALESCE_MS` of each other merge —
 * the merged inverse runs the newer inverse first, then the older one, which is exactly the order
 * that unwinds them.
 *
 * Agent edits: the agent saves on the server, so the app never holds its operations — it holds the
 * revision the canvas was at before (`revisions.from`) and the one the agent left (`revisions.to`).
 * Such an entry carries no operations; stepping it restores `revisions.from` from the design's saved
 * history (`/restore`, which writes the old content as a NEW revision and never rewrites history),
 * and the entry it becomes points the other way, so redo restores what the agent made. Manual
 * entries around it keep working, because operations are about element ids, not revision numbers.
 *
 * Pure: every function returns a new history; nothing here touches the document.
 */
import type { DesignOperation } from "@/lib/design/model";

export const COALESCE_MS = 1_000;
export const MAX_ENTRIES = 200;

export interface HistoryEntry {
  forward: DesignOperation[];
  inverse: DesignOperation[];
  /** i18n key naming what the step did, for "Undone: …" announcements. */
  label: string;
  coalesceKey?: string;
  at: number;
  /** An agent edit (see above): stepping this entry restores revision `from`. No operations. */
  revisions?: { from: number; to: number };
}

export interface History {
  past: HistoryEntry[];
  future: HistoryEntry[];
}

export function emptyHistory(): History {
  return { past: [], future: [] };
}

export function canUndo(history: History): boolean {
  return history.past.length > 0;
}

export function canRedo(history: History): boolean {
  return history.future.length > 0;
}

/** Record a newly applied batch. Clears the redo stack, as every editor does. */
export function record(history: History, entry: HistoryEntry): History {
  if (entry.forward.length === 0 && entry.revisions === undefined) return history;
  const last = history.past[history.past.length - 1];
  if (
    last !== undefined &&
    entry.coalesceKey !== undefined &&
    last.coalesceKey === entry.coalesceKey &&
    last.revisions === undefined &&
    entry.revisions === undefined &&
    entry.at - last.at <= COALESCE_MS
  ) {
    const merged: HistoryEntry = {
      forward: [...last.forward, ...entry.forward],
      inverse: [...entry.inverse, ...last.inverse],
      label: entry.label,
      coalesceKey: entry.coalesceKey,
      at: entry.at,
    };
    return { past: [...history.past.slice(0, -1), merged], future: [] };
  }
  const past = [...history.past, entry];
  return { past: past.slice(Math.max(0, past.length - MAX_ENTRIES)), future: [] };
}

/** The entry to undo, or undefined. Apply `entry.inverse`, then call `afterUndo`. */
export function peekUndo(history: History): HistoryEntry | undefined {
  return history.past[history.past.length - 1];
}

/** The entry to redo, or undefined. Same contract: apply `entry.inverse`, then call `afterRedo`. */
export function peekRedo(history: History): HistoryEntry | undefined {
  return history.future[history.future.length - 1];
}

/**
 * Move the top entry to the redo stack. `reapply` is the inverse `applyOps` returned when the
 * undo batch ran — the batch that redoes it.
 */
export function afterUndo(history: History, reapply: DesignOperation[]): History {
  const entry = peekUndo(history);
  if (entry === undefined) return history;
  const redo: HistoryEntry = { ...entry, forward: entry.inverse, inverse: reapply, coalesceKey: undefined, revisions: flip(entry) };
  return { past: history.past.slice(0, -1), future: [...history.future, redo] };
}

export function afterRedo(history: History, unapply: DesignOperation[]): History {
  const entry = peekRedo(history);
  if (entry === undefined) return history;
  const back: HistoryEntry = { ...entry, forward: entry.inverse, inverse: unapply, coalesceKey: undefined, revisions: flip(entry) };
  return { past: [...history.past, back], future: history.future.slice(0, -1) };
}

/** The revision pair of an agent entry, pointing the other way once it has been stepped. */
function flip(entry: HistoryEntry): HistoryEntry["revisions"] {
  return entry.revisions && { from: entry.revisions.to, to: entry.revisions.from };
}

/** Record an agent edit: the canvas moved from revision `from` to `to` on the server. */
export function recordAgentEdit(history: History, from: number, to: number, label: string, at: number): History {
  if (from === to) return history;
  return record(history, { forward: [], inverse: [], label, at, revisions: { from, to } });
}
