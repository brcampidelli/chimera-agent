import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Folder, GitBranch, ShieldHalf } from "lucide-react";

import { folderName } from "@/components/code/side/rules";
import { Badge } from "@/components/ui/panel";
import { getGitStatus } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";

/** One fact of a side card: an icon, a label for a screen reader, the value. */
export function Fact({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <span aria-hidden className="shrink-0 text-muted-foreground">
        {icon}
      </span>
      <dt className="sr-only">{label}</dt>
      <dd className="flex min-w-0 flex-1 items-center gap-1.5">{children}</dd>
    </div>
  );
}

/**
 * Where the conversation works and under what rules (C5): the folder, the branch, whether the folder
 * is a linked worktree, and the posture its turns are sent with.
 *
 * The git status is the query the git panel and the batch proposal already make (same key), and a
 * finished turn invalidates it, so a branch the agent switched shows here without polling.
 * The posture is the pair the Code screen sends with every turn, not a second reading of the config.
 */
export function EnvironmentCard() {
  const t = useT();
  const side = useConversationSide();
  const workspace = side?.workspace ?? "";
  const git = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
    enabled: side !== null && workspace !== "",
  });
  if (!side) return null;
  const posture = side.posture;
  const postureText = posture
    ? t("side.env.postureValue", {
        reach: t(`code.posture.reach.${posture.reach}`),
        approval: t(`code.posture.approval.${posture.approval}`),
      })
    : "";
  return (
    <dl className="space-y-1.5" data-testid="side-environment">
      {workspace ? (
        <Fact icon={<Folder className="h-3.5 w-3.5" />} label={t("side.env.folder")}>
          {/* The name fits a narrow column; the whole path is one hover away. */}
          <span className="truncate font-mono text-sm" title={workspace}>
            {folderName(workspace)}
          </span>
        </Fact>
      ) : (
        <p className="text-sm text-muted-foreground">{t("side.env.noFolder")}</p>
      )}
      {git.data?.is_repo ? (
        <Fact icon={<GitBranch className="h-3.5 w-3.5" />} label={t("side.env.branch")}>
          <span className="min-w-0 break-words font-mono text-sm" title={git.data.branch || undefined}>
            {git.data.branch || t("side.env.noBranch")}
          </span>
          {side.checkout.kind === "worktree" || git.data.worktree ? (
            <Badge tone="accent" title={side.checkout.project || undefined}>
              {side.checkout.projectName
                ? t("side.env.worktreeOf", { project: side.checkout.projectName })
                : t("side.env.worktree")}
            </Badge>
          ) : null}
        </Fact>
      ) : null}
      {posture ? (
        <Fact icon={<ShieldHalf className="h-3.5 w-3.5" />} label={t("side.env.posture")}>
          {/* Wraps instead of truncating (the approval policy is the end of the sentence); the title
              keeps the whole text for a reader who has the card collapsed to one line. */}
          <span className="min-w-0 break-words text-sm" title={postureText}>
            {postureText}
          </span>
        </Fact>
      ) : null}
    </dl>
  );
}
