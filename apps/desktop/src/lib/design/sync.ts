/**
 * Keeping the canvas and the server's copy of a design in step.
 *
 * The canvas applies every edit locally first (`applyOps`) and queues the operations; autosave sends
 * the queue to `/ops` against the revision it last saw. When the design moved on in the meantime (the
 * agent saved, another window saved), the server answers 409 and changes nothing. The client then
 * reads the newer document and replays its unsaved operations on top of it. If they still validate,
 * nothing the person did is lost and the merged result is what gets saved next; if one no longer
 * applies (it moves an element the other side deleted, say), the replay is refused as a whole and
 * the person is told, rather than half their edits landing.
 *
 * Pure: nothing here talks to the network.
 */
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import { applyOps, DesignOpError } from "@/lib/design/ops";

/** The server's cap on one `/ops` batch (`MAX_OPS_PER_CALL` in chimera/design/model.py). */
export const MAX_SAVE_BATCH = 500;

export type Rebased = { ok: true; document: DesignDocument } | { ok: false; code: string };

/** `pending` replayed on top of `remote`, all of it or none of it. */
export function rebase(remote: DesignDocument, pending: readonly DesignOperation[]): Rebased {
  if (pending.length === 0) return { ok: true, document: remote };
  try {
    // Uncapped: the queue may hold several batches' worth, and each is re-checked by the server.
    const { document } = applyOps(remote, pending, { maxOperations: Number.MAX_SAFE_INTEGER });
    // The revision is the server's to count; the replay's local bump would claim one it never made.
    return { ok: true, document: { ...document, revision: remote.revision, updatedAt: remote.updatedAt } };
  } catch (error) {
    if (error instanceof DesignOpError) return { ok: false, code: error.code };
    throw error;
  }
}
