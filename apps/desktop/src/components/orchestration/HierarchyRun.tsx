import { useEffect, useReducer } from "react";
import Markdown from "react-markdown";

import { streamHierarchy, type HierarchyRunInput, type OrchFrame } from "@/lib/api";
import { useNum, useT, type TFunc } from "@/lib/i18n";
import {
  applyFrame,
  EMPTY_RUN,
  isRunning,
  type OrchestrationState,
} from "@/lib/orchestration-run";

import { FellBackNote } from "./FellBackNote";
import { rememberRun } from "./resume";
import { StopButton } from "./StopButton";
import { useStop } from "./use-stop";
import { RunStepper } from "./RunStepper";
import { WorkerCard } from "./WorkerCard";

/**
 * One hierarchy run, from the first frame to the answer.
 *
 * The parent gives this component a `key` per confirmed run, so a new run is a new component
 * rather than a re-run of an old one — the same device `Code.tsx` uses for the batch board, and
 * the reason the effect below can be a genuine mount-once.
 */
export function HierarchyRun({
  request,
  resume,
  onOpenCode,
}: {
  /** Start a run. Absent when `resume` is given — a transcript is read, never re-run. */
  request?: HierarchyRunInput;
  /** A past run's frames, replayed. The same reducer the live stream feeds, so a resumed run and a
   *  live one differ in exactly one thing: whether more is coming. */
  resume?: OrchFrame[];
  onOpenCode: () => void;
}) {
  const t = useT();
  const [state, dispatch] = useReducer(applyFrame, EMPTY_RUN);

  // Remember which run this is, so a reload can ask the server for what it missed. Written from the
  // reducer rather than from a second copy: the id arrives as the `run` frame and there is one
  // source of truth for it.
  useEffect(() => {
    if (state.runId) rememberRun(state.runId);
  }, [state.runId]);

  useEffect(() => {
    if (!resume) return;
    // Replayed in one pass. No stream is opened: this run finished, or died with the process that
    // was running it, and either way nothing more is coming — re-running it would spend the money
    // again to reproduce an answer that is already on disk.
    for (const frame of resume) dispatch(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    void streamHierarchy(
      request,
      {
        // No `onRunId` handler: the id also arrives as the `run` frame, and reading it from the
        // reducer keeps one source of truth. Two copies of the same value is how the Stop button
        // ended up enabled off one of them while `isRunning` was deciding off the other.
        onFrame: dispatch,
        onError: (message) => {
          // An abort is this component's own cleanup, not a failure: leaving the screen tears
          // down the fetch, and "signal is aborted without reason" was reaching the user as
          // though the run had broken.
          if (/abort/i.test(message)) return;
          dispatch({ seq: 0, kind: "error", task_id: "", text: "", data: { message } });
        },
      },
      controller.signal,
    );
    // Aborting the fetch closes OUR end. The server keeps working, deliberately: a half-finished
    // fan-out is not something to leave behind, and stopping is a separate, explicit request.
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A replayed run is never running, whatever its frames say. A transcript that stops mid-fan-out
  // is a process that died, and showing a spinner and a Stop button over it would offer to halt
  // something that ended before this window opened.
  const running = !resume && isRunning(state);
  const stop = useStop(state.runId ?? "", running);
  const interrupted = Boolean(resume) && isRunning(state);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <RunStepper state={state} />
        {running ? (
          <StopButton stop={stop} disabled={!state.runId} hint={t("orch.stopHint")} />
        ) : null}
      </div>

      {/* Said before the cards, not under them: someone reading a half-finished fan-out needs to
          know it is over before they start waiting for the rest of it. */}
      {interrupted ? (
        <p className="rounded-card border border-warn/25 bg-warn/5 p-3 text-sm text-warn-foreground">
          {t("orch.interrupted")}
        </p>
      ) : null}

      {state.fellBack ? (
        <FellBackNote
          shape={state.fellBack.shape}
          reason={state.fellBack.reason}
          onOpenCode={onOpenCode}
        />
      ) : null}

      {state.workers.length > 0 ? (
        <div className="space-y-2">
          <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
            {state.workers.map((worker) => (
              <WorkerCard key={worker.taskId} worker={worker} />
            ))}
          </div>
          {/* Said once, plainly. The workers run through a non-streaming backend, so there is no
              token to show; a blinking cursor here would be an animation standing in for progress
              that does not exist. */}
          <p className="text-xs text-muted-foreground">{t("orch.stateOnly")}</p>
        </div>
      ) : null}

      {state.error ? (
        <p className="rounded-card border border-bad/25 bg-bad/5 p-3 text-sm text-bad-foreground">
          {state.error}
        </p>
      ) : null}

      {/* A stopped run has a bill. The server sends `total_tokens` on cancel, the reducer stores
          it, and it used to live only inside the `state.answer` branch — which is empty on
          cancel, so the number arrived and was never rendered. The sentence above it said
          "nothing was spent", while the Stop button's own tooltip two lines up said the
          opposite and said it correctly. */}
      {state.cancelled ? (
        <section className="surface p-4">
          <p className="text-sm text-muted-foreground">{t("orch.cancelled")}</p>
          <Totals totals={state.totals} t={t} />
        </section>
      ) : null}

      {state.answer ? (
        <section className="surface p-4">
          <div className="prose-chimera text-sm">
            <Markdown>{state.answer}</Markdown>
          </div>
          <Totals totals={state.totals} t={t} />
        </section>
      ) : null}
    </div>
  );
}

/**
 * What the run cost.
 *
 * It used to print the two numbers side by side, joined by a dot and nothing else:
 *
 *     8721 tokens · um agente só teria custado cerca de 8000
 *
 * A 721-token LOSS, in the grammar of a saving. And the second number is not a measurement — no
 * second run happened. It is the profitability gate's own arithmetic: a fixed 24000-character
 * context divided by the subtask count, which comes out to roughly 6000 + 1000 per subtask, always,
 * for every task of that width. `receipts.py` says so in its own comment, and says the saving must
 * not be quoted until the sweep has a tool-enabled arm to measure it against.
 *
 * So this names each number for what it is and says which side the run landed on. What it will not
 * do is call the difference a saving.
 */
function Totals({ totals, t }: { totals: OrchestrationState["totals"]; t: TFunc }) {
  const num = useNum();
  if (!totals) return null;
  const spent = totals.tokens ?? 0;
  const guess = totals.counterfactual ?? 0;
  return (
    <p className="mt-3 border-t border-hairline pt-3 text-xs tabular-nums text-muted-foreground">
      {t("orch.tokens", { n: num(spent) })}
      {guess ? (
        <>
          {" · "}
          <span title={t("orch.estimate.what")}>
            {t(spent <= guess ? "orch.estimate.under" : "orch.estimate.over", { n: num(guess) })}
          </span>
        </>
      ) : null}
    </p>
  );
}
