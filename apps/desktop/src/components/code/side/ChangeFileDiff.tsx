import { useQuery } from "@tanstack/react-query";

import { DiffView } from "@/components/code/DiffView";
import { ErrorState } from "@/components/ui/async";
import { getGitDiff } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** The diff of one uncommitted file, opened from the Changes card. Lines take comments for the
 *  conversation the card belongs to, so the owner can review the change where it is listed. The
 *  query key is the git panel's, so the two screens share one read. */
export function ChangeFileDiff({
  workspace,
  path,
  staged,
  sessionId,
}: {
  workspace: string;
  path: string;
  staged: boolean;
  sessionId: string | null;
}) {
  const t = useT();
  const diff = useQuery({
    queryKey: ["git-diff", workspace, path, staged],
    queryFn: () => getGitDiff(workspace, path, staged),
  });
  if (diff.isLoading) return <div className="py-1 text-xs text-muted-foreground">…</div>;
  if (diff.isError) return <ErrorState error={diff.error} onRetry={() => void diff.refetch()} />;
  if (!diff.data?.patch) return <p className="py-1 text-xs text-muted-foreground">{t("code.git.noDiff")}</p>;
  return (
    <div className="pt-1">
      <DiffView patch={diff.data.patch} commentable={sessionId ? { sessionId, file: path, origin: "git" } : undefined} />
    </div>
  );
}
