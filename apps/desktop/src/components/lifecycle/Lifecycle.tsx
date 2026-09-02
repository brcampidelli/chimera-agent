import { useEffect, useState } from "react";
import { Check, Loader2, Play, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  cancelLifecycle,
  streamLifecycle,
  type LifecycleDone,
  type LifecycleStage,
  type RunVerify,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The four stages, in order, named before any of them has run.
 *
 *  Declared rather than accumulated from the stream, so the screen can show what is COMING as well
 *  as what happened. A list that grows one row at a time tells you where you are and never how far
 *  there is to go — and the build stage alone can take minutes.
 */
export const STAGES = ["plan", "build", "test", "review"] as const;
export type StageName = (typeof STAGES)[number];

type State = "idle" | "running" | "done";

export function stageState(
  name: StageName,
  seen: LifecycleStage[],
  running: boolean,
): "waiting" | "active" | "passed" | "failed" {
  const landed = seen.find((s) => s.name === name);
  if (landed) return landed.passed ? "passed" : "failed";
  if (!running) return "waiting";
  // The first stage with nothing reported yet is the one being worked. The crew is strictly
  // sequential, so this is a fact about the pipeline rather than a guess about the clock.
  return STAGES.slice(0, STAGES.indexOf(name)).every((s) => seen.some((x) => x.name === s))
    ? "active"
    : "waiting";
}

/** plan → build → test → review, one frame at a time.
 *
 *  `LifecycleCrew` has been working and tested for a long time, and until now only
 *  `chimera lifecycle` in a terminal could reach it — the app's own most structured way of doing a
 *  piece of work was unreachable from the app. What it adds over an ordinary run is that the test
 *  gate is a step you can watch fail and the reviewer's opinion is separate from the verdict, so
 *  the stages arrive as they land rather than as one block at the end.
 */
export function Lifecycle({
  task,
  verify,
  workspace,
  onBusy,
}: {
  /** The shared task and check, from the console above. This screen used to ask for both itself,
   *  which made it the third place on one screen asking for the same sentence — and the reason
   *  trying the same task a second way meant typing it a second time. */
  task: string;
  verify: string;
  workspace: string | null;
  onBusy?: (busy: boolean) => void;
}) {
  const t = useT();
  const [state, setState] = useState<State>("idle");
  const [stages, setStages] = useState<LifecycleStage[]>([]);
  const [gate, setGate] = useState<RunVerify | null>(null);
  const [done, setDone] = useState<LifecycleDone | null>(null);
  const [error, setError] = useState("");
  // State and not a ref. The route emits `run` FIRST precisely so Stop can target the run from
  // the first moment; a ref does not re-render, so the button stayed disabled until some other
  // frame happened to arrive — which on a build stage is minutes away.
  const [runId, setRunId] = useState("");

  async function start() {
    setState("running");
    setStages([]);
    setDone(null);
    setError("");
    setGate(null);
    setRunId("");
    await streamLifecycle(
      {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace,
      },
      {
        onRunId: setRunId,
        onVerify: setGate,
        onStage: (s) => setStages((prev) => [...prev, s]),
        onDone: setDone,
        onError: setError,
      },
    );
    setState("done");
  }

  const running = state === "running";

  useEffect(() => {
    onBusy?.(running);
  }, [running, onBusy]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {running ? (
            <Button
              size="sm"
              variant="outline"
              type="button"
              onClick={() => void cancelLifecycle(runId)}
              disabled={!runId}
            >
              <Square className="h-3.5 w-3.5" /> {t("lifecycle.stop")}
            </Button>
          ) : (
            <Button size="sm" type="button" disabled={!task.trim()} onClick={() => void start()}>
              <Play className="h-3.5 w-3.5" /> {t("lifecycle.start")}
            </Button>
          )}
        </div>
        {running ? (
          <p className="text-xs text-muted-foreground">{t("lifecycle.stopsBetweenStages")}</p>
        ) : null}
      </div>

      {/* What is judging this build, said before it starts. "No verify command — a model reads the
          answer" has always been true whenever the field was empty, and a screen that does not say
          so lets an approving paragraph pass for a passing test. */}
      {gate ? (
        <p className={cn("text-xs", gate.command ? "text-muted-foreground" : "text-warn-foreground")}>
          {gate.command
            ? t("runs.judgedBy", { cmd: gate.command, src: gate.source })
            : t("runs.judgedByModel")}
        </p>
      ) : null}

      {state !== "idle" ? (
        <ol className="flex flex-col gap-1.5">
          {STAGES.map((name) => {
            const status = stageState(name, stages, running);
            const landed = stages.find((s) => s.name === name);
            return (
              <li key={name}>
                <div
                  className={cn(
                    "flex items-start gap-2 rounded-chip border p-2",
                    status === "passed" && "border-ok",
                    status === "failed" && "border-bad",
                    status === "active" && "border-accent",
                    status === "waiting" && "border-border opacity-60",
                  )}
                >
                  <span className="mt-0.5" aria-hidden>
                    {status === "passed" ? (
                      <Check className="h-3.5 w-3.5 text-ok-foreground" />
                    ) : status === "failed" ? (
                      <X className="h-3.5 w-3.5 text-bad-foreground" />
                    ) : status === "active" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-accent-foreground" />
                    ) : (
                      <span className="block h-3.5 w-3.5 rounded-chip bg-muted" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium">
                      {t(`lifecycle.stage.${name}`)}{" "}
                      <span className="font-normal text-muted-foreground">
                        {t(`lifecycle.status.${status}`)}
                      </span>
                    </p>
                    {landed?.output ? (
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">
                        {landed.output}
                      </pre>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}

      {error ? (
        <p className="text-xs text-bad-foreground" role="alert">
          {error}
        </p>
      ) : null}

      {/* A stop is not a failure. Saying "did not pass" about a run somebody halted would report
          working code as broken — nothing ever tested it. */}
      {done ? (
        <p
          className={cn(
            "text-xs",
            done.cancelled ? "text-muted-foreground" : done.success ? "text-ok-foreground" : "text-bad-foreground",
          )}
          role="status"
        >
          {done.cancelled
            ? t("lifecycle.stopped")
            : done.success
              ? t("lifecycle.passed")
              : t("lifecycle.failed")}
        </p>
      ) : null}
    </div>
  );
}
