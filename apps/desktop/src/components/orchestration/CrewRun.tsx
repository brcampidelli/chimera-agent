import { useEffect, useReducer } from "react";
import { AlertTriangle, Check, FolderGit2, Loader2, X } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { streamCrew, type CrewRunInput, type OrchFrame } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { applyCrewFrame, EMPTY_CREW, isCrewRunning, type CrewWorkerState } from "@/lib/orchestration-run";
import { cn } from "@/lib/utils";

import { StopButton } from "./StopButton";
import { useStop } from "./use-stop";

const rails: Record<CrewWorkerState["status"], string> = {
  queued: "bg-muted",
  running: "bg-accent",
  verified: "bg-ok",
  rejected: "bg-bad",
  failed: "bg-bad",
};

/**
 * One crew, watched.
 *
 * The card carries the two things this run has that a hierarchy worker does not: the checkout it
 * writes in, and the output of the check that judged it. Both were invisible before — the
 * worktrees are created, used and removed without ever being named, and a rejected worker was a
 * count in a tally.
 */
function CrewWorkerCard({ worker }: { worker: CrewWorkerState }) {
  const t = useT();
  // A worker built from the catalogue is named by its approach id, which is a routing key and not
  // a thing to read ("no_new_deps"). Translate it when there is a translation and show the raw
  // name when there is not, which is exactly the custom-worker case — no list of known ids to
  // keep in step with the server's.
  const key = `crew.approach.${worker.name}`;
  const label = t(key) === key ? worker.name : t(key);
  const icon =
    worker.status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
    ) : worker.status === "verified" ? (
      <Check className="h-3.5 w-3.5 text-ok" />
    ) : worker.status === "queued" ? (
      <span className="h-3.5 w-3.5" />
    ) : (
      <X className="h-3.5 w-3.5 text-bad" />
    );

  return (
    <article className="surface relative overflow-hidden pl-4">
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-0.5", rails[worker.status])} />
      <div className="space-y-2 p-3">
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-sm font-medium text-foreground">{label}</span>
          <Badge
            tone={
              worker.status === "verified"
                ? worker.abstained ? "warn" : "ok"
                : worker.status === "running" ? "accent"
                  : worker.status === "queued" ? "muted" : "bad"
            }
          >
            {t(`crew.status.${worker.status}`)}
          </Badge>
        </div>

        {worker.instruction ? (
          <p className="text-xs text-muted-foreground">{worker.instruction}</p>
        ) : null}

        {/* Which checkout produced this. Named, so a diff can be gone and looked at afterwards. */}
        {worker.workspace ? (
          <p className="flex items-start gap-1.5 font-mono text-xs text-muted-foreground">
            <FolderGit2 className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="min-w-0 break-all">{worker.workspace}</span>
          </p>
        ) : null}

        {/* A merge with nothing behind it. The crew is justified entirely by "N attempts, the
            test picks the winner", so a run where the test never executed has to say so — the card
            used to read "verified by pytest -q" for a pytest that is not installed. */}
        {worker.status === "verified" && worker.abstained ? (
          <div className="space-y-1">
            <p className="text-xs text-warn-foreground">{t("crew.verified.abstained")}</p>
            {worker.detail ? (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  {t("crew.rejected.output")}
                </summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 font-mono text-muted-foreground">
                  {worker.detail}
                </pre>
              </details>
            ) : null}
          </div>
        ) : null}

        {worker.status === "rejected" ? (
          <div className="space-y-1">
            <p className="text-xs text-bad-foreground">
              {worker.reason === "cancelled"
                ? t("crew.rejected.cancelled")
                : /could not run|not found|no such file/i.test(worker.detail)
                  ? // The check never executed. Saying "your check failed" here sends someone
                    // looking for a bug in code that was never read — the fault is in the
                    // command or the folder, and those are different places to look.
                    t("crew.rejected.couldNotRun", { command: worker.verify })
                  : t("crew.rejected.verify", { command: worker.verify })}
            </p>
            {/* The output, not just the fact. A crew whose workers all fail the same check is a
                crew whose check is wrong — and that is only visible if the output is. */}
            {worker.detail ? (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  {t("crew.rejected.output")}
                </summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 font-mono text-muted-foreground">
                  {worker.detail}
                </pre>
              </details>
            ) : null}
            <p className="text-xs text-muted-foreground">{t("crew.rejected.discarded")}</p>
          </div>
        ) : null}

        {worker.status === "failed" && worker.detail ? (
          <p className="text-xs text-bad-foreground">{worker.detail}</p>
        ) : null}

        {/* Two lists, not one with a heading that swings. A worker can pass the check and still
            lose a file another worker also touched, so "what it wrote" splits into what reached
            your project and what did not — and the second list is the point: the worktree is
            removed when the run ends, so this is the only surviving account of a thrown-away
            attempt. A single flag here once put "and that landed" directly above a panel saying
            nothing landed. */}
        {worker.files.length > 0 ? (
          <div className="space-y-1">
            <p className="text-xs text-ok-foreground">{t("crew.produced.landed")}</p>
            <ul className="space-y-0.5">
              {worker.files.map((file) => (
                <li key={file} className="font-mono text-xs text-muted-foreground">
                  {file}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {worker.lost.length > 0 ? (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">{t("crew.produced.lost")}</p>
            <ul className="space-y-0.5">
              {worker.lost.map((file) => (
                <li key={file} className="font-mono text-xs text-muted-foreground line-through">
                  {file}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* Folded, and second: the files are the evidence, this is the worker's own account of
            them — useful, and not the same kind of thing. */}
        {worker.answer ? (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              {t("crew.produced.report")}
            </summary>
            <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{worker.answer}</p>
          </details>
        ) : null}
      </div>
    </article>
  );
}

export function CrewRun({
  request,
  resume,
  onBusy,
}: {
  /** Start a run. Absent when `resume` is given — a transcript is read, never re-run. */
  request?: CrewRunInput;
  /** A past run's frames, replayed through the same reducer the live stream feeds.
   *
   *  Crew runs were being written to disk and could never be read back: `HierarchyRun` had this
   *  and this one did not, so half of what the run list offers was unopenable. A crew is the more
   *  expensive of the two — N workers, each with its own worktree — which makes it the worse half
   *  to lose. */
  resume?: OrchFrame[];
  /** Told while this crew is in flight, so the console above can refuse to change mode under it.
   *  Derived from the frames: a crew that finished, failed or was stopped releases the lock, and
   *  only the stream knows which of those happened. */
  onBusy?: (busy: boolean) => void;
}) {
  const t = useT();
  const [state, dispatch] = useReducer(applyCrewFrame, EMPTY_CREW);

  useEffect(() => {
    if (!resume) return;
    // One pass, no stream. This run ended, or died with the process that was running it; either
    // way nothing more is coming, and re-running it would spend the money again to reproduce an
    // answer that is already on disk.
    for (const frame of resume) dispatch(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    void streamCrew(
      request,
      {
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
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = isCrewRunning(state);
  const stop = useStop(state.runId ?? "", running);

  useEffect(() => {
    onBusy?.(running);
  }, [running, onBusy]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="status" aria-live="polite" className="text-xs text-muted-foreground">
          {state.stage === "done"
            ? t("crew.done", { merged: state.merged })
            : state.stage === "synthesizing"
              ? t("orch.stage.synthesizing")
              : t("crew.working", { n: state.workers.length })}
        </div>
        {running && state.runId ? (
          <StopButton stop={stop} hint={t("crew.stopHint")} />
        ) : null}
      </div>

      {/* Said before the results, because it changes what every line below MEANS. Outside a git
          repository there are no worktrees: the workers shared one folder, edits landed on top of
          each other, and a file two of them touched cannot even be reported as a conflict. */}
      {state.isRepo === false ? (
        <p className="flex items-start gap-1.5 rounded-card border border-warn/25 bg-warn/5 p-3 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("crew.notARepo")}
        </p>
      ) : null}

      {state.workers.length > 0 ? (
        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {state.workers.map((worker) => (
            <CrewWorkerCard key={worker.name} worker={worker} />
          ))}
        </div>
      ) : null}

      {state.conflicts.length > 0 ? (
        <div className="rounded-card border border-warn/25 bg-warn/5 p-3">
          <p className="text-xs font-semibold text-warn-foreground">
            {t("crew.conflicts.title", { n: state.conflicts.length })}
          </p>
          {/* The wording matters: a conflict is not "one of them won". Neither version landed,
              and the file is exactly as it was. */}
          <p className="mt-1 text-xs text-muted-foreground">{t("crew.conflicts.explain")}</p>
          <ul className="mt-2 space-y-0.5">
            {state.conflicts.map((path) => (
              <li key={path} className="font-mono text-xs text-warn-foreground">
                {path}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {state.error ? (
        <p className="rounded-card border border-bad/25 bg-bad/5 p-3 text-sm text-bad-foreground">
          {state.error}
        </p>
      ) : null}

      {state.stage === "done" && state.merged === 0 && state.conflicts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("crew.nothingLanded")}</p>
      ) : null}

      {state.answer ? (
        <section className="surface whitespace-pre-wrap p-4 text-sm">{state.answer}</section>
      ) : null}
    </div>
  );
}
