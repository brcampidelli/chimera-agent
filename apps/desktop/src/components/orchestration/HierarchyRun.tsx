import { useEffect, useReducer } from "react";
import Markdown from "react-markdown";

import { streamHierarchy, type HierarchyRunInput } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import {
  applyFrame,
  EMPTY_RUN,
  isRunning,
  type OrchestrationState,
} from "@/lib/orchestration-run";

import { FellBackNote } from "./FellBackNote";
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
  onOpenCode,
}: {
  request: HierarchyRunInput;
  onOpenCode: () => void;
}) {
  const t = useT();
  const [state, dispatch] = useReducer(applyFrame, EMPTY_RUN);

  useEffect(() => {
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

  const running = isRunning(state);
  const stop = useStop(state.runId ?? "", running);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <RunStepper state={state} />
        {running ? (
          <StopButton stop={stop} disabled={!state.runId} hint={t("orch.stopHint")} />
        ) : null}
      </div>

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
        <p className="rounded-card border border-bad/25 bg-bad/5 p-3 text-sm text-bad">
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

/** What the run cost, under the answer or under the notice that there will not be one. */
function Totals({ totals, t }: { totals: OrchestrationState["totals"]; t: TFunc }) {
  if (!totals) return null;
  return (
    <p className="mt-3 border-t border-hairline pt-3 text-xs tabular-nums text-muted-foreground">
      {t("orch.tokens", { n: totals.tokens ?? 0 })}
      {/* Only when there IS one. A counterfactual is an ESTIMATE of what one inline agent would
          have spent, and the word matters: printing a saving without it would read as a
          measurement of two runs that never both happened. */}
      {totals.counterfactual
        ? ` · ${t("orch.counterfactual", { n: totals.counterfactual })}`
        : ""}
    </p>
  );
}
