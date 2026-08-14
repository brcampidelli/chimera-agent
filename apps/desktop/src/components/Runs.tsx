import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { getRuns } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { RunLauncher } from "@/components/run/RunLauncher";
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

/** Who approved this attempt, as its own badge rather than a ✓/✗.
 *
 *  The tick alone read `verified`, so an attempt approved by a real workspace diff plus a manager
 *  review rendered identically to one where an LLM approved prose and nothing else was checked —
 *  both ✗. That is precisely the collapse the three-way verdict exists to prevent, showing up on
 *  the one surface a human actually looks at. Tone carries the strength: only executable evidence
 *  is `ok`, an LLM approving prose with nothing else checked is `warn`, not a muted footnote. */
function EvidenceBadge({ evidence, t }: { evidence: string; t: TFunc }) {
  // "diff" sits with "diff+manager": both rest on a measured workspace change. It is the label an
  // attempt gets when files really changed and NO manager was configured — which used to be
  // reported as "manager", naming a reviewer that never ran.
  const machineBacked = evidence === "diff+manager" || evidence === "diff";
  const tone = evidence === "verifier" ? "ok" : machineBacked ? "accent" : "warn";
  return <Badge tone={tone}>{t(`runs.evidence.${evidence}`)}</Badge>;
}

/** One attempt's proof row: index, who approved it, whether the workspace measurably changed,
 *  any out-of-checkout side effect, the revert flag, the diff, and (collapsed) the verifier output.
 *  Shows only captured data — an empty field is simply omitted, and an UNKNOWN is shown as unknown
 *  rather than being quietly rendered as a negative. */
function AttemptRow({ attempt, t }: { attempt: AttemptReceipt; t: TFunc }) {
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          {t("runs.attempt")} {attempt.index}
        </span>
        <EvidenceBadge evidence={attempt.evidence} t={t} />
        {/* Explicitly three-way. `null` is "could not be measured" and gets its own chip: inferring
            it from the absence of the other two is the shortcut that erases the state. */}
        {attempt.diff_productive === null ? (
          <Badge tone="warn">{t("runs.diff.unknown")}</Badge>
        ) : attempt.diff_productive ? null : (
          <Badge tone="bad">{t("runs.diff.empty")}</Badge>
        )}
        {attempt.side_effects.length > 0 ? (
          <Badge tone="warn">
            ⚠ {t("runs.sideEffects")}: {attempt.side_effects.join(", ")}
          </Badge>
        ) : null}
        {attempt.reverted ? <Badge tone="warn">↩ {t("runs.reverted")}</Badge> : null}
      </div>
      {attempt.diff_summary ? (
        <div className="mt-1.5 whitespace-pre-wrap font-mono text-xs text-muted-foreground">
          <span className="text-foreground/70">{t("runs.diff")}: </span>
          {attempt.diff_summary}
        </div>
      ) : null}
      {attempt.verify_output ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
            {t("runs.output")}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground">
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
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
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

export function Runs({
  embedded = false,
  workspace,
}: { embedded?: boolean; workspace?: string } = {}) {
  const t = useT();
  // Keyed by project, so switching project refetches rather than showing the previous one's runs
  // until something else happens to invalidate.
  const q = useQuery({ queryKey: ["runs", workspace], queryFn: () => getRuns(workspace) });
  const runs = q.data ?? [];

  return (
    <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />} embedded={embedded}>
      <RunLauncher />
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
