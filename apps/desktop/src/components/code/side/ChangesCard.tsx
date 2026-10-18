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
export function ChangesCard({ alone = false }: { alone?: boolean } = {}) {
  const t = useT();
  const facts = useChangesFacts();
  const { workspace, sessionId, hasPr } = facts;
  const git = facts.git;
  // A folder with nothing uncommitted, or that is not a repository, has no changes to show: the dock
  // leaves the card out, and only a card drawn where it cannot vanish (`alone`) says which.
  if (alone && workspace && !hasPr && git && (!git.is_repo || git.files.length === 0)) {
    return <p className="text-sm text-muted-foreground">{t(git.is_repo ? "side.changes.clean" : "side.changes.notRepo")}</p>;
  }
  if (changesSilent(facts)) return null;
  if (hasPr && sessionId) {
    return (
      <div data-testid="side-changes">
        <PullRequestStatusLine workspace={workspace} sessionId={sessionId} />
      </div>
    );
  }
  if (!git) return null;
  const files = git.files;
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

/** What the card draws from, shared with the dock's silence rule (`useSilentPanels`) so the two read
 *  the same value. `enabled: false` asks nothing, for a card the person closed. */
export function useChangesFacts(enabled = true) {
  const side = useConversationSide();
  const workspace = side?.workspace ?? "";
  const sessionId = side?.sessionId ?? null;
  const hasPr = useHasPullRequest(enabled && side ? sessionId : null);
  const git = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
    enabled: enabled && side !== null && workspace !== "" && hasPr === false,
  });
  return { side, workspace, sessionId, hasPr, git: git.data };
}

/** The card draws nothing in a dock: no conversation or folder, neither the PR record nor the git status
 *  read yet, or a folder with nothing uncommitted (clean, or not a repository). */
export function changesSilent({ side, workspace, sessionId, hasPr, git }: ReturnType<typeof useChangesFacts>): boolean {
  if (!side || !workspace) return true;
  if (hasPr && sessionId) return false;
  return hasPr === undefined || !git || !git.is_repo || git.files.length === 0;
}
