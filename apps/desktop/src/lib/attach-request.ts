import { useEffect, useRef } from "react";

import type { Attachment } from "@/lib/api";

/**
 * Ask the conversation screen to put files in the next message's tray.
 *
 * The same shape as {@link requestAddContext}: a window event, nothing happens when no conversation is
 * mounted. The Preview panel uses it for an element picked from the page, which is stored on the server
 * as attachments (a text one, fenced there, and a crop) and arrives here as their ids.
 */
export const ADD_ATTACHMENT_EVENT = "chimera:add-attachment";

let listeners = 0;

/** Ask for the chips; true when a conversation screen is mounted to take them, false when nobody is. The
 *  caller must not say "added" for a false, and should discard what it stored for them. */
export function requestAddAttachments(items: Attachment[]): boolean {
  if (listeners === 0) return false;
  window.dispatchEvent(new CustomEvent<Attachment[]>(ADD_ATTACHMENT_EVENT, { detail: items }));
  return true;
}

/** Take the attachments other screens ask for, while the calling component is mounted. */
export function useAddAttachmentRequest(run: (items: Attachment[]) => void): void {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    function onAdd(event: Event) {
      const detail = (event as CustomEvent<Attachment[]>).detail;
      if (Array.isArray(detail)) ref.current(detail);
    }
    window.addEventListener(ADD_ATTACHMENT_EVENT, onAdd);
    listeners += 1;
    return () => {
      listeners -= 1;
      window.removeEventListener(ADD_ATTACHMENT_EVENT, onAdd);
    };
  }, []);
}
