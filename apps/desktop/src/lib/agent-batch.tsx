import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import {
  cancelAgents,
  streamAgents,
  type AgentTaggedEvent,
  type Approval,
  type Profile,
  type Reach,
} from "@/lib/api";
import type { AgentsBatch } from "@/lib/types";

/**
 * The parallel batch (several agents, each in its own worktree), held above the view that draws it.
 *
 * The board used to own the batch in its own `useState` and start it on mount. That made the board
 * the batch: a board that was closed, minimised to its header or moved to the side panel would have
 * unmounted, and with it every live line and every result, while the worktrees kept running with
 * nothing listening. The owner asked on 2026-10-17 for the board to be minimisable, movable and
 * closable like the other panels. That is only safe if hiding it hides a VIEW: so the stream lives
 * here, and the only things that stop a task are the board's own Stop and Stop all.
 */

/** What each task card shows while it runs: the tagged frames, kept raw so the wording stays at the
 *  render site (a language change must not leave half a board in the previous locale). */
export type BatchLiveEvent = Pick<AgentTaggedEvent, "kind" | "text" | "path">;

/** Live frames kept per task. A card shows the last four; this bounds a long batch's memory. */
const LIVE_KEPT = 32;

export interface BatchRun {
  /** Which confirmed batch this is. A second confirmation is a new batch, not a restart. */
  key: number;
  workspace: string;
  /** The submitted task texts, in order. */
  labels: string[];
  live: Record<number, BatchLiveEvent[]>;
  /** The terminal frame, once it lands. */
  batch: AgentsBatch | null;
  error: string | null;
  /** The cancel handle, from the first `batch` frame; null before it and after the batch ends. */
  batchId: string | null;
  /** Tasks whose Stop was requested: they halt after their current attempt. */
  stopping: Record<number, boolean>;
  running: boolean;
}

export interface BatchStart {
  tasks: string[];
  workspace: string;
  /** The same reach/approval a single run gets: a batch must not be structurally weaker. */
  posture: { reach: Reach; approval: Approval };
  profile: Profile;
}

export interface AgentBatchApi {
  run: BatchRun | null;
  /** Start a confirmed batch. Ignored when no task is left after trimming. */
  start: (req: BatchStart) => void;
  /** Cooperative Stop of one task, before its next attempt. */
  stopTask: (index: number) => Promise<void>;
  /** The same Stop, for every task in the batch. */
  stopAll: () => Promise<void>;
  /** Forget a FINISHED batch's results. A running batch cannot be dismissed: forgetting it would be
   *  the batch running with nothing on screen and no Stop, which is what this module exists to end. */
  dismiss: () => void;
}

const AgentBatchContext = createContext<AgentBatchApi | null>(null);

export function AgentBatchProvider({ children }: { children: ReactNode }) {
  const [run, setRun] = useState<BatchRun | null>(null);
  // The latest run, for the async Stop handlers and to ignore frames from a superseded batch.
  const current = useRef<BatchRun | null>(null);

  const update = useCallback((key: number, change: (prev: BatchRun) => BatchRun) => {
    setRun((prev) => {
      // A frame from a batch that is no longer the one on screen changes nothing.
      if (!prev || prev.key !== key) return prev;
      const next = change(prev);
      current.current = next;
      return next;
    });
  }, []);

  const start = useCallback(
    (req: BatchStart) => {
      const submitted = req.tasks.map((task) => task.trim()).filter(Boolean);
      if (submitted.length === 0) return;
      // `Date.now()` alone can repeat within a millisecond in a test; the counter cannot.
      const key = Math.max(Date.now(), (current.current?.key ?? 0) + 1);
      const fresh: BatchRun = {
        key,
        workspace: req.workspace,
        labels: submitted,
        live: {},
        batch: null,
        error: null,
        batchId: null,
        stopping: {},
        running: true,
      };
      current.current = fresh;
      setRun(fresh);
      const ended = (prev: BatchRun): BatchRun => ({ ...prev, running: false, batchId: null, stopping: {} });
      void streamAgents(
        {
          // No verify command, no model, no fusion mode: the system resolves each from the project and
          // the profile, the same way a single run does.
          tasks: submitted.map((task) => ({ task, verify: null })),
          workspace: req.workspace || null,
          max_workers: Math.min(submitted.length, 4),
          posture: req.posture,
          profile: req.profile,
        },
        {
          onBatchId: (id) => update(key, (prev) => ({ ...prev, batchId: id })),
          onStart: (s) => update(key, (prev) => ({ ...prev, labels: s.tasks })),
          onEvent: (e) => {
            if (typeof e.index !== "number") return;
            const index = e.index;
            const frame: BatchLiveEvent = { kind: e.kind, text: e.text, path: e.path };
            update(key, (prev) => ({
              ...prev,
              live: { ...prev.live, [index]: [...(prev.live[index] ?? []), frame].slice(-LIVE_KEPT) },
            }));
          },
          onBatchDone: (b) => update(key, (prev) => ({ ...ended(prev), batch: b })),
          onError: (msg) => update(key, (prev) => ({ ...ended(prev), error: msg })),
        },
      );
    },
    [update],
  );

  const stopTask = useCallback(
    async (index: number) => {
      const live = current.current;
      if (!live?.batchId || live.stopping[index]) return;
      const { key, batchId } = live;
      update(key, (prev) => ({ ...prev, stopping: { ...prev.stopping, [index]: true } }));
      try {
        await cancelAgents(batchId, index);
      } catch {
        // A cancel that fails (the batch just finished) is a no-op: `batch_done` clears the state.
      }
    },
    [update],
  );

  const stopAll = useCallback(async () => {
    const live = current.current;
    if (!live?.batchId) return;
    const { key, batchId, labels } = live;
    update(key, (prev) => ({ ...prev, stopping: Object.fromEntries(labels.map((_, i) => [i, true])) }));
    try {
      await cancelAgents(batchId, null);
    } catch {
      // As above: a batch that already ended has nothing to cancel.
    }
  }, [update]);

  const dismiss = useCallback(() => {
    setRun((prev) => {
      if (!prev || prev.running) return prev;
      current.current = null;
      return null;
    });
  }, []);

  const value = useMemo<AgentBatchApi>(
    () => ({ run, start, stopTask, stopAll, dismiss }),
    [run, start, stopTask, stopAll, dismiss],
  );
  return <AgentBatchContext.Provider value={value}>{children}</AgentBatchContext.Provider>;
}

/** The batch, or null outside the provider: a panel window of its own has no batch to draw, and draws
 *  nothing rather than throwing. */
export function useOptionalAgentBatch(): AgentBatchApi | null {
  return useContext(AgentBatchContext);
}

export function useAgentBatch(): AgentBatchApi {
  const api = useContext(AgentBatchContext);
  if (!api) throw new Error("useAgentBatch must be used inside <AgentBatchProvider>");
  return api;
}

/** The numbers a one-line summary is made of. While the batch runs, the server reports no per-task
 *  end (results arrive together in `batch_done`), so `passed`/`failed` are only known once it ends;
 *  a count guessed from the live attempt lines would be a number nobody measured. */
export interface BatchCounts {
  total: number;
  running: boolean;
  passed: number;
  failed: number;
  stopping: number;
  error: boolean;
}

export function batchCounts(run: BatchRun): BatchCounts {
  const results = run.batch?.results ?? [];
  return {
    total: run.batch ? results.length : run.labels.length,
    running: run.running,
    passed: results.filter((r) => r.success).length,
    failed: results.filter((r) => !r.success).length,
    stopping: Object.values(run.stopping).filter(Boolean).length,
    error: run.error !== null,
  };
}
