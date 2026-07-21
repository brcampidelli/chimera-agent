import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Loader2, Play } from "lucide-react";
import { getRuns, streamRun, type RunEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT, type TFunc } from "@/lib/i18n";
import type { AttemptReceipt, RunReceipt } from "@/lib/types";

const MAX_TASK = 160;
const MAX_OUTPUT = 4000;

function truncate(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

/** The run's terminal verdict as a tone-coded badge: paused (warn) → passed (ok) → failed (bad). */
function StatusBadge({ run, t }: { run: RunReceipt; t: TFunc }) {
  if (run.paused) return <Badge tone="warn">{t("runs.paused")}</Badge>;
  return run.success ? (
    <Badge tone="ok">{t("runs.passed")}</Badge>
  ) : (
    <Badge tone="bad">{t("runs.failed")}</Badge>
  );
}

/** One attempt's proof row: index, verified check, revert flag, the diff it made, and (collapsed)
 *  the concrete verifier output. Shows only captured data — an empty field is simply omitted. */
function AttemptRow({ attempt, t }: { attempt: AttemptReceipt; t: TFunc }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">
          {t("runs.attempt")} {attempt.index}
        </span>
        <span
          className={attempt.verified ? "text-ok" : "text-muted-foreground"}
          title={attempt.verified ? "verified" : "not verified"}
        >
          {attempt.verified ? "✓" : "✗"}
        </span>
        {attempt.reverted ? <Badge tone="warn">↩ {t("runs.reverted")}</Badge> : null}
      </div>
      {attempt.diff_summary ? (
        <div className="mt-1.5 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
          <span className="text-foreground/70">{t("runs.diff")}: </span>
          {attempt.diff_summary}
        </div>
      ) : null}
      {attempt.verify_output ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">
            {t("runs.output")}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] text-muted-foreground">
            {truncate(attempt.verify_output, MAX_OUTPUT)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function RunCard({ run, t }: { run: RunReceipt; t: TFunc }) {
  return (
    <Panel
      title={truncate(run.task || "—", MAX_TASK)}
      action={<StatusBadge run={run} t={t} />}
    >
      <div className="flex items-center gap-2 px-4 py-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("runs.verifyCmd")}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
          {run.verify_command ?? t("runs.noVerify")}
        </span>
      </div>
      {run.attempts.map((attempt) => (
        <AttemptRow key={attempt.index} attempt={attempt} t={t} />
      ))}
    </Panel>
  );
}

/** Render one streamed run event as a compact live line, or null to skip (e.g. the `final` event —
 *  the `done` payload drives the terminal line). Backend `text` is English; map to the UI language. */
function liveLine(e: RunEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("runs.planning") : e.text;
  if (e.kind === "attempt") return `${t("runs.attempt")} ${e.index} — ${t("runs.verifying")}`;
  if (e.kind === "result")
    return `${t("runs.attempt")} ${e.index}: ${e.success ? t("runs.passed") : t("runs.failed")}`;
  return null; // `final` is covered by onDone
}

const fieldCls = "field w-full px-3 text-sm";

/** The in-app trigger: launch an autonomous run (`chimera solve` semantics) and stream it live. On
 *  finish it invalidates the receipts query so the new run appears in the list below. */
function NewRunPanel({ t }: { t: TFunc }) {
  const qc = useQueryClient();
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);

  function start() {
    if (!task.trim() || running) return;
    setRunning(true);
    setLines([]);
    const append = (s: string) => setLines((prev) => [...prev, s]);
    const finish = () => {
      setRunning(false);
      void qc.invalidateQueries({ queryKey: ["runs"] });
    };
    void streamRun(
      {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace: workspace.trim() || null,
        max_attempts: maxAttempts,
      },
      {
        onEvent: (e) => {
          const line = liveLine(e, t);
          if (line) append(line);
        },
        onDone: (d) => {
          append(d.success ? t("runs.doneOk") : t("runs.doneFail"));
          finish();
        },
        onError: () => {
          append(t("runs.doneFail"));
          finish();
        },
      },
    );
  }

  return (
    <Panel title={t("runs.new")}>
      <div className="space-y-2.5 px-4 py-3">
        <textarea
          className={`${fieldCls} min-h-[72px] resize-y py-2`}
          placeholder={t("runs.taskPlaceholder")}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          disabled={running}
        />
        <input
          className={`${fieldCls} h-9 font-mono text-xs`}
          placeholder={t("runs.verifyPlaceholder")}
          value={verify}
          onChange={(e) => setVerify(e.target.value)}
          disabled={running}
        />
        <input
          className={`${fieldCls} h-9 font-mono text-xs`}
          placeholder={t("runs.workspacePlaceholder")}
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          disabled={running}
        />
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {t("runs.maxAttempts")}
            <input
              type="number"
              min={1}
              max={10}
              className="field h-9 w-16 px-2 text-sm"
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
              disabled={running}
            />
          </label>
          <Button size="sm" disabled={!task.trim() || running} onClick={start}>
            {running ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> {t("runs.running")}
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> {t("runs.run")}
              </>
            )}
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">{t("runs.safetyNote")}</p>
        {lines.length > 0 ? (
          <div className="mt-1 space-y-1 rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] text-muted-foreground">
            {lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

export function Runs() {
  const t = useT();
  const q = useQuery({ queryKey: ["runs"], queryFn: getRuns });
  const runs = q.data ?? [];

  return (
    <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />}>
      <NewRunPanel t={t} />
      {q.isError ? (
        <Panel>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Panel>
      ) : q.isLoading ? (
        <Panel>
          <Spinner />
        </Panel>
      ) : runs.length === 0 ? (
        <Panel>
          <EmptyState text={t("runs.empty")} />
        </Panel>
      ) : (
        runs.map((run, i) => <RunCard key={`${run.ts}-${i}`} run={run} t={t} />)
      )}
    </Screen>
  );
}
