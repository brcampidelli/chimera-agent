import { useEffect, useRef } from "react";

/**
 * Commands a screen owns but something outside it can ask for.
 *
 * "Reopen the closed conversation" and "find in conversation" act on state that lives in the
 * conversation screen, while the command palette is built in `App`, above every screen. Lifting that
 * state up to `App` to reach it would move the conversation's bookkeeping into the shell; asking by
 * name instead keeps one implementation — the screen's — and lets any caller (the palette today, a
 * native window menu later) name it without a copy of the logic.
 *
 * A request nobody listens to does nothing, which is the right answer: the palette only offers these
 * while the conversation screen is mounted, and a menu item for a screen that is not open has
 * nothing to act on.
 */
export type AppCommand =
  | "reopenConversation"
  | "previousConversation"
  | "nextConversation"
  | "find";

export const APP_COMMAND_EVENT = "chimera:command";

export function requestAppCommand(command: AppCommand): void {
  window.dispatchEvent(new CustomEvent<AppCommand>(APP_COMMAND_EVENT, { detail: command }));
}

/** Answer `command` while the calling component is mounted. */
export function useAppCommand(command: AppCommand, run: () => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function onCommand(event: Event) {
      if ((event as CustomEvent<AppCommand>).detail === command) ref.current();
    }
    window.addEventListener(APP_COMMAND_EVENT, onCommand);
    return () => window.removeEventListener(APP_COMMAND_EVENT, onCommand);
  }, [command]);
}

/** A stored conversation someone outside the conversation screen asked to open — the palette's
 *  conversation entries. A payload, so it is its own event rather than one more `AppCommand` name. */
export interface OpenConversationRequest {
  id: string;
  workspace: string;
}

export const OPEN_CONVERSATION_EVENT = "chimera:open-conversation";

export function requestOpenConversation(request: OpenConversationRequest): void {
  window.dispatchEvent(new CustomEvent<OpenConversationRequest>(OPEN_CONVERSATION_EVENT, { detail: request }));
}

/** Open the requested conversation while the calling component (the conversation screen) is mounted. */
export function useOpenConversationRequest(run: (request: OpenConversationRequest) => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function onOpen(event: Event) {
      const detail = (event as CustomEvent<OpenConversationRequest>).detail;
      if (detail && typeof detail.id === "string") ref.current(detail);
    }
    window.addEventListener(OPEN_CONVERSATION_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_CONVERSATION_EVENT, onOpen);
  }, []);
}
