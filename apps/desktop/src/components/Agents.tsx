import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  GitMerge,
  Loader2,
  Play,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";
import { streamAgents, type AgentTaggedEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { AgentResult, AgentsBatch, FileDiff } from "@/lib/types";
import { cn } from "@/lib/utils";

const fieldCls = "field w-full px-3 text-sm";

/** A hand-rolled unified-diff renderer: + green, - red, @@ dim headers, file headers muted.
 *  (Kept local to this screen, mirroring the Code screen's own copy — a small presentational helper.) */
function DiffLines({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <pre className="overflow-x-auto rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] leading-relaxed">
      {lines.map((line, i) => {
        let cls = "text-muted-foreground";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-foreground/50";
        else if (line.startsWith("@@")) cls = "text-accent/70";
        else if (line.startsWith("+")) cls = "text-ok";
        else if (line.startsWith("-")) cls = "text-bad";
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

/** Map one tagged live event to a compact localized line (backend `text` is English; the attempt
 *  number is already inside `text`, so we don't read the numeric index here). */
function liveLine(e: AgentTaggedEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("agents.planning") : e.text;
  if (e.kind === "attempt") return e.text; // e.g. "attempt 2/3"
  if (e.kind === "result") return e.text; // e.g. "attempt 2 passed/failed"
  if (e.kind === "edit" && e.path) return `${t("agents.edited")} ${e.path}`;
  return null;
}

/** One task's input row: a task textarea + optional verify command, with a remove button. */
function TaskRow({
  index,
  task,
  verify,
  disabled,
  removable,
  onTask,
  onVerify,
  onRemove,
}: {
  index: number;
  task: string;
  verify: string;
  disabled: boolean;
  removable: boolean;
  onTask: (v: string) => void;
  onVerify: (v: string) => void;
  onRemove: () => void;
}) {
  const t = useT();
  return (
    <div className="space-y-1.5 rounded-chip border border-white/10 bg-white/[0.02] p-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("agents.task")} {index + 1}
        </span>
        {removable ? (
          <button
            type="button"
            onClick={onRemove}
            disabled={disabled}
            title={t("agents.removeTask")}
            aria-label={t("agents.removeTask")}
            className="text-muted-foreground transition-colors hover:text-bad disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <textarea
        className={`${fieldCls} min-h-[56px] resize-y py-2`}
        placeholder={t("agents.taskPlaceholder")}
        value={task}
        onChange={(e) => onTask(e.target.value)}
        disabled={disabled}
      />
      <input
        className={`${fieldCls} h-8 font-mono text-xs`}
        placeholder={t("agents.verifyPlaceholder")}
        value={verify}
        onChange={(e) => onVerify(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}

/** One task card on the board: its live status while running, then its real final result. */
function AgentCard({
  index,
  label,
  lines,
  result,
  conflicts,
  running,
  t,
}: {
  index: number;
  label: string;
  lines: string[];
  result: AgentResult | null;
  conflicts: string[];
  running: boolean;
  t: TFunc;
}) {
  // Which of this task's changed files collided with another task (left unmerged).
  const conflictSet = useMemo(() => new Set(conflicts), [conflicts]);
  const done = result !== null;
  return (
    <div className="flex flex-col rounded-chip border border-white/10 bg-white/[0.02]">
      <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2">
        <span className="font-mono text-[11px] text-muted-foreground">#{index + 1}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={label}>
          {label || t("agents.untitled")}
        </span>
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
          <div className="space-y-0.5 rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] text-muted-foreground">
            {lines.slice(-4).map((line, i) => (
              <div key={i} className="truncate">
                {line}
              </div>
            ))}
          </div>
        ) : null}

        {done ? (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span>
                {t("agents.attempts")}: {result.attempts}
              </span>
              {result.reverted ? <Badge tone="warn">↩ {t("agents.reverted")}</Badge> : null}
              <span>
                {t("agents.changedFiles")}: {result.changed_paths.length}
              </span>
            </div>

            {result.changed_paths.length > 0 ? (
              <ul className="space-y-0.5">
                {result.changed_paths.map((p) => (
                  <li
                    key={p}
                    className="flex items-center gap-1.5 font-mono text-[11px] text-foreground/80"
                  >
                    <span className="truncate">{p}</span>
                    {conflictSet.has(p) ? <Badge tone="bad">{t("agents.conflict")}</Badge> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[11px] text-muted-foreground">{t("agents.noChanges")}</p>
            )}

            {result.diffs.length > 0 ? (
              <details className="rounded-chip bg-white/[0.02]">
                <summary className="cursor-pointer px-2 py-1.5 text-[11px] text-muted-foreground hover:text-foreground">
                  {t("agents.showDiff")} ({result.diffs.length})
                </summary>
                <div className="space-y-2 px-2 pb-2">
                  {result.diffs.map((diff: FileDiff) => (
                    <details key={diff.path} className="rounded-chip bg-white/[0.02]" open>
                      <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-[11px] text-foreground/80 hover:text-foreground">
                        {diff.path}
                        {diff.truncated ? <Badge tone="muted">{t("agents.truncated")}</Badge> : null}
                      </summary>
                      <div className="px-2 pb-2">
                        <DiffLines patch={diff.patch} />
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

export function Agents() {
  const t = useT();
  const [workspace, setWorkspace] = useState("");
  const [tasks, setTasks] = useState<{ task: string; verify: string }[]>([
    { task: "", verify: "" },
    { task: "", verify: "" },
  ]);
  const [maxWorkers, setMaxWorkers] = useState(4);
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<"single" | "fuse" | "cascade">("single");

  const [running, setRunning] = useState(false);
  const [labels, setLabels] = useState<string[]>([]); // the submitted task texts, in order
  const [live, setLive] = useState<Record<number, string[]>>({});
  const [batch, setBatch] = useState<AgentsBatch | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canRun = !running && tasks.some((r) => r.task.trim());

  function setTaskAt(i: number, patch: Partial<{ task: string; verify: string }>) {
    setTasks((prev) => prev.map((row, idx) => (idx === i ? { ...row, ...patch } : row)));
  }
  function addTask() {
    if (tasks.length >= 8) return;
    setTasks((prev) => [...prev, { task: "", verify: "" }]);
  }
  function removeTask(i: number) {
    setTasks((prev) => (prev.length <= 1 ? prev : prev.filter((_, idx) => idx !== i)));
  }

  function runAll() {
    const submitted = tasks.filter((r) => r.task.trim());
    if (submitted.length === 0 || running) return;
    setRunning(true);
    setBatch(null);
    setError(null);
    setLive({});
    setLabels(submitted.map((r) => r.task.trim()));
    const append = (index: number, line: string) =>
      setLive((prev) => ({ ...prev, [index]: [...(prev[index] ?? []), line] }));

    void streamAgents(
      {
        tasks: submitted.map((r) => ({ task: r.task.trim(), verify: r.verify.trim() || null })),
        workspace: workspace.trim() || null,
        max_workers: maxWorkers,
        model: model.trim() || null,
        fuse: mode === "fuse",
        cascade: mode === "cascade",
      },
      {
        onStart: (s) => setLabels(s.tasks),
        onEvent: (e) => {
          const line = liveLine(e, t);
          if (line && typeof e.index === "number") append(e.index, line);
        },
        onBatchDone: (b) => {
          setBatch(b);
          setRunning(false);
        },
        onError: (msg) => {
          setError(msg);
          setRunning(false);
        },
      },
    );
  }

  // The cards to render: the final results once the batch lands, else the in-flight task labels.
  const cards: { index: number; label: string; result: AgentResult | null }[] = batch
    ? batch.results.map((r) => ({ index: r.index, label: r.task, result: r }))
    : labels.map((label, index) => ({ index, label, result: null }));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-white/5 px-5 py-3 text-accent">
        <Boxes className="h-5 w-5" />
        <h1 className="text-sm font-semibold text-foreground">{t("agents.title")}</h1>
      </div>
      <p className="border-b border-white/5 px-5 py-2 text-[11px] text-muted-foreground">
        {t("agents.safetyNote")}
      </p>

      <div className="min-h-0 flex-1 overflow-auto">
        {/* --- Config + task rows --- */}
        <div className="space-y-3 border-b border-white/5 p-4">
          <div>
            <label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("agents.workspace")}
            </label>
            <input
              className={`${fieldCls} mt-1.5 h-9 font-mono text-xs`}
              placeholder={t("agents.workspacePlaceholder")}
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
              disabled={running}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <input
              className={`${fieldCls} h-9 min-w-0 flex-1 font-mono text-xs`}
              placeholder={t("agents.modelPlaceholder")}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={running}
            />
            <div className="flex shrink-0 overflow-hidden rounded-chip border border-white/10">
              {(["single", "fuse", "cascade"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  disabled={running}
                  className={cn(
                    "px-2.5 py-1.5 text-[11px] transition-colors disabled:opacity-50",
                    mode === m
                      ? "bg-accent/20 text-accent"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t(`agents.mode.${m}` as const)}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {t("agents.maxWorkers")}
              <input
                type="number"
                min={1}
                max={8}
                className="field h-9 w-16 px-2 text-sm"
                value={maxWorkers}
                onChange={(e) => setMaxWorkers(Math.min(8, Math.max(1, Number(e.target.value) || 1)))}
                disabled={running}
              />
            </label>
          </div>

          <div className="grid gap-2 lg:grid-cols-2">
            {tasks.map((row, i) => (
              <TaskRow
                key={i}
                index={i}
                task={row.task}
                verify={row.verify}
                disabled={running}
                removable={tasks.length > 1}
                onTask={(v) => setTaskAt(i, { task: v })}
                onVerify={(v) => setTaskAt(i, { verify: v })}
                onRemove={() => removeTask(i)}
              />
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="ghost" onClick={addTask} disabled={running || tasks.length >= 8}>
              <Plus className="h-4 w-4" /> {t("agents.addTask")}
            </Button>
            <Button size="sm" disabled={!canRun} onClick={runAll}>
              {running ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("agents.running")}
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" /> {t("agents.runAll")}
                </>
              )}
            </Button>
            {error ? <span className="text-[11px] text-bad">{error}</span> : null}
          </div>
        </div>

        {/* --- Batch summary bar (merged + conflicts + non-git honesty banner) --- */}
        {batch ? (
          <div className="space-y-2 border-b border-white/5 px-4 py-3">
            {!batch.is_repo ? (
              <div className="flex items-start gap-2 rounded-chip border border-[hsl(38_92%_50%/0.3)] bg-[hsl(38_92%_50%/0.08)] px-3 py-2 text-[11px] text-[hsl(38_92%_62%)]">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{t("agents.notRepoBanner")}</span>
              </div>
            ) : null}
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <GitMerge className="h-3.5 w-3.5 text-accent" />
                {t("agents.merged")}: <span className="text-foreground">{batch.merged}</span>
              </span>
              <span>
                {t("agents.tasksCount")}: <span className="text-foreground">{batch.results.length}</span>
              </span>
            </div>
            {batch.conflicts.length > 0 ? (
              <div className="rounded-chip border border-[hsl(0_84%_60%/0.3)] bg-[hsl(0_84%_60%/0.08)] px-3 py-2">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-bad">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {t("agents.conflictsTitle")} ({batch.conflicts.length})
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">{t("agents.conflictsNote")}</p>
                <ul className="mt-1.5 space-y-0.5">
                  {batch.conflicts.map((p) => (
                    <li key={p} className="font-mono text-[11px] text-foreground/80">
                      {p}
                    </li>
                  ))}
                </ul>
              </div>
            ) : batch.is_repo ? (
              <p className="text-[11px] text-muted-foreground">{t("agents.noConflicts")}</p>
            ) : null}
          </div>
        ) : null}

        {/* --- The task board --- */}
        {cards.length > 0 ? (
          <div className="grid gap-3 p-4 lg:grid-cols-2 xl:grid-cols-3">
            {cards.map((c) => (
              <AgentCard
                key={c.index}
                index={c.index}
                label={c.label}
                lines={live[c.index] ?? []}
                result={c.result}
                conflicts={batch?.conflicts ?? []}
                running={running}
                t={t}
              />
            ))}
          </div>
        ) : (
          <div className="px-5 py-8 text-center text-xs text-muted-foreground">{t("agents.empty")}</div>
        )}
      </div>
    </div>
  );
}
