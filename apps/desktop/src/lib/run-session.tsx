import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  cancelRun,
  streamRun,
  type PausedRun,
  type RunDone,
  type RunEvent,
  type RunRequestInput,
  type RunVerify,
} from "@/lib/api";

/**
 * The live autonomous runs, held above the view switch.
 *
 * A run is the one thing in this app that outlives the screen you started it from. It takes
 * minutes, it edits files, and there is nothing to watch while it thinks — so of course you
 * navigate away. Before this, the launcher owned the run in its own `useState`: leaving Work
 * unmounted the component, the progress was gone, and the run kept going with nothing listening
 * and no way to stop it. The agent was working and the app had forgotten.
 *
 * Holding it here is also what lets the status bar tell the truth from every screen, and what
 * gives the global Stop something to stop.
 *
 * One run PER PROJECT, and runs in several projects at once (the owner's decision of 2026-09-30).
 * It used to be one run for the whole app, so a run in one project blocked every other. The server
 * now takes one lock per folder for runs as it does for coding turns, so two writers never share a
 * folder whoever started them; here, a second run in the SAME project is still refused rather than
 * queued — a silent queue is a worse surprise than a disabled button.
 */
export interface RunSession {
  /** True from the moment start() is called until the terminal frame arrives. */
  running: boolean;
  /** The task text of the live (or last) run — what the status bar names. */
  task: string;
  /** The backend's handle for this run, from the first `run` frame. Null until it arrives. */
  runId: string | null;
  /**
   * Every progress event of the run, oldest first. Kept RAW rather than as formatted strings so
   * the wording stays at the render site: this state outlives the screen, and a language change
   * must not leave half a transcript frozen in the previous locale.
   */
  events: RunEvent[];
  /** The terminal frame once it has arrived — null while running, and after a transport failure. */
  done: RunDone | null;
  /** True once Stop has been sent and the run has not yet ended. */
  stopping: boolean;
  /** Set when our view of the run broke (network/stream), which is NOT the run failing. */
  broken: boolean;
  /**
   * The project the in-flight run is working in, or null when nothing is running.
   *
   * Exposed because "a run is in progress" and "a run is in progress HERE" stopped being the same
   * question the moment a second project existed. The reason a screen blocks typing while a run is
   * live is that a turn and a run editing the same directory would race each other — which is not
   * true of a run in another project, and blocking there is a lie about why.
   */
  workspace: string | null;
  /** The run stopped for a human verdict. Not a failure and not a success — an open question. */
  paused: PausedRun | null;
  /**
   * What is about to judge this run, from the frame the server sends BEFORE the first step.
   *
   * Held in the session rather than in the launcher because it must survive navigating away: a run
   * judged by a model reading the answer is the same run whichever screen you are looking at.
   */
  verify: RunVerify | null;
  /** How many runs in OTHER projects are working right now. */
  alsoRunning: number;
  /** Start a run in `req.workspace`. False, and nothing started, when that project already has one. */
  start: (req: RunRequestInput, handlers?: RunSessionHandlers) => boolean;
  stop: () => void;
  /** Clear a resolved pause once its resume has been launched. */
  clearPaused: () => void;
}

export interface RunSessionHandlers {
  /** The terminal frame. `success` is the run's own verdict, not "the stream ended". */
  onDone?: (d: RunDone | null, success: boolean) => void;
}

interface RunState {
  running: boolean;
  task: string;
  runId: string | null;
  events: RunEvent[];
  done: RunDone | null;
  stopping: boolean;
  broken: boolean;
  workspace: string | null;
  paused: PausedRun | null;
  verify: RunVerify | null;
  /** When it started, in starts: the status bar follows the latest. */
  order: number;
}

const IDLE: RunState = {
  running: false, task: "", runId: null, events: [], done: null, stopping: false, broken: false,
  workspace: null, paused: null, verify: null, order: 0,
};

/** The project a run belongs to. The app's own project is sent as no workspace; both are "". */
function keyOf(workspace: string | null | undefined): string {
  return workspace ?? "";
}

interface RunsApi {
  runs: Record<string, RunState>;
  start: (req: RunRequestInput, handlers?: RunSessionHandlers) => boolean;
  stop: (key: string) => void;
  clearPaused: (key: string) => void;
}

const RunSessionContext = createContext<RunsApi | null>(null);

export function RunSessionProvider({ children }: { children: ReactNode }) {
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  // Read synchronously by `start`, so two clicks in one tick cannot both start a run in one project.
  const live = useRef(new Set<string>());
  const current = useRef(runs);
  // Read inside the stream callbacks, which close over the value at start(). A ref rather than
  // state because a mid-run subscriber change must reach the in-flight stream, not the next one.
  const handlers = useRef<Record<string, RunSessionHandlers>>({});
  const starts = useRef(0);

  const patch = useCallback((key: string, fn: (s: RunState) => RunState) => {
    setRuns((prev) => {
      const next = { ...prev, [key]: fn(prev[key] ?? IDLE) };
      current.current = next;
      return next;
    });
  }, []);

  const start = useCallback(
    (req: RunRequestInput, on: RunSessionHandlers = {}) => {
      const key = keyOf(req.workspace);
      if (live.current.has(key)) return false;
      live.current.add(key);
      handlers.current[key] = on;
      starts.current += 1;
      const order = starts.current;
      patch(key, () => ({ ...IDLE, running: true, workspace: req.workspace ?? null, task: req.task, order }));

      const finish = (frame: RunDone | null, success: boolean) => {
        live.current.delete(key);
        patch(key, (s) => ({ ...s, running: false, stopping: false, runId: null, done: frame }));
        handlers.current[key]?.onDone?.(frame, success);
      };

      void streamRun(req, {
        onRunId: (id) => patch(key, (s) => ({ ...s, runId: id })),
        onEvent: (e) => patch(key, (s) => ({ ...s, events: [...s.events, e] })),
        onVerify: (v) => patch(key, (s) => ({ ...s, verify: v })),
        onDone: (d) => finish(d, d.success),
        // A pause resolves the STREAM without resolving the RUN. It ends as neither success nor
        // failure, because it has not reached a verdict — it is waiting on a person.
        onPaused: (p) => {
          patch(key, (s) => ({ ...s, paused: p }));
          finish(null, false);
        },
        // A transport failure is not a verdict. The run may well still be going on the backend;
        // what ended is our view of it, and reporting "failed" would be a guess.
        onError: () => {
          patch(key, (s) => ({ ...s, broken: true }));
          finish(null, false);
        },
      });
      return true;
    },
    [patch],
  );

  const stop = useCallback(
    (key: string) => {
      const s = current.current[key];
      if (!s?.runId || s.stopping) return;
      patch(key, (x) => ({ ...x, stopping: true }));
      // Cooperative: the loop halts before its NEXT attempt, so an in-flight model call still
      // finishes. The button stays in "stopping" until the stream's own terminal frame arrives —
      // flipping to idle here would claim a stop the backend has not yet made.
      void cancelRun(s.runId).catch(() => patch(key, (x) => ({ ...x, stopping: false })));
    },
    [patch],
  );

  const clearPaused = useCallback((key: string) => patch(key, (s) => ({ ...s, paused: null })), [patch]);

  const value = useMemo(() => ({ runs, start, stop, clearPaused }), [runs, start, stop, clearPaused]);
  return <RunSessionContext.Provider value={value}>{children}</RunSessionContext.Provider>;
}

/** The run the status bar names: the latest one still working, else the latest one at all. */
function focusedKey(runs: Record<string, RunState>): string | null {
  const all = Object.entries(runs);
  const running = all.filter(([, s]) => s.running);
  const pool = running.length > 0 ? running : all;
  if (pool.length === 0) return null;
  return pool.reduce((a, b) => (b[1].order > a[1].order ? b : a))[0];
}

const INERT: RunSession = {
  ...IDLE,
  alsoRunning: 0,
  start: () => false,
  stop: () => {},
  clearPaused: () => {},
};

/**
 * Read a run.
 *
 * With a `workspace`, that project's run: the screen working in a project asks about its own. With
 * none, the run the status bar names — the latest one still working — and how many others are.
 *
 * Returns an inert session outside a provider, for the same reason `useAgent` does: a test that
 * mounts one screen should not have to stand up the whole shell, and "no run is going" is true
 * there.
 */
export function useRunSession(workspace?: string | null): RunSession {
  const api = useContext(RunSessionContext);
  const runs = api?.runs;
  return useMemo(() => {
    if (!api || !runs) return INERT;
    const key = workspace === undefined ? focusedKey(runs) : keyOf(workspace);
    const s = key !== null ? (runs[key] ?? IDLE) : IDLE;
    const alsoRunning = Object.entries(runs).filter(([k, r]) => r.running && k !== key).length;
    return {
      ...s,
      // "Null when nothing is running", as it always was: a finished run's project is not busy.
      workspace: s.running ? s.workspace : null,
      alsoRunning,
      start: api.start,
      stop: () => {
        if (key !== null) api.stop(key);
      },
      clearPaused: () => {
        if (key !== null) api.clearPaused(key);
      },
    };
  }, [api, runs, workspace]);
}
