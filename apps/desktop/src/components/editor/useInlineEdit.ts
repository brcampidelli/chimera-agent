import { useCallback, useEffect, useRef, useState } from "react";

import type { InlineEditState } from "@/components/editor/InlineDiff";
import { streamCodeTurn } from "@/lib/api";
import { proposalOf, type LineSelection } from "@/lib/editorSelection";
import { readLastSession, writeLastSession } from "@/lib/workspace";

/** One "change this" in flight or waiting on the person: the lines it is about and where it stands. */
export interface InlineEdit {
  selection: LineSelection;
  instruction: string;
  state: InlineEditState;
}

/**
 * "Change this": ask the agent for a replacement of some lines, and hold its answer as a proposal.
 *
 * The turn goes into the project's conversation (the one the Code screen opens for this project),
 * so the request and the answer are there to follow up on — the Editor does not keep a second,
 * private history of talking to the agent. It is sent with `propose_change`, which the server runs
 * read-only whatever the posture (`chimera/api/proposed_change.py`), and the posture sent here says
 * the same thing so the receipt describes the turn that ran. So the answer can only be a proposal:
 * the file changes when the person presses Accept, and through the Editor's own save.
 */
export function useInlineEdit(workspace: string | null) {
  const [edit, setEdit] = useState<InlineEdit | null>(null);
  const abort = useRef<AbortController | null>(null);
  // A turn still streaming when the screen goes away has nobody to show its proposal to.
  useEffect(() => () => abort.current?.abort(), []);

  const start = useCallback(
    (selection: LineSelection, instruction: string) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setEdit({ selection, instruction, state: { status: "drafting" } });
      const settle = (state: InlineEditState) => {
        if (abort.current !== controller) return; // superseded or cancelled
        abort.current = null;
        setEdit((current) => (current ? { ...current, state } : current));
      };
      let streamed = "";
      void streamCodeTurn(
        {
          message: instruction,
          session_id: workspace ? readLastSession(workspace) : null,
          workspace,
          open_file: selection.path,
          posture: { reach: "read_only", approval: "suspicious" },
          // The lines, as the composer's `@` points at them (the server reads them from disk and
          // fences them as data), and the one thing a proposal adds: propose, do not write.
          context: [{ kind: "file", path: selection.path, start_line: selection.startLine, end_line: selection.endLine }],
          propose_change: true,
        },
        {
          onSession: (id) => {
            if (workspace) writeLastSession(workspace, id);
          },
          onToken: (text) => {
            streamed += text;
          },
          onDone: (done) => {
            const answer = done.answer || streamed;
            const replacement = proposalOf(answer);
            settle(replacement === null ? { status: "prose", answer } : { status: "ready", replacement });
          },
          onError: (message) => settle({ status: "failed", message }),
        },
        controller.signal,
      );
    },
    [workspace],
  );

  /** Stop waiting for the answer. The turn on the server is the conversation's to stop or keep. */
  const cancel = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setEdit(null);
  }, []);

  /** Accepted or rejected: the proposal is gone either way. */
  const clear = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setEdit(null);
  }, []);

  return { edit, start, cancel, clear };
}
