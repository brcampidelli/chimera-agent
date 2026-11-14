import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, Search } from "lucide-react";
import { getRuns } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { useT, type TFunc } from "@/lib/i18n";
import { formatHash } from "@/lib/router";
import { cn } from "@/lib/utils";
import type { AttemptReceipt, RunReceipt } from "@/lib/types";

const MAX_TASK = 160;
const MAX_OUTPUT = 4000;
/** Runs drawn before "show older". Every attempt of every run used to render at once. */
const PAGE = 20;

type Outcome = "all" | "passed" | "failed" | "paused";

function outcomeOf(run: RunReceipt): Exclude<Outcome, "all"> {
  if (run.paused) return "paused";
  return run.success ? "passed" : "failed";
}

function truncate(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

/** The run's terminal verdict as a tone-coded badge: paused (warn) → passed (ok) → failed (bad).
 *
 *  A passed run can also carry the one caveat that undoes what "passed" is read to mean: the tree on
 *  disk is no longer the tree that verdict was about. Measured — a receipt said `verified: True` with
 *  the verifier's own `Ran 20 tests ... OK` stored beside it, and the delivered files failed all
 *  twenty. The verdict was true when it was given, so it stays; the caveat sits next to it rather
 *  than replacing it. Only `false` shows: `null` means nothing looked, and a badge for that would
 *  put a warning on every receipt written before the check existed.
 */
function StatusBadge({ run, t }: { run: RunReceipt; t: TFunc }) {
  if (run.paused) return <Badge tone="warn">{t("runs.paused")}</Badge>;
  const moveu = run.delivered_matches_verified === false;
  return (
    <>
      {run.success ? (
        <Badge tone="ok">{t("runs.passed")}</Badge>
      ) : (
        <Badge tone="bad">{t("runs.failed")}</Badge>
      )}
      {moveu && (
        <Badge tone="warn" title={t("runs.deliveryMoved.why")}>
          {t("runs.deliveryMoved")}
        </Badge>
      )}
    </>
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
      {/* The last attempt is the one that decided the run; the ones before it are how it got there.
          A run that retried four times drew four full attempts, and a long history scrolled for
          pages before reaching the next run. */}
      {run.attempts.length > 1 ? (
        <details className="border-t border-hairline">
          <summary className="cursor-pointer px-4 py-2 text-xs text-muted-foreground hover:text-foreground">
            {t("runs.earlier", { n: run.attempts.length - 1 })}
          </summary>
          {run.attempts.slice(0, -1).map((attempt) => (
            <AttemptRow key={attempt.index} attempt={attempt} t={t} />
          ))}
        </details>
      ) : null}
      {run.attempts.length > 0 ? (
        <AttemptRow attempt={run.attempts[run.attempts.length - 1]} t={t} />
      ) : null}
    </Panel>
  );
}

/** Render one streamed run event as a compact live line, or null to skip (e.g. the `final` event —
 *  the `done` payload drives the terminal line). Backend `text` is English; map to the UI language. */

/** What the autonomous runs did, most recent first.
 *
 *  The launcher used to sit on top of this list, which made it one of four boxes on the Work
 *  screen asking for the same sentence. It is one place now — the task console — and this is the archive of what that
 *  console started. Reading what a run did and starting the next one are different activities and
 *  they were sharing a column.
 */
export function Runs({
  embedded = false,
  workspace,
}: { embedded?: boolean; workspace?: string } = {}) {
  const t = useT();
  // Keyed by project, so switching project refetches rather than showing the previous one's runs
  // until something else happens to invalidate.
  const q = useQuery({ queryKey: ["runs", workspace], queryFn: () => getRuns(workspace) });
  const runs = useMemo(() => q.data ?? [], [q.data]);
  const [outcome, setOutcome] = useState<Outcome>("all");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE);
  const counts = useMemo(() => {
    const c = { all: runs.length, passed: 0, failed: 0, paused: 0 };
    for (const r of runs) c[outcomeOf(r)] += 1;
    return c;
  }, [runs]);
  const needle = query.trim().toLowerCase();
  const visible = runs.filter(
    (r) => (outcome === "all" || outcomeOf(r) === outcome) && (!needle || (r.task ?? "").toLowerCase().includes(needle)),
  );
  const chips: { value: Outcome; label: string }[] = [
    { value: "all", label: t("runs.filter.all", { n: counts.all }) },
    { value: "passed", label: t("runs.filter.passed", { n: counts.passed }) },
    { value: "failed", label: t("runs.filter.failed", { n: counts.failed }) },
    { value: "paused", label: t("runs.filter.paused", { n: counts.paused }) },
  ];

  return (
    <Screen title={t("runs.title")} icon={<ListChecks className="h-5 w-5" />} embedded={embedded}>
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
          <EmptyState
            text={t("runs.empty")}
            action={
              <Button size="sm" variant="outline" onClick={() => { window.location.hash = formatHash("code"); }}>
                {t("runs.empty.action")}
              </Button>
            }
          />
        </Panel>
      ) : (
        <>
          {/* Find a run by what it was asked, or by how it ended. The list only grew, and the one
              you wanted was somewhere in it. */}
          <div className="flex flex-wrap items-center gap-2">
            <label className="field flex h-8 min-w-56 flex-1 items-center gap-2 px-2.5">
              <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("runs.search")}
                aria-label={t("runs.search")}
                className="min-w-0 flex-1 bg-transparent text-sm outline-hidden"
              />
            </label>
            <div role="group" aria-label={t("runs.filter.label")} className="flex flex-wrap gap-1.5">
              {chips.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  aria-pressed={outcome === c.value}
                  onClick={() => setOutcome(c.value)}
                  className={cn(
                    "rounded-chip border px-2.5 py-1 text-xs transition-colors duration-1 ease-out",
                    focusRing,
                    outcome === c.value
                      ? "border-accent/50 bg-accent/15 text-accent-ink"
                      : "border-hairline text-foreground hover:bg-surface-hover",
                  )}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
          {visible.length === 0 ? (
            <Panel>
              <EmptyState text={t("runs.noMatch")} />
            </Panel>
          ) : (
            visible.slice(0, shown).map((run, i) => <RunCard key={`${run.ts}-${i}`} run={run} t={t} />)
          )}
          {visible.length > shown ? (
            <button
              type="button"
              onClick={() => setShown((n) => n + PAGE)}
              className={cn("self-center rounded-chip px-3 py-1.5 text-sm text-accent-ink hover:bg-surface-hover", focusRing)}
            >
              {t("runs.showOlder", { n: visible.length - shown })}
            </button>
          ) : null}
        </>
      )}
    </Screen>
  );
}
