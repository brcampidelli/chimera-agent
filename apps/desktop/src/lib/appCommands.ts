import { useEffect, useRef } from "react";

import type { ContextEntry } from "@/lib/context-items";

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
  | "find"
  | "openDesign"
  | "newDesign";

export const APP_COMMAND_EVENT = "chimera:command";

export function requestAppCommand(command: AppCommand): void {
  window.dispatchEvent(new CustomEvent<AppCommand>(APP_COMMAND_EVENT, { detail: command }));
}

/** A command asked for from another screen, kept until the screen that answers it mounts. */
let queued: AppCommand | null = null;

/**
 * Ask for `command` even when the screen that answers it is not mounted yet: the palette's "Open
 * design" from the Settings screen navigates to Code and then has to be heard there. The request is
 * dispatched now (heard at once when the screen is up) and kept until a listener takes it.
 */
export function queueAppCommand(command: AppCommand): void {
  queued = command;
  requestAppCommand(command);
}

/** Answer `command` while the calling component is mounted. */
export function useAppCommand(command: AppCommand, run: () => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function onCommand(event: Event) {
      if ((event as CustomEvent<AppCommand>).detail !== command) return;
      if (queued === command) queued = null;
      ref.current();
    }
    // One asked for before this screen mounted.
    if (queued === command) {
      queued = null;
      ref.current();
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

/**
 * A turn the Editor asked the conversation to send: "Ask Chimera" on a selection.
 *
 * The question is typed in the Editor and answered in the conversation of the same project, where
 * the person can follow up. The Code screen is not mounted while the Editor is, so the request is
 * kept here until it mounts and takes it — the same shape as `queueAppCommand`, with a payload.
 */
export interface ConversationSeed {
  /** The project it belongs to: the conversation sends it in that project's conversation. */
  workspace: string;
  /** What the person typed. Their words, and the conversation's title if it is a new one. */
  message: string;
  /** What the question is about — the selected lines, as a file with a range. The same item the
   *  composer's `@` sends (`lib/context-items.ts`), so the server has one door for "what the person
   *  pointed at". Unlike `requestAddContext`, this is kept until the conversation screen mounts and
   *  is SENT with the words rather than left as a chip: the person already typed the question. */
  context: ContextEntry;
}

export const CONVERSATION_SEED_EVENT = "chimera:conversation-seed";

let queuedSeed: ConversationSeed | null = null;

export function queueConversationSeed(seed: ConversationSeed): void {
  queuedSeed = seed;
  window.dispatchEvent(new CustomEvent<ConversationSeed>(CONVERSATION_SEED_EVENT, { detail: seed }));
}

/** Take the Editor's request while the calling component (the conversation screen) is mounted. */
export function useConversationSeed(run: (seed: ConversationSeed) => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function take(seed: ConversationSeed) {
      if (queuedSeed === seed) queuedSeed = null;
      ref.current(seed);
    }
    if (queuedSeed) take(queuedSeed);
    function onSeed(event: Event) {
      const detail = (event as CustomEvent<ConversationSeed>).detail;
      if (detail && typeof detail.message === "string") take(detail);
    }
    window.addEventListener(CONVERSATION_SEED_EVENT, onSeed);
    return () => window.removeEventListener(CONVERSATION_SEED_EVENT, onSeed);
  }, []);
}
