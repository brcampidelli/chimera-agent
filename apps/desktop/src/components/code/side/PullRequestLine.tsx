import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import { STATE_KEYS, dotClass, summaryKey, usePullRequestState } from "@/components/code/PullRequestStatus";
import { getCodeSessionPullRequest } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * One line for the conversation's PR: the status dot, the number (a link), the state and the checks.
 *
 * The Code screen's side card (C5) draws this rather than the whole panel. It has no options and no
 * actions on purpose: the post-merge cleanup is asked by the panel, once, and a second component able
 * to ask it would be a second place that can remove a worktree. Renders nothing without a PR, so the
 * caller can show what it has instead (`hasPullRequest` tells it which).
 */
export function PullRequestStatusLine({ workspace, sessionId }: { workspace: string; sessionId: string }) {
  const t = useT();
  const { pr, number, status } = usePullRequestState(workspace, sessionId);
  if (number === null || !pr) return null;
  const data = status.data;
  const total = (data?.passed ?? 0) + (data?.failed ?? 0) + (data?.pending ?? 0);
  const stateKey = data && !data.error ? STATE_KEYS.get(data.state) : undefined;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs" data-testid="pr-line">
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
      {data && !data.error && total > 0 ? (
        <span className="text-muted-foreground">{t("code.pr.checks", { passed: data.passed, total })}</span>
      ) : null}
    </div>
  );
}

/** Whether the conversation has a PR recorded, from the same cached read the line makes. */
export function useHasPullRequest(sessionId: string | null): boolean | undefined {
  const record = useQuery({
    queryKey: ["code-session-pull-request", sessionId],
    queryFn: () => getCodeSessionPullRequest(sessionId ?? ""),
    enabled: sessionId !== null,
    retry: false,
  });
  if (sessionId === null || record.isError) return false;
  return record.data ? record.data.number !== null : undefined;
}
