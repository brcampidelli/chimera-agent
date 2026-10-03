import { postSuggestionEvent } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
import type { SuggestionEvent } from "@/lib/types";

/**
 * What to say next, offered under the last answer: at most three suggestions, each derived from a
 * FACT of the turn and never from a model (study 29, P4.5; a model-written suggestion is in the
 * plan's "not doing" list — it spends tokens on behaviour nobody measured).
 *
 * - the turn's check failed and was not undone → "try to fix it", with the failure in the text;
 * - an item of the agent's own task list is still open → "continue: <item>";
 * - the turn changed files that git still reports as changed → "commit <files>".
 *
 * They FILL the box and never send, for the reason the empty screen's examples give: the first thing
 * anyone does with a suggestion is read it, often edit it, and a click that spends money before that
 * is a worse offer than none.
 */
export type SuggestionKind = "fix" | "continue" | "commit";

export interface Suggestion {
  kind: SuggestionKind;
  /** The chip's words — short. */
  label: string;
  /** What lands in the box. */
  text: string;
}

/** The facts of one finished turn the suggestions are read from. */
export interface TurnFacts {
  /** The text "try to fix it" would send — the failure and the original request — present only
   *  when the turn's check failed and its edits were not undone. */
  fixText?: string;
  /** The agent's own task list, as its last frame left it. */
  todos: { task: string; status: string }[];
  /** The files the turn wrote that git still reports as changed, workspace-relative and in the order
   *  they were written — as the server answered it (`POST /api/git/uncommitted`), or null when that
   *  is not known (no repository, not asked yet). Null offers no commit: "these may be uncommitted"
   *  is a guess, not a fact.
   *
   *  Decided by the server and not matched here: comparing the agent's path for a file with git's
   *  path for it went wrong three ways that never raised — an absolute path never matched, a file in
   *  a new folder never matched (git names the folder), and a suffix match made a clean `a.py` the
   *  dirty `vendor/a.py`. Both paths are only comparable where the workspace and the repository
   *  root are both known. */
  uncommitted: string[] | null;
}

/** At most this many. Three kinds, so the cap is also a promise that none is ever doubled. */
export const MAX_SUGGESTIONS = 3;
/** A task line longer than this is cut on the chip; the box gets all of it. */
const LABEL_CHARS = 60;
/** File names on the commit chip; the rest are counted. */
const FILES_ON_CHIP = 2;

function cut(text: string): string {
  return text.length > LABEL_CHARS ? `${text.slice(0, LABEL_CHARS - 1)}…` : text;
}

export function turnSuggestions(facts: TurnFacts, t: TFunc): Suggestion[] {
  const out: Suggestion[] = [];
  if (facts.fixText) {
    out.push({ kind: "fix", label: t("code.suggest.fix"), text: facts.fixText });
  }
  const open = facts.todos.find((item) => item.status !== "done" && item.task.trim());
  if (open) {
    const item = open.task.trim();
    out.push({
      kind: "continue",
      label: t("code.suggest.continue", { item: cut(item) }),
      text: t("code.suggest.continueText", { item }),
    });
  }
  // Not after a failed check: committing what the check just rejected is the one suggestion here
  // that could make things worse, and "try to fix it" is already the next step on offer.
  if (!facts.fixText && facts.uncommitted) {
    const files = [...new Set(facts.uncommitted)];
    if (files.length > 0) {
      const names = files.slice(0, FILES_ON_CHIP).map((path) => path.split("/").pop() ?? path);
      const more = files.length - names.length;
      out.push({
        kind: "commit",
        label: more > 0
          ? t("code.suggest.commitMore", { files: names.join(", "), n: more })
          : t("code.suggest.commit", { files: names.join(", ") }),
        text: t("code.suggest.commitText", { files: files.join(", ") }),
      });
    }
  }
  return out.slice(0, MAX_SUGGESTIONS);
}

/** Count one event of a suggestion — the kind and what happened, never its words — for the rate the
 *  plan measures them by (`/api/suggestions/stats`). Fire-and-forget and never throwing: a click must
 *  not wait on a statistic, and a server that cannot record it costs a sample, not the click. */
export function recordSuggestion(event: SuggestionEvent): void {
  try {
    void Promise.resolve(postSuggestionEvent(event)).catch(() => undefined);
  } catch {
    // An older server without the route, or no server at all: the suggestion still worked.
  }
}
