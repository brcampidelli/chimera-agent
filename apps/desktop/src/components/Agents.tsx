import { useMemo } from "react";
import { AlertTriangle, CheckCircle2, GitMerge, Loader2, Square, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { batchCounts, useOptionalAgentBatch, type BatchCounts, type BatchLiveEvent } from "@/lib/agent-batch";
import { useT, type TFunc } from "@/lib/i18n";
import type { AgentResult, FileDiff } from "@/lib/types";

import { EndingBadge, TaintedBadge, TaskAnswer } from "@/components/code/BatchRecord";
import { DiffView } from "@/components/code/DiffView";

/** Map one tagged live event to a compact localized line (backend `text` is English; the attempt
 *  number is already inside `text`, so we don't read the numeric index here). */
function liveLine(e: BatchLiveEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("agents.planning") : e.text;
  if (e.kind === "attempt") return e.text; // e.g. "attempt 2/3"
  if (e.kind === "result") return e.text; // e.g. "attempt 2 passed/failed"
  if (e.kind === "edit" && e.path) return `${t("agents.edited")} ${e.path}`;
  return null;
}

/** One task card on the board: its live status while running, then its real final result. */
function AgentCard({
  index,
  label,
  lines,
  result,
  conflicts,
  running,
  canStop,
  stopping,
  onStop,
  t,
}: {
  index: number;
  label: string;
  lines: string[];
  result: AgentResult | null;
  conflicts: string[];
  running: boolean;
  // Whether this card can offer a Stop: the batch is in flight AND has reported its id (the cancel
  // handle). A task with a result has already finished — it never gets one.
  canStop: boolean;
  stopping: boolean;
  onStop: () => void;
  t: TFunc;
}) {
  // Which of this task's changed files collided with another task (left unmerged).
  const conflictSet = useMemo(() => new Set(conflicts), [conflicts]);
  const done = result !== null;
  return (
    <div className="flex flex-col rounded-chip border border-border bg-surface-2">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2">
        <span className="font-mono text-xs text-muted-foreground">#{index + 1}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={label}>
          {label || t("agents.untitled")}
        </span>
        {/* Cooperative per-task Stop, offered only while the batch is in flight: it halts THIS task
            before its next attempt (never mid model-call), leaving the batch's others running. */}
        {!done && running ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 px-1.5 text-xs"
            disabled={!canStop || stopping}
            onClick={onStop}
            title={t("agents.stopTooltip")}
          >
            <Square className="h-3 w-3" />
            {stopping ? t("agents.stopping") : t("agents.stop")}
          </Button>
        ) : null}
        {!done && running ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
        ) : done && result.success ? (
          <Badge tone="ok">
            <CheckCircle2 className="h-3 w-3" /> {t("agents.passed")}
          </Badge>
        ) : done ? (
          <Badge tone="bad">
            <XCircle className="h-3 w-3" /> {t("agents.failed")}
          </Badge>
        ) : (
          <Badge tone="muted">{t("agents.queued")}</Badge>
        )}
      </div>

      <div className="min-w-0 space-y-2 p-3">
        {/* Live status: the latest few tagged frames for this task. */}
        {!done && lines.length > 0 ? (
          <div className="space-y-0.5 rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground">
            {lines.slice(-4).map((line, i) => (
              <div key={i} className="truncate">
                {line}
              </div>
            ))}
          </div>
        ) : null}

        {done ? (
          <>
            {/* Why this card has nothing on it. A task the batch stopped waiting for and a task
                that ran and did not pass both arrive as `success: false` with zero attempts and
                no files — identical on screen, and the first of the two is not the user's code
                being wrong. Printed verbatim from the server (`timed out after Ns`, or the
                exception that killed the unit); this component never invents the reason. */}
            {result.error ? (
              <p className="wrap-break-word font-mono text-xs text-bad-foreground">{result.error}</p>
            ) : null}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {/* How the task's loop ended, when it says more than the pass/fail badge above it does:
                  `no_op` is an answer with no edit (done, not "nothing"), `exhausted` ran out of tries. */}
              {result.ending === "success" ? null : <EndingBadge ending={result.ending} />}
              <TaintedBadge tainted={result.tainted && result.answer.trim() !== ""} />
              <span>
                {t("agents.attempts")}: {result.attempts}
              </span>
              {result.reverted ? <Badge tone="warn">↩ {t("agents.reverted")}</Badge> : null}
              <span>
                {t("agents.changedFiles")}: {result.changed_paths.length}
              </span>
            </div>

            {/* The task's own final answer. For an analysis task it is the whole result; it used to
                be dropped by the server, and the card said only that nothing changed on disk. */}
            <TaskAnswer answer={result.answer} />

            {result.changed_paths.length > 0 ? (
              <ul className="space-y-0.5">
                {result.changed_paths.map((p) => (
                  <li
                    key={p}
                    className="flex items-center gap-1.5 font-mono text-xs text-foreground/80"
                  >
                    <span className="truncate">{p}</span>
                    {conflictSet.has(p) ? <Badge tone="bad">{t("agents.conflict")}</Badge> : null}
                  </li>
                ))}
              </ul>
            ) : result.answer.trim() ? null : (
              // Said only when there is no answer either: beside an answer, "changed nothing" reads
              // as a failure of a task whose work was the answer.
              <p className="text-xs text-muted-foreground">{t("agents.noChanges")}</p>
            )}

            {result.diffs.length > 0 ? (
              <details className="rounded-chip bg-surface-2">
                <summary className="cursor-pointer px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground">
                  {t("agents.showDiff")} ({result.diffs.length})
                </summary>
                <div className="space-y-2 px-2 pb-2">
                  {result.diffs.map((diff: FileDiff) => (
                    <details key={diff.path} className="rounded-chip bg-surface-2" open>
                      <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-xs text-foreground/80 hover:text-foreground">
                        {diff.path}
                        {diff.truncated ? <Badge tone="muted">{t("agents.truncated")}</Badge> : null}
                      </summary>
                      <div className="px-2 pb-2">
                        <DiffView patch={diff.patch} />
                      </div>
                    </details>
                  ))}
                </div>
              </details>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

/** One line that says where the batch is: beside the panel's title (so a minimised or docked board
 *  still says it), and on the chip that brings a closed board back.
 *
 *  While the batch runs it says how many tasks are running and no more. The server reports no per-task
 *  end (the results arrive together in `batch_done`), so "3/6 done" mid-batch would be a number nobody
 *  measured; once the batch ends, the counts are the results'. */
export function batchSummary(c: BatchCounts, t: TFunc): string {
  if (c.running) {
    const parts = [t("agents.summary.running", { n: c.total })];
    if (c.stopping > 0) parts.push(t("agents.summary.stopping", { n: c.stopping }));
    return parts.join(" · ");
  }
  // A stream that broke before any result: there is nothing to count, only the failure to say.
  if (c.error && c.passed + c.failed === 0) return t("agents.summary.error");
  const parts = [t("agents.summary.done", { passed: c.passed, n: c.total })];
  if (c.failed > 0) parts.push(t("agents.summary.failed", { n: c.failed }));
  if (c.error) parts.push(t("agents.summary.error"));
  return parts.join(" · ");
}

/** The batch's one-line summary, beside the panel's title. Nothing without a batch. */
export function AgentBatchSummary() {
  const t = useT();
  const api = useOptionalAgentBatch();
  if (!api?.run) return null;
  return <>{batchSummary(batchCounts(api.run), t)}</>;
}

/** The board for a confirmed parallel batch: N isolated runs, live, with their conflicts.
 *
 *  It used to be a destination with its own launcher — eight task boxes, a worker count, a model
 *  field and three fusion modes, all chosen before anyone knew whether the work was parallel. The
 *  launcher is gone. Asking for several things IS the request for a batch; this renders the batch
 *  that was confirmed, and nothing else.
 *
 *  It draws the batch held by `AgentBatchProvider` and owns none of it. It is the body of a panel that
 *  can be minimised, closed and moved (2026-10-17), and each of those unmounts it: anything it held
 *  would be lost every time, while what the provider holds is the batch. Its title is the panel's. */
export function AgentBatchBoard() {
  const t = useT();
  const api = useOptionalAgentBatch();
  const run = api?.run ?? null;
  if (!api || !run) return null;
  const { running, labels, live, batch, error, batchId, stopping, workspace } = run;

  // The cards to render: the final results once the batch lands, else the in-flight task labels.
  const cards: { index: number; label: string; result: AgentResult | null }[] = batch
    ? batch.results.map((r) => ({ index: r.index, label: r.task, result: r }))
    : labels.map((label, index) => ({ index, label, result: null }));

  return (
    // A container, so the cards lay out by the room the panel has: three across in the main column,
    // one in a 288px side panel. The window's width says nothing about which of the two it is in.
    <div className="@container flex min-h-0 flex-col">
      <p className="border-b border-hairline pb-2 text-xs text-muted-foreground">{t("agents.safetyNote")}</p>

      {/* What is running, against what, and the controls that still apply. The config that used to
          live here — a model field, three fusion modes, a worker count, eight task boxes — is resolved
          by the system now, the same way it is for a single run. */}
      <div className="space-y-3 border-b border-hairline py-3">
        {/* The project comes from the one place a project is chosen, not from a field of its own. A
            second box asking for a path meant two answers to one question. */}
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t("agents.workspace")}
          </span>
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={workspace}>
            {workspace || t("code.sessions.defaultProject")}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {running ? (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("agents.running")}
            </span>
          ) : null}
          {/* Cooperative Stop for the WHOLE batch: every task halts before its next attempt. This and
              each card's Stop are the only things that stop anything; closing, minimising or moving
              the panel hides the board and leaves the batch running. */}
          {running ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={!batchId}
              onClick={() => void api.stopAll()}
              title={t("agents.stopAllTooltip")}
            >
              <Square className="h-4 w-4" /> {t("agents.stopAll")}
            </Button>
          ) : (
            // A finished batch's results stay until someone says they are done with them.
            <Button size="sm" variant="ghost" onClick={api.dismiss}>
              <XCircle className="h-4 w-4" /> {t("agents.dismiss")}
            </Button>
          )}
          {error ? <span className="text-xs text-bad-foreground">{error}</span> : null}
        </div>
      </div>

      {/* --- Batch summary bar (merged + conflicts + non-git honesty banner) --- */}
      {batch ? (
        <div className="space-y-2 border-b border-hairline py-3">
          {!batch.is_repo ? (
            <div className="flex items-start gap-2 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{t("agents.notRepoBanner")}</span>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <GitMerge className="h-3.5 w-3.5 text-accent" />
              {t("agents.merged")}: <span className="text-foreground">{batch.merged}</span>
            </span>
            <span>
              {t("agents.tasksCount")}: <span className="text-foreground">{batch.results.length}</span>
            </span>
          </div>
          {batch.conflicts.length > 0 ? (
            <div className="rounded-chip border border-bad/30 bg-bad/10 px-3 py-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-bad-foreground">
                <AlertTriangle className="h-3.5 w-3.5" />
                {t("agents.conflictsTitle")} ({batch.conflicts.length})
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{t("agents.conflictsNote")}</p>
              <ul className="mt-1.5 space-y-0.5">
                {batch.conflicts.map((p) => (
                  <li key={p} className="font-mono text-xs text-foreground/80">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          ) : batch.is_repo ? (
            <p className="text-xs text-muted-foreground">{t("agents.noConflicts")}</p>
          ) : null}
        </div>
      ) : null}

      {/* --- The task board --- */}
      {cards.length > 0 ? (
        <div className="grid gap-3 pt-3 @xl:grid-cols-2 @4xl:grid-cols-3">
          {cards.map((c) => (
            <AgentCard
              key={c.index}
              index={c.index}
              label={c.label}
              lines={(live[c.index] ?? []).map((e) => liveLine(e, t)).filter((l): l is string => l !== null)}
              result={c.result}
              conflicts={batch?.conflicts ?? []}
              running={running}
              canStop={batchId !== null}
              stopping={stopping[c.index] === true}
              onStop={() => void api.stopTask(c.index)}
              t={t}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
