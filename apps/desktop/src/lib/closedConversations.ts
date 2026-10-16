/**
 * The conversations this app session has closed, newest last — what ⌘⇧T reopens.
 *
 * "Closed" is the conversation screen putting a different conversation where this one was: a new
 * one, another from the sidebar, the next one by Ctrl+Tab, a project switch. The screen has no tabs
 * to close, and leaving is the moment a person loses the one they were in.
 *
 * Module state, not the screen's: the conversation screen unmounts whenever another screen is shown,
 * and a stack held in its state would be emptied by a visit to Settings. Not persisted either — a
 * closed-tabs list is about this sitting, like a browser's, and the sidebar already holds every
 * conversation for good.
 */
export interface ClosedConversation {
  id: string;
  workspace: string;
}

export const CLOSED_LIMIT = 20;

const stack: ClosedConversation[] = [];

export function rememberClosed(conversation: ClosedConversation): void {
  // Once, at the top: closing the same conversation twice should not take two presses to get past.
  const at = stack.findIndex((c) => c.id === conversation.id);
  if (at >= 0) stack.splice(at, 1);
  stack.push(conversation);
  if (stack.length > CLOSED_LIMIT) stack.splice(0, stack.length - CLOSED_LIMIT);
}

/**
 * The most recently closed conversation that can still be opened, taken off the stack.
 *
 * `exists` is asked of each candidate so a conversation deleted since it was closed is passed over
 * rather than reopened as an empty transcript under a dead id; the one on screen is passed over too,
 * since reopening it would do nothing a person could see.
 */
export function takeClosed(
  exists: (id: string) => boolean,
  onScreen: string | null,
): ClosedConversation | null {
  while (stack.length > 0) {
    const candidate = stack.pop() as ClosedConversation;
    if (candidate.id !== onScreen && exists(candidate.id)) return candidate;
  }
  return null;
}

/** For tests: the stack is module state and would otherwise leak from one test into the next. */
export function forgetClosed(): void {
  stack.length = 0;
}
