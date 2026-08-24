// The hierarchy stream, folded into something a screen can render. Pure: no React, no fetch, so
// the interesting property below can be tested without a DOM.
//
// That property is idempotence. The frames carry a server-stamped `seq`, and `applyFrame` ignores
// any frame it has already seen. That one line is what lets a reloaded page ask for everything
// after the last number it had and push those frames through the SAME function the live stream
// uses — one code path, no "replay mode" that drifts from the real one.
//
// Workers are an array in decomposition order rather than a map, because the order the frames
// ARRIVE in is not an order at all: N workers run in parallel and the server has no sequence to
// offer beyond the one it stamps. Sorting cards by arrival would reshuffle the fan-out on every
// reload. The decomposition names them once, in order, and that order is the display's.

import type { OrchFrame } from "@/lib/api";

export type WorkerStatus = "queued" | "running" | "verified" | "rejected";

/** Why a worker produced nothing usable. `no_output` is a budget or provider fault, `verifier` is
 *  a judgement with a stage behind it, `deadline` is the batch bound firing. Kept apart because
 *  collapsing them reads a provider outage as a model that cannot follow a contract. */
export type RejectReason =
  | "no_output"
  | "verifier"
  | "deadline"
  // The worker was CUT OFF mid-run rather than producing nothing. It used to arrive as
  // `no_output`, which reads as a provider fault — but a budget cut is the one case a user can
  // act on, by raising the budget, and folding the two together hid that.
  | "budget"
  | "max_steps"
  | "tool_loop"
  | "cancelled"
  | "";

export interface WorkerState {
  taskId: string;
  objective: string;
  status: WorkerStatus;
  /** "top" for a subtask small enough to be answered inline, "mid" for a delegated worker. */
  tier: string;
  stage: string;
  /** Which verification gates actually RAN. See `checks_run` in `envelope_verify.py`. */
  checksRun: string[];
  detail: string;
  reason: RejectReason;
  reasked: boolean;
  tokens: number;
  /** The summary's SIZE. The text itself never travels on a progress frame. */
  summaryChars: number;
  gaps: string[];
  /** Non-empty means this worker's output did not FIT the summary cap: what the synthesis read
   *  is a head+tail slice, and the whole of it was written to these paths. */
  evidenceRefs: string[];
}

export interface OrchestrationTotals {
  tokens: number | null;
  counterfactual: number | null;
}

export interface OrchestrationState {
  runId: string | null;
  seq: number;
  stage: "idle" | "classified" | "decomposed" | "working" | "synthesizing" | "done" | "error";
  shape: string | null;
  sources: number;
  /** Set when the orchestrator chose the single-agent path. NOT an error — see FellBackNote. */
  fellBack: { reason: string; shape: string } | null;
  fused: boolean;
  workers: WorkerState[];
  answer: string | null;
  cancelled: boolean;
  totals: OrchestrationTotals | null;
  error: string | null;
}

export const EMPTY_RUN: OrchestrationState = {
  runId: null,
  seq: 0,
  stage: "idle",
  shape: null,
  sources: 0,
  fellBack: null,
  fused: false,
  workers: [],
  answer: null,
  cancelled: false,
  totals: null,
  error: null,
};

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function patchWorker(
  workers: WorkerState[],
  taskId: string,
  patch: Partial<WorkerState>,
): WorkerState[] {
  const index = workers.findIndex((w) => w.taskId === taskId);
  if (index === -1) {
    // A worker frame for a subtask the decomposition never announced. It should not happen, and
    // appending is still better than dropping it: a card in the wrong order beats a worker that
    // silently did not exist, because the second one is invisible in the display AND in the count.
    return [
      ...workers,
      { ...blankWorker(taskId), ...patch },
    ];
  }
  const next = workers.slice();
  next[index] = { ...next[index], ...patch };
  return next;
}

function blankWorker(taskId: string, objective = ""): WorkerState {
  return {
    taskId,
    objective,
    status: "queued",
    tier: "",
    stage: "",
    detail: "",
    reason: "",
    reasked: false,
    tokens: 0,
    checksRun: [],
    summaryChars: 0,
    gaps: [],
    evidenceRefs: [],
  };
}

/**
 * Fold one frame into the state. Frames already applied are ignored, so live and replayed frames
 * can be mixed freely and in any combination.
 */
export function applyFrame(state: OrchestrationState, frame: OrchFrame): OrchestrationState {
  if (frame.seq !== 0 && frame.seq <= state.seq) return state;
  const seq = frame.seq || state.seq;
  const data = frame.data ?? {};

  switch (frame.kind) {
    case "run":
      return { ...state, seq, runId: str(data.run_id) || state.runId };

    case "classified":
      return {
        ...state,
        seq,
        stage: "classified",
        shape: str(data.shape),
        sources: num(data.sources),
      };

    case "decomposed": {
      const specs = Array.isArray(data.specs) ? data.specs : [];
      return {
        ...state,
        seq,
        stage: "decomposed",
        // Created up front, all queued, in the order the decomposition named them. Without this
        // the cards would pop into existence as each worker starts, which shows the fan-out
        // finishing rather than the fan-out running.
        workers: specs.map((raw) => {
          const spec = raw as Record<string, unknown>;
          return blankWorker(str(spec.task_id), str(spec.objective));
        }),
      };
    }

    case "worker_started": {
      // Only the fields this frame actually carries. Spreading `objective: undefined` would
      // ERASE the objective `decomposed` already named, leaving cards labelled by their task id
      // — which is what a test caught. An absent key and a key set to undefined are not the same
      // thing to Object.assign, and this is the shape of bug that difference produces.
      const patch: Partial<WorkerState> = { status: "running", tier: str(data.tier) };
      const objective = str(data.objective);
      if (objective) patch.objective = objective;
      return { ...state, seq, stage: "working", workers: patchWorker(state.workers, frame.task_id, patch) };
    }

    case "worker_verified":
      return {
        ...state,
        seq,
        workers: patchWorker(state.workers, frame.task_id, {
          status: "verified",
          stage: str(data.stage),
          checksRun: strings(data.checks_run),
          reasked: data.reasked === true,
          tokens: num(data.tokens),
          summaryChars: num(data.summary_chars),
          gaps: strings(data.gaps),
          evidenceRefs: strings(data.evidence_refs),
        }),
      };

    case "worker_rejected":
      return {
        ...state,
        seq,
        workers: patchWorker(state.workers, frame.task_id, {
          status: "rejected",
          stage: str(data.stage),
          detail: str(data.detail),
          reason: str(data.reason) as RejectReason,
          tokens: num(data.tokens),
        }),
      };

    case "synthesizing":
      return { ...state, seq, stage: "synthesizing", fused: data.fused === true };

    case "fell_back":
      return {
        ...state,
        seq,
        // The cards go away, because there are none: the run is one agent. Leaving an empty grid
        // on screen would suggest workers that failed rather than workers that were never right
        // for this task.
        workers: [],
        fellBack: { reason: str(data.reason), shape: str(data.shape) },
      };

    case "done":
      return {
        ...state,
        seq,
        stage: "done",
        answer: str(data.answer),
        cancelled: data.cancelled === true,
        totals: {
          tokens: typeof data.total_tokens === "number" ? data.total_tokens : null,
          counterfactual:
            typeof data.counterfactual_tokens === "number" ? data.counterfactual_tokens : null,
        },
      };

    case "error":
      return { ...state, seq, stage: "error", error: str(data.message) || "the run failed" };

    default:
      // An unknown frame advances the sequence and changes nothing else. A newer backend adding a
      // frame kind must not strand an older client at a `seq` it will never pass.
      return { ...state, seq };
  }
}

/** Whether a Stop control should be live: there is a run, and it has not reached a terminal frame. */
export function isRunning(state: OrchestrationState): boolean {
  return (
    state.runId !== null && state.stage !== "done" && state.stage !== "error" && state.stage !== "idle"
  );
}

export function countByStatus(state: OrchestrationState, status: WorkerStatus): number {
  return state.workers.filter((w) => w.status === status).length;
}

// --- The crew ---------------------------------------------------------------------------------
//
// A second reducer rather than a branch inside the first one. The two runs answer different
// questions: a hierarchy asks "what do these sources say", and its output is one synthesised
// answer; a crew asks "which of these attempts survives a check", and its output is a set of
// files that landed and a set that did not. Folding them together would mean a state shape where
// half the fields are always null, and a card that has to ask which kind of run it belongs to.

export type CrewWorkerStatus = "queued" | "running" | "verified" | "rejected" | "failed";

export interface CrewWorkerState {
  name: string;
  status: CrewWorkerStatus;
  /** The checkout this worker writes in. Empty until it starts. */
  workspace: string;
  instruction: string;
  /** The check that passed, or the one that refused it. Empty when there was none, and empty when
   *  there WAS one that reached no verdict — see `abstained`. */
  verify: string;
  /** The check ran and decided nothing: pytest collected no tests (exit 5), or the binary is not
   *  installed (exit 127). This worker merged, as it would with no check configured, and nothing
   *  may say a check approved it. Kept apart from `verify` because "approved by pytest" and
   *  "pytest is not installed here" send a reader to two different places. */
  abstained: boolean;
  /** What the failing check printed — the only thing that says WHY this was discarded. */
  detail: string;
  reason: string;
  answerChars: number;
  /** The files it wrote that actually reached the workspace. */
  files: string[];
  /** The files it wrote that did NOT — refused by the check, or contested by another worker.
   *  Kept apart from `files` because passing a check and landing are different things: two
   *  workers who both pass on one file both lose it. */
  lost: string[];
  /** The worker's own report of what it did. Arrives at the end, not while it runs. */
  answer: string;
  /** Whether those files were merged. False and non-empty means the work existed and was lost. */
  landed: boolean;
}

export interface CrewState {
  runId: string | null;
  seq: number;
  stage: "idle" | "working" | "synthesizing" | "done" | "error";
  workers: CrewWorkerState[];
  merged: number;
  /** Files two workers both changed. NEITHER version landed. */
  conflicts: string[];
  answer: string | null;
  /** False = no git repository, so the workers shared one folder and nothing was isolated. */
  isRepo: boolean | null;
  error: string | null;
}

export const EMPTY_CREW: CrewState = {
  runId: null,
  seq: 0,
  stage: "idle",
  workers: [],
  merged: 0,
  conflicts: [],
  answer: null,
  isRepo: null,
  error: null,
};

function patchCrewWorker(
  workers: CrewWorkerState[],
  name: string,
  patch: Partial<CrewWorkerState>,
): CrewWorkerState[] {
  const index = workers.findIndex((w) => w.name === name);
  const blank: CrewWorkerState = {
    name,
    status: "queued",
    workspace: "",
    instruction: "",
    verify: "",
    abstained: false,
    detail: "",
    reason: "",
    answerChars: 0,
    files: [],
    lost: [],
    answer: "",
    landed: false,
  };
  if (index === -1) return [...workers, { ...blank, ...patch }];
  const next = workers.slice();
  next[index] = { ...next[index], ...patch };
  return next;
}

/** Fold one crew frame in. Same idempotence rule as `applyFrame`: a frame already applied is
 *  ignored, so replay after a reload goes through this exact function. */
export function applyCrewFrame(state: CrewState, frame: OrchFrame): CrewState {
  if (frame.seq !== 0 && frame.seq <= state.seq) return state;
  const seq = frame.seq || state.seq;
  const data = frame.data ?? {};

  switch (frame.kind) {
    case "run":
      return { ...state, seq, runId: str(data.run_id) || state.runId, stage: "working" };

    case "crew_worker_started":
      return {
        ...state,
        seq,
        stage: "working",
        workers: patchCrewWorker(state.workers, frame.task_id, {
          status: "running",
          workspace: str(data.workspace),
          instruction: str(data.instruction),
        }),
      };

    case "crew_worker_verified":
      return {
        ...state,
        seq,
        workers: patchCrewWorker(state.workers, frame.task_id, {
          // "verified" is the merge decision. `abstained` is whether anything actually decided it:
          // pytest that collected nothing, or a binary that is not installed, comes back
          // `passed=True` so the work is not punished for our inability to check it — and reading
          // only that turned a command which DOES NOT EXIST into an approval.
          status: "verified",
          verify: str(data.verified_by),
          abstained: data.abstained === true,
          detail: str(data.detail),
          answerChars: num(data.answer_chars),
        }),
      };

    case "crew_worker_rejected":
      return {
        ...state,
        seq,
        workers: patchCrewWorker(state.workers, frame.task_id, {
          status: "rejected",
          reason: str(data.reason),
          verify: frame.text,
          detail: str(data.detail),
        }),
      };

    case "crew_worker_produced":
      // Arrives after the worker finished, for the discarded ones too. Without it, a run where
      // every worker was rejected reports that three attempts happened and nothing about what
      // they were — the worktrees are gone by the time anyone could go and look.
      return {
        ...state,
        seq,
        workers: patchCrewWorker(state.workers, frame.task_id, {
          files: strings(data.files),
          lost: strings(data.lost),
          answer: str(data.answer),
          landed: data.landed === true,
        }),
      };

    case "crew_worker_failed":
      return {
        ...state,
        seq,
        workers: patchCrewWorker(state.workers, frame.task_id, {
          status: "failed",
          detail: frame.text,
        }),
      };

    case "conflict":
      // Accumulated, not replaced: one frame per contested file.
      return { ...state, seq, conflicts: [...state.conflicts, str(data.path)] };

    case "synthesizing":
      return { ...state, seq, stage: "synthesizing" };

    case "crew_done":
      return {
        ...state,
        seq,
        stage: "done",
        merged: num(data.merged),
        conflicts: strings(data.conflicts).length ? strings(data.conflicts) : state.conflicts,
        answer: str(data.answer) || null,
        isRepo: data.is_repo === true,
      };

    case "error":
      return { ...state, seq, stage: "error", error: str(data.message) || "the crew failed" };

    default:
      return { ...state, seq };
  }
}

export function isCrewRunning(state: CrewState): boolean {
  return state.runId !== null && state.stage !== "done" && state.stage !== "error";
}
