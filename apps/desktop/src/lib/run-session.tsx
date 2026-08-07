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
 * The live autonomous run, held above the view switch.
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
 * One run at a time, deliberately: the backend registers one cancel handle per run and the status
 * bar has room for one subject. Starting a second while one is live is refused rather than
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
  /** The run stopped for a human verdict. Not a failure and not a success — an open question. */
  paused: PausedRun | null;
  /**
   * What is about to judge this run, from the frame the server sends BEFORE the first step.
   *
   * Held in the session rather than in the launcher because it must survive navigating away: a run
   * judged by a model reading the answer is the same run whichever screen you are looking at.
   */
  verify: RunVerify | null;
  start: (req: RunRequestInput, handlers?: RunSessionHandlers) => void;
  stop: () => void;
  /** Clear a resolved pause once its resume has been launched. */
  clearPaused: () => void;
}

export interface RunSessionHandlers {
  /** The terminal frame. `success` is the run's own verdict, not "the stream ended". */
  onDone?: (d: RunDone | null, success: boolean) => void;
}

const RunSessionContext = createContext<RunSession | null>(null);

export function RunSessionProvider({ children }: { children: ReactNode }) {
  const [running, setRunning] = useState(false);
  const [task, setTask] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [done, setDone] = useState<RunDone | null>(null);
  const [stopping, setStopping] = useState(false);
  const [broken, setBroken] = useState(false);
  const [paused, setPaused] = useState<PausedRun | null>(null);
  const [verify, setVerify] = useState<RunVerify | null>(null);
  // Read inside the stream callbacks, which close over the value at start(). A ref rather than
  // state because a mid-run subscriber change must reach the in-flight stream, not the next one.
  const handlers = useRef<RunSessionHandlers>({});

  const start = useCallback(
    (req: RunRequestInput, on: RunSessionHandlers = {}) => {
      if (running) return;
      handlers.current = on;
      setRunning(true);
      setStopping(false);
      setBroken(false);
      setRunId(null);
      setEvents([]);
      setDone(null);
      setPaused(null);
      setVerify(null);
      setTask(req.task);

      const finish = (frame: RunDone | null, success: boolean) => {
        setRunning(false);
        setStopping(false);
        setRunId(null);
        setDone(frame);
        handlers.current.onDone?.(frame, success);
      };

      void streamRun(req, {
        onRunId: (id) => setRunId(id),
        onEvent: (e) => setEvents((prev) => [...prev, e]),
        onVerify: (v) => setVerify(v),
        onDone: (d) => finish(d, d.success),
        // A pause resolves the STREAM without resolving the RUN. It ends as neither success nor
        // failure, because it has not reached a verdict — it is waiting on a person.
        onPaused: (p) => {
          setPaused(p);
          finish(null, false);
        },
        // A transport failure is not a verdict. The run may well still be going on the backend;
        // what ended is our view of it, and reporting "failed" would be a guess.
        onError: () => {
          setBroken(true);
          finish(null, false);
        },
      });
    },
    [running],
  );

  const clearPaused = useCallback(() => setPaused(null), []);

  const stop = useCallback(() => {
    if (!runId || stopping) return;
    setStopping(true);
    // Cooperative: the loop halts before its NEXT attempt, so an in-flight model call still
    // finishes. The button stays in "stopping" until the stream's own terminal frame arrives —
    // flipping to idle here would claim a stop the backend has not yet made.
    void cancelRun(runId).catch(() => setStopping(false));
  }, [runId, stopping]);

  const value = useMemo(
    () => ({ running, task, runId, events, done, stopping, broken, paused, verify, start, stop, clearPaused }),
    [running, task, runId, events, done, stopping, broken, paused, verify, start, stop, clearPaused],
  );
  return <RunSessionContext.Provider value={value}>{children}</RunSessionContext.Provider>;
}

/**
 * Read the live run.
 *
 * Returns an inert session outside a provider, for the same reason `useAgent` does: a test that
 * mounts one screen should not have to stand up the whole shell, and "no run is going" is true
 * there.
 */
export function useRunSession(): RunSession {
  return (
    useContext(RunSessionContext) ?? {
      running: false,
      task: "",
      runId: null,
      events: [],
      done: null,
      stopping: false,
      broken: false,
      paused: null,
      verify: null,
      start: () => {},
      stop: () => {},
      clearPaused: () => {},
    }
  );
}
