/**
 * A task the agent proposed as a card (`chimera/tools/task_suggestion.py`). A card is only a
 * proposal: starting it is the owner's click, and the wire carries the prompt the owner reads first.
 */
export interface ProposedTaskCard {
  id: string;
  title: string;
  tldr: string;
  /** The full instruction the new conversation runs. Shown in full before the owner starts it. */
  prompt: string;
  /** The turn that proposed it had read external content: the conversation it starts is tainted. */
  tainted: boolean;
}

/** A card as the conversation shows it: the owner's own outcome for it, kept beside the card. */
export interface TaskCardState extends ProposedTaskCard {
  status?: "starting" | "started" | "failed";
  startedSessionId?: string;
  error?: string;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The card from a `task_suggestion` frame. A frame with no id is not a card and is dropped. */
export function taskCardOf(payload: Record<string, unknown>): ProposedTaskCard | null {
  const id = text(payload.id);
  if (!id) return null;
  return {
    id,
    title: text(payload.title),
    tldr: text(payload.tldr),
    prompt: text(payload.prompt),
    tainted: payload.tainted === true,
  };
}

/**
 * The body of `POST /api/code/task-suggestions/start`. Only the card's id and the owner's options: the
 * title, prompt and taint are the server's record of the card, so nothing the client sends can change them.
 */
export interface TaskSuggestionStartInput {
  origin_session_id: string;
  card_id: string;
  model?: string | null;
  new_worktree?: boolean;
}

/** The most cards one click starts. The rest wait for another click. */
export const START_ALL_LIMIT = 5;
