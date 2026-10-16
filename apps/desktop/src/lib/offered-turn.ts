/** A turn the app OFFERS to a conversation: text placed in its composer, never sent.
 *
 * The pull request card (on the Work screen) can offer "the CI failed; fix it" to the Code
 * conversation the PR was opened from. The two screens do not share a component tree, so the offer
 * travels through storage, keyed by conversation, and the conversation takes it once when it opens.
 *
 * Taking it only fills the draft. Sending stays the owner's press of the send button, so the turn is
 * an ordinary one with the ordinary governance — nothing here can start a turn on its own.
 */
const PREFIX = "chimera:code:offered-turn:";

export function offerTurn(sessionId: string, text: string): void {
  try {
    localStorage.setItem(PREFIX + sessionId, text);
  } catch {
    // Storage blocked: the offer is lost, and the owner can still type the turn themselves.
  }
}

/** The offered text for this conversation, removed as it is read; null when there is none. */
export function takeOfferedTurn(sessionId: string): string | null {
  try {
    const text = localStorage.getItem(PREFIX + sessionId);
    if (text !== null) localStorage.removeItem(PREFIX + sessionId);
    return text;
  } catch {
    return null;
  }
}
