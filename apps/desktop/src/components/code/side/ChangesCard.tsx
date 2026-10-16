import { useQuery } from "@tanstack/react-query";

import { PullRequestStatusLine, useHasPullRequest } from "@/components/code/side/PullRequestLine";
import { CHANGES_SHOWN } from "@/components/code/side/rules";
import { getGitStatus } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";

/**
 * What the conversation changed (C5): its pull request when it has one, drawn by the C1 status line
 * from the same cached reads as the Work screen's card; otherwise the folder's uncommitted files,
 * from the git status the git panel reads (same key, refreshed when a turn finishes).
 *
 * The PR wins because it is the conversation's own record. The git status is the folder's, and in a
 * folder shared by several conversations it can carry another conversation's edits; it is labelled
 * as the folder's for that reason.
 */
export function ChangesCard() {
  const t = useT();
  const side = useConversationSide();
  const workspace = side?.workspace ?? "";
  const sessionId = side?.sessionId ?? null;
  const hasPr = useHasPullRequest(side ? sessionId : null);
  const git = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
    enabled: side !== null && workspace !== "" && hasPr === false,
  });
  if (!side || !workspace) return null;
  if (hasPr && sessionId) {
    return (
      <div data-testid="side-changes">
        <PullRequestStatusLine workspace={workspace} sessionId={sessionId} />
      </div>
    );
  }
  if (hasPr === undefined || !git.data) return null;
  if (!git.data.is_repo) return <p className="text-sm text-muted-foreground">{t("side.changes.notRepo")}</p>;
  const files = git.data.files;
  if (files.length === 0) return <p className="text-sm text-muted-foreground">{t("side.changes.clean")}</p>;
  const shown = files.slice(0, CHANGES_SHOWN);
  return (
    <div className="space-y-1" data-testid="side-changes">
      <p className="text-sm">{t("side.changes.files", { n: files.length })}</p>
      <ul className="space-y-0.5">
        {shown.map((file) => (
          <li key={file.path} className="flex min-w-0 items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground" title={file.path}>
              {file.path}
            </span>
            <span className="shrink-0 font-mono text-muted-foreground">
              {file.untracked ? "??" : `${file.x === " " ? "·" : file.x}${file.y === " " ? "·" : file.y}`}
            </span>
          </li>
        ))}
      </ul>
      {files.length > shown.length ? (
        <p className="text-xs text-muted-foreground">{t("side.changes.more", { n: files.length - shown.length })}</p>
      ) : null}
    </div>
  );
}
