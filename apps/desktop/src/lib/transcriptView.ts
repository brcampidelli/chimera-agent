import type { CardKind } from "@/lib/layout/model";

/**
 * The default transcript view: how a newly opened conversation starts (Settings › Appearance).
 *
 * Every view is made of things the conversation could already show or hide, nothing new:
 *
 * - **normal** — the per-kind card preferences exactly as they are. Someone who never opens the row
 *   sees the conversation as it was before the row existed.
 * - **reasoning** (labelled "Steps") — also opens the tool-call list, the record of what the agent did.
 *   The model's own internal reasoning is NOT in the stream — its events are token, tool, edit, todo,
 *   notice, chart, approval, error and done — so this view cannot show it and the label does not
 *   promise it. The value keeps the name the setting was specified with, so a later stream that
 *   carries reasoning can give it real content.
 * - **detailed** — opens every card kind, and inside the tool list unfolds each tool's output and
 *   shows its full arguments instead of only the first one.
 *
 * It is a STARTING point. A per-kind change made in the conversation (minimise all tool cards) wins
 * over the view for that kind on that screen; a view that re-opened what the person just closed would
 * be a preference that cannot be turned off.
 */
export const TRANSCRIPT_VIEW_KEY = "chimera.transcriptView";
/** Fired on `window` in the window that changed the view; other windows hear `storage`. */
export const TRANSCRIPT_VIEW_EVENT = "chimera:transcript-view";
export const TRANSCRIPT_VIEWS = ["normal", "reasoning", "detailed"] as const;
export type TranscriptView = (typeof TRANSCRIPT_VIEWS)[number];

export function getTranscriptView(): TranscriptView {
  try {
    const value = localStorage.getItem(TRANSCRIPT_VIEW_KEY);
    if (value === "normal" || value === "reasoning" || value === "detailed") return value;
  } catch {
    // A private or sandboxed window still gets the normal presentation.
  }
  return "normal";
}

export function setTranscriptView(value: TranscriptView): void {
  try {
    localStorage.setItem(TRANSCRIPT_VIEW_KEY, value);
  } catch {
    // The current window can still use this choice; it just will not survive a restart.
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(TRANSCRIPT_VIEW_EVENT));
}

/** Whether the view opens this card kind regardless of its per-kind preference. */
export function viewOpensKind(view: TranscriptView, kind: CardKind): boolean {
  if (view === "detailed") return true;
  if (view === "reasoning") return kind === "tools";
  return false;
}

/** Whether tool rows unfold their output and show every argument. Only Detailed does: Steps is the
 *  list of what ran, not the dump of what each step returned. */
export function viewShowsToolDetail(view: TranscriptView): boolean {
  return view === "detailed";
}
