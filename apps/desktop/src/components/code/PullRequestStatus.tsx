import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import {
  cleanupCodeSessionPullRequest,
  getCodeSessionPullRequest,
  getPullRequestStatus,
  setCodeSessionPullRequestOptions,
} from "@/lib/api";
import type { CodeSessionPullRequest, PullRequestStatus } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { offerTurn } from "@/lib/offered-turn";
import { useRoute } from "@/lib/router";

/** How often to re-read a PR, in ms, or false to stop.
 *
 * Only while the PR is OPEN and has checks still running, and never with the window hidden: a merged
 * or closed PR will not change, a PR whose checks all finished changes only when someone pushes, and
 * a hidden window has nobody to show the answer to. An error is not polled either — the refresh
 * button is how the owner asks again. Exported so the rule is tested as a rule, not through timers.
 */
export function pollInterval(status: PullRequestStatus | undefined, hidden: boolean): number | false {
  if (hidden || !status || status.error) return false;
  return status.state === "open" && status.pending > 0 ? 180_000 : false;
}

const STATE_KEYS = new Map<string, string>(
  Object.entries({
    open: "code.pr.state.open",
    merged: "code.pr.state.merged",
    closed: "code.pr.state.closed",
  }),
);

const MERGEABLE_KEYS = new Map<string, string>(
  Object.entries({
    mergeable: "code.pr.mergeable.mergeable",
    conflicting: "code.pr.mergeable.conflicting",
    unknown: "code.pr.mergeable.unknown",
  }),
);

/** Why the post-merge cleanup kept a worktree, from the server's stable tokens. */
const KEPT_KEYS = new Map<string, string>(
  Object.entries({
    not_chimera_worktree: "code.pr.kept.notChimeraWorktree",
    main_checkout: "code.pr.kept.mainCheckout",
    not_registered: "code.pr.kept.notRegistered",
    detached: "code.pr.kept.detached",
    current_branch: "code.pr.kept.currentBranch",
    dirty: "code.pr.kept.dirty",
    no_upstream: "code.pr.kept.noUpstream",
    unpushed: "code.pr.kept.unpushed",
    git_failed: "code.pr.kept.gitFailed",
  }),
);

/** The one word the dot stands for, translated — the aria-label never carries a raw server token. */
function summaryKey(status: PullRequestStatus | undefined): string {
  if (!status) return "code.pr.state.checksPending";
  if (status.error) return "code.pr.state.error";
  if (status.state !== "open") return STATE_KEYS.get(status.state) ?? "code.pr.state.error";
  if (status.failed > 0) return "code.pr.state.checksFailed";
  if (status.pending > 0) return "code.pr.state.checksPending";
  return "code.pr.state.open";
}

function dotClass(status: PullRequestStatus | undefined): string {
  if (!status) return "bg-warn";
  if (status.error) return "bg-bad";
  if (status.state === "closed") return "bg-muted-foreground";
  if (status.state === "merged") return "bg-ok";
  if (status.failed > 0) return "bg-bad";
  if (status.pending > 0) return "bg-warn";
  return "bg-ok";
}

/**
 * The pull request the Code conversation opened: its state, whether it can merge, its checks, and
 * the owner's two options for it (both off until switched on).
 *
 * The PR is read from the conversation's record (`GET /api/code/sessions/{id}/pull-request`), so it
 * is there again when the conversation is reopened — it used to live in this component's state and
 * vanished on the next render of the screen.
 *
 * "Fix when the CI fails" only OFFERS a turn: the button puts the text in the conversation's message
 * box and goes there. The text names the failing checks and asks the agent to read the logs itself
 * (`gh pr checks`, `gh run view --log-failed`) rather than pasting them in, so the log arrives as a
 * tool result under the turn's ordinary governance — approvals, and the taint every command output
 * carries — instead of as text the owner appears to have typed.
 *
 * "Clean up after the merge" asks the server once, when this card first sees the PR merged with the
 * option on. The server re-checks everything (option, merge, a turn running, the worktree clean and
 * pushed) and either removes the worktree and local branch and archives, or keeps all of it and says
 * why. The desktop bridge can read this card's data and cannot reach either action.
 */
export function PullRequestStatusPanel({ workspace, sessionId }: { workspace: string; sessionId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const { navigate } = useRoute();
  const recordKey = ["code-session-pull-request", sessionId];
  const record = useQuery({
    queryKey: recordKey,
    queryFn: () => getCodeSessionPullRequest(sessionId),
    retry: false,
  });
  const pr: CodeSessionPullRequest | undefined = record.data;
  const number = pr?.number ?? null;
  const status = useQuery({
    queryKey: ["pull-request-status", workspace, number],
    queryFn: () => getPullRequestStatus(workspace, number ?? 0),
    enabled: number !== null,
    // An answer with an error is stale at once, so the next look asks again instead of serving it.
    staleTime: (query) => (query.state.data?.error ? 0 : 60_000),
    refetchInterval: (query) => pollInterval(query.state.data, document.hidden),
    refetchIntervalInBackground: false,
    retry: false,
  });
  const data = status.data;
  const refetch = status.refetch;
  const open = data?.state === "open";

  // Back from a hidden window: read once, now, rather than waiting out an interval that was paused.
  useEffect(() => {
    if (number === null || !open) return;
    const onVisible = () => {
      if (!document.hidden) void refetch();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [number, open, refetch]);

  const options = useMutation({
    mutationFn: (change: { fix_on_ci_failure?: boolean; cleanup_on_merge?: boolean }) =>
      setCodeSessionPullRequestOptions(sessionId, change),
    onSuccess: (next) => qc.setQueryData(recordKey, next),
  });

  const cleanup = useMutation({ mutationFn: () => cleanupCodeSessionPullRequest(sessionId) });
  const askedCleanup = useRef<number | null>(null);
  const cleanupOn = pr?.cleanup_on_merge === true;
  const merged = data?.state === "merged" && !data.error;
  const runCleanup = cleanup.mutate;
  useEffect(() => {
    if (!cleanupOn || !merged || number === null || askedCleanup.current === number) return;
    askedCleanup.current = number;
    runCleanup();
  }, [cleanupOn, merged, number, runCleanup]);

  if (number === null || !pr) return null;

  const total = (data?.passed ?? 0) + (data?.failed ?? 0) + (data?.pending ?? 0);
  const failing = data?.failing_names ?? [];
  const offerFix = pr.fix_on_ci_failure && open && !data?.error && (data?.failed ?? 0) > 0;
  const mergeableKey = open && data ? MERGEABLE_KEYS.get(data.mergeable) : undefined;
  const stateKey = data && !data.error ? STATE_KEYS.get(data.state) : undefined;
  const done = cleanup.data;

  return (
    <div className="space-y-1.5 rounded-chip bg-surface-2 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span
          role="img"
          aria-label={t("code.pr.statusIndicator", { state: t(summaryKey(data)) })}
          className={`h-2 w-2 shrink-0 rounded-full ${dotClass(data)}`}
        />
        {pr.url ? (
          <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline">
            {t("code.pr.number", { number })} <ExternalLink className="h-3 w-3" />
          </a>
        ) : (
          <span>{t("code.pr.number", { number })}</span>
        )}
        {stateKey ? <span className="text-muted-foreground">{t(stateKey)}</span> : null}
        {mergeableKey ? <span className="text-muted-foreground">· {t(mergeableKey)}</span> : null}
        <span className="text-muted-foreground">
          {data?.error ? data.error : total > 0 ? t("code.pr.checks", { passed: data?.passed ?? 0, total }) : null}
        </span>
        <Button size="sm" variant="ghost" onClick={() => void refetch()} disabled={status.isFetching}>
          {t("code.pr.refresh")}
        </Button>
      </div>
      {failing.length > 0 ? (
        <ul className="space-y-0.5 font-mono">
          {failing.map((name) => (
            <li key={name} className="truncate text-bad-foreground" title={name}>
              {name}
            </li>
          ))}
        </ul>
      ) : null}
      {offerFix ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              offerTurn(
                sessionId,
                t("code.pr.fixTurn", { number, checks: failing.map((name) => `\`${name}\``).join(", ") }),
              );
              navigate("code");
            }}
          >
            {t("code.pr.offerFix")}
          </Button>
          <span className="text-muted-foreground">{t("code.pr.offerFixHint")}</span>
        </div>
      ) : null}
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          className="h-3 w-3 accent-accent"
          checked={pr.fix_on_ci_failure}
          disabled={options.isPending}
          onChange={(e) => options.mutate({ fix_on_ci_failure: e.target.checked })}
        />
        {t("code.pr.option.fix")}
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          className="h-3 w-3 accent-accent"
          checked={pr.cleanup_on_merge}
          disabled={options.isPending}
          onChange={(e) => options.mutate({ cleanup_on_merge: e.target.checked })}
        />
        {t("code.pr.option.cleanup")}
      </label>
      <div role="status">
        {done?.archived ? (
          <span className="text-ok-foreground">{t("code.pr.cleanup.done")}</span>
        ) : done ? (
          <span className="text-warn-foreground">
            {t("code.pr.cleanup.kept", { why: t(KEPT_KEYS.get(done.kept) ?? "code.pr.kept.gitFailed") })}
          </span>
        ) : cleanup.isError ? (
          <span className="text-bad-foreground">
            {t("code.pr.cleanup.refused")} {cleanup.error instanceof Error ? cleanup.error.message : ""}
          </span>
        ) : null}
      </div>
    </div>
  );
}
