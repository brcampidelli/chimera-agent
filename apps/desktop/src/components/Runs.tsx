import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { getRuns } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
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

export function Runs() {
  const t = useT();
  const q = useQuery({ queryKey: ["runs"], queryFn: getRuns });

  if (q.isLoading) {
    return (
      <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  const runs = q.data ?? [];

  if (runs.length === 0) {
    return (
      <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />}>
        <Panel>
          <EmptyState text={t("runs.empty")} />
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />}>
      {runs.map((run, i) => (
        <RunCard key={`${run.ts}-${i}`} run={run} t={t} />
      ))}
    </Screen>
  );
}
