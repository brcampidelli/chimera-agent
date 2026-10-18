import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  applyDesignOps,
  getDesign,
  restoreDesignVersion,
  type DesignProposal,
} from "@/lib/api";
import { emptyHistory, record, recordAgentEdit, type History } from "@/lib/design/history";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import type { ApplyResult } from "@/lib/design/ops";
import { MAX_SAVE_BATCH, rebase } from "@/lib/design/sync";

/** What the save indicator says. `error` is a refusal that is neither a conflict nor the network. */
export type SaveState = "saved" | "saving" | "conflict" | "offline" | "error";

/** Why the last save needed attention, for the sentence under the indicator. */
export type SaveNote = "merged" | "dropped" | "refused" | "stale" | "versionGone" | null;

export type LoadState = "loading" | "ready" | "missing" | "denied" | "failed";

/** Autosave waits this long after the last edit, so a drag is one save rather than sixty. */
export const AUTOSAVE_MS = 600;
/** How long an offline save waits before trying again (the `online` event also wakes it). */
export const OFFLINE_RETRY_MS = 5_000;

export interface DesignSession {
  load: LoadState;
  doc: DesignDocument | null;
  /** The revision the server has confirmed. */
  savedRevision: number;
  save: SaveState;
  note: SaveNote;
  history: History;
  setHistory: (next: History) => void;
  /** The workspace's `onOps`: keep the result, queue the operations for autosave. */
  onOps: (ops: DesignOperation[], result: ApplyResult) => void;
  /** Save whatever is queued now, and wait for it. */
  flush: () => Promise<void>;
  /** Apply a proposal the owner approved. False when it no longer fits the design. */
  applyProposal: (proposal: DesignProposal) => Promise<boolean>;
  /** The agent saved this design (a `design` frame): read it, and make it one step to undo. */
  agentEdited: (revision: number) => Promise<void>;
  /** Undo or redo an agent edit: restore `revision` from the saved history. */
  stepRevision: (revision: number) => Promise<boolean>;
  /** Restore an old revision from the versions panel (already confirmed); undoable as one step. */
  restore: (revision: number) => Promise<boolean>;
  /** Read the server's copy again, dropping nothing that is still queued. */
  reload: () => Promise<void>;
}

function isNetworkError(error: unknown): boolean {
  return !(error instanceof ApiError);
}

function statusOf(error: unknown): number {
  return error instanceof ApiError ? error.status : 0;
}

/**
 * One design, open on the canvas: the server's copy, the local copy the person is editing, the queue
 * between them and the undo history over both. Every way the document changes goes through here, so
 * the four ways it can change (the person, the agent, an approved proposal, a restore) agree on what
 * the server holds.
 */
export function useDesignSession(id: string, autosaveMs: number = AUTOSAVE_MS): DesignSession {
  const [load, setLoad] = useState<LoadState>("loading");
  const [doc, setDocState] = useState<DesignDocument | null>(null);
  const [savedRevision, setSavedRevision] = useState(0);
  const [save, setSave] = useState<SaveState>("saved");
  const [note, setNote] = useState<SaveNote>(null);
  const [history, setHistoryState] = useState<History>(emptyHistory);
  const historyRef = useRef(history);
  const base = useRef<DesignDocument | null>(null);
  const local = useRef<DesignDocument | null>(null);
  const pending = useRef<DesignOperation[]>([]);
  const inflight = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const setHistory = useCallback((next: History) => {
    historyRef.current = next;
    setHistoryState(next);
  }, []);

  /** The server's copy changed: adopt it as the base, and the local copy as `next` (default: it). */
  const adopt = useCallback((server: DesignDocument, next: DesignDocument = server) => {
    base.current = server;
    local.current = next;
    if (!alive.current) return;
    setSavedRevision(server.revision);
    setDocState(next);
  }, []);

  /** Replay the queue over a newer server copy, or drop it and say so. */
  const reconcile = useCallback(
    (remote: DesignDocument, merged: SaveNote = "merged"): boolean => {
      const replay = rebase(remote, pending.current);
      if (replay.ok) {
        adopt(remote, replay.document);
        if (pending.current.length > 0) setNote(merged);
        return true;
      }
      pending.current = [];
      adopt(remote);
      // The undo stack described edits that are gone; stepping it would refuse every entry.
      setHistory(emptyHistory());
      setSave("conflict");
      setNote("dropped");
      return false;
    },
    [adopt, setHistory],
  );

  const schedule = useCallback(
    (delay: number) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        void flushRef.current();
      }, delay);
    },
    [],
  );

  const sendOnce = useCallback(async (): Promise<void> => {
    const server = base.current;
    if (!server || pending.current.length === 0) return;
    const batch = pending.current.splice(0, MAX_SAVE_BATCH);
    setSave("saving");
    try {
      const out = await applyDesignOps(id, server.revision, batch);
      const replay = rebase(out.document, pending.current);
      if (replay.ok) adopt(out.document, replay.document);
      else reconcile(out.document);
      if (alive.current && pending.current.length === 0) setSave((s) => (s === "conflict" ? s : "saved"));
      // More than one batch's worth was queued: send the rest now. (Edits made meanwhile have their
      // own timer already.)
      else if (pending.current.length > 0 && !timer.current) schedule(0);
    } catch (error) {
      pending.current = [...batch, ...pending.current];
      if (statusOf(error) === 409) {
        const remote = await getDesign(id).catch(() => null);
        if (remote && reconcile(remote)) schedule(0);
        else if (!remote) setSave("offline");
      } else if (statusOf(error) === 422) {
        // The server refused an edit the canvas allowed: its copy is the truth.
        const remote = await getDesign(id).catch(() => null);
        pending.current = [];
        if (remote) adopt(remote);
        setHistory(emptyHistory());
        setSave("conflict");
        setNote("refused");
      } else if (isNetworkError(error)) {
        setSave("offline");
        schedule(OFFLINE_RETRY_MS);
      } else {
        setSave("error");
      }
    }
  }, [id, adopt, reconcile, schedule, setHistory]);

  const flush = useCallback(async (): Promise<void> => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    // One request at a time: a second would carry the same expected revision and lose the race.
    while (inflight.current) await inflight.current;
    if (pending.current.length === 0) return;
    const run = sendOnce().finally(() => {
      inflight.current = null;
    });
    inflight.current = run;
    await run;
  }, [sendOnce]);
  const flushRef = useRef(flush);
  flushRef.current = flush;

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    setLoad("loading");
    getDesign(id)
      .then((d) => {
        if (cancelled) return;
        adopt(d);
        setLoad("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const status = statusOf(error);
        setLoad(status === 404 ? "missing" : status === 401 || status === 403 ? "denied" : "failed");
      });
    const online = () => {
      if (pending.current.length > 0) void flushRef.current();
    };
    window.addEventListener("online", online);
    return () => {
      cancelled = true;
      window.removeEventListener("online", online);
      // Leaving the design saves what is queued rather than dropping it.
      if (pending.current.length > 0) void flushRef.current();
      alive.current = false;
    };
  }, [id, adopt]);

  const onOps = useCallback(
    (ops: DesignOperation[], result: ApplyResult) => {
      local.current = result.document;
      setDocState(result.document);
      pending.current.push(...ops);
      setSave("saving");
      setNote(null);
      schedule(autosaveMs);
    },
    [autosaveMs, schedule],
  );

  /** Everything queued is saved and nothing is in flight. False when that could not happen. */
  const settle = useCallback(async (): Promise<boolean> => {
    await flush();
    return pending.current.length === 0;
  }, [flush]);

  const applyProposal = useCallback(
    async (proposal: DesignProposal): Promise<boolean> => {
      if (!(await settle())) return false;
      const server = base.current;
      if (!server || server.revision !== proposal.base_revision) {
        setNote("stale");
        return false;
      }
      setSave("saving");
      try {
        const out = await applyDesignOps(id, proposal.base_revision, proposal.operations, "proposal");
        adopt(out.document);
        setHistory(
          record(historyRef.current, {
            forward: proposal.operations,
            inverse: out.inverse,
            label: "design.did.proposal",
            at: Date.now(),
          }),
        );
        setSave("saved");
        setNote(null);
        return true;
      } catch (error) {
        setSave(isNetworkError(error) ? "offline" : "saved");
        if (statusOf(error) === 409 || statusOf(error) === 422) setNote("stale");
        return false;
      }
    },
    [id, adopt, settle, setHistory],
  );

  const agentEdited = useCallback(
    async (revision: number): Promise<void> => {
      const from = base.current?.revision;
      if (from === undefined || revision <= from) return;
      while (inflight.current) await inflight.current;
      const remote = await getDesign(id).catch(() => null);
      if (!remote || remote.revision <= from) return;
      reconcile(remote, "merged");
      setHistory(recordAgentEdit(historyRef.current, from, remote.revision, "design.did.agent", Date.now()));
      if (pending.current.length > 0) schedule(0);
    },
    [id, reconcile, schedule, setHistory],
  );

  const restoreTo = useCallback(
    async (revision: number): Promise<number | null> => {
      if (!(await settle())) return null;
      const server = base.current;
      if (!server) return null;
      setSave("saving");
      try {
        const restored = await restoreDesignVersion(id, revision, server.revision);
        adopt(restored);
        setSave("saved");
        setNote(null);
        return server.revision;
      } catch (error) {
        const status = statusOf(error);
        if (status === 409) {
          const remote = await getDesign(id).catch(() => null);
          if (remote) reconcile(remote);
          setSave("saved");
          setNote("stale");
        } else if (status === 404) {
          // Only the newest 50 revisions are kept; an undo that reaches past them has nothing to read.
          setSave("saved");
          setNote("versionGone");
        } else {
          setSave(isNetworkError(error) ? "offline" : "error");
        }
        return null;
      }
    },
    [id, adopt, reconcile, settle],
  );

  const stepRevision = useCallback(async (revision: number) => (await restoreTo(revision)) !== null, [restoreTo]);

  const restore = useCallback(
    async (revision: number): Promise<boolean> => {
      const from = await restoreTo(revision);
      if (from === null || !base.current) return false;
      setHistory(recordAgentEdit(historyRef.current, from, base.current.revision, "design.did.restore", Date.now()));
      return true;
    },
    [restoreTo, setHistory],
  );

  const reload = useCallback(async () => {
    while (inflight.current) await inflight.current;
    try {
      const remote = await getDesign(id);
      reconcile(remote);
      setLoad("ready");
      if (pending.current.length > 0) schedule(0);
      else setSave("saved");
    } catch (error) {
      if (isNetworkError(error)) setSave("offline");
    }
  }, [id, reconcile, schedule]);

  return {
    load,
    doc,
    savedRevision,
    save,
    note,
    history,
    setHistory,
    onOps,
    flush,
    applyProposal,
    agentEdited,
    stepRevision,
    restore,
    reload,
  };
}
