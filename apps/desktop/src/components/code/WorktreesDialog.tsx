import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { listProjectWorktrees, pruneProjectWorktrees } from "@/lib/api";
import type { ProjectWorktree, ProjectWorktreePrune } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";

/** Why the server keeps a worktree, from its stable tokens (`chimera/core/worktree_prune.py`). An
 *  unknown token reads as a plain "kept" rather than as a raw identifier. */
const KEPT: Record<string, string> = {
  main_checkout: "code.worktrees.kept.mainCheckout",
  missing: "code.worktrees.kept.missing",
  not_chimera_worktree: "code.worktrees.kept.external",
  locked: "code.worktrees.kept.locked",
  in_use: "code.worktrees.kept.inUse",
  detached: "code.worktrees.kept.detached",
  current_branch: "code.worktrees.kept.currentBranch",
  dirty: "code.worktrees.kept.dirty",
  no_upstream: "code.worktrees.kept.noUpstream",
  unpushed: "code.worktrees.kept.unpushed",
  not_registered: "code.worktrees.kept.notRegistered",
  git_failed: "code.worktrees.kept.gitFailed",
};

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** A project's worktrees, each removable or with the reason it stays, and a two-step removal.
 *
 * Two steps on purpose: the confirm button exists only after a dry run answered, and it removes
 * exactly what that preview said would go — the server re-checks every refusal before acting, so a
 * row that changed in between is kept and says why. Worktrees made outside Chimera are listed with
 * their reason and never get a checkbox: the owner did not hand the app the right to remove them.
 */
export function WorktreesDialog({
  project,
  name,
  onClose,
}: {
  project: string | null;
  name: string;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<ProjectWorktreePrune | null>(null);
  const [done, setDone] = useState<ProjectWorktreePrune | null>(null);
  const list = useQuery({
    queryKey: ["project-worktrees", project],
    queryFn: () => listProjectWorktrees(project as string),
    enabled: project !== null,
  });
  const run = useMutation({
    mutationFn: ({ paths, dryRun }: { paths: string[]; dryRun: boolean }) =>
      pruneProjectWorktrees(project as string, paths, dryRun),
    onSuccess: (answer, { dryRun }) => {
      if (dryRun) {
        setPreview(answer);
        return;
      }
      setDone(answer);
      setPreview(null);
      setPicked(new Set());
      void qc.invalidateQueries({ queryKey: ["project-worktrees", project] });
      void qc.invalidateQueries({ queryKey: ["code-sessions"] });
    },
  });

  function close() {
    setPicked(new Set());
    setPreview(null);
    setDone(null);
    onClose();
  }

  function toggle(path: string, on: boolean) {
    const next = new Set(picked);
    if (on) next.add(path);
    else next.delete(path);
    setPicked(next);
    setPreview(null);
  }

  const rows = (list.data?.worktrees ?? []).filter((row) => !row.main);
  const going = (preview?.results ?? []).filter((r) => r.would_remove).map((r) => r.path);
  const removed = (done?.results ?? []).filter((r) => r.removed);
  const archived = removed.reduce((n, r) => n + r.archived.length, 0);

  return (
    <Dialog
      open={project !== null}
      onOpenChange={(next) => !next && close()}
      title={t("code.worktrees.title", { name })}
      description={t("code.worktrees.intro")}
    >
      {list.isError ? (
        <p className="text-sm text-bad-foreground">{t("code.worktrees.failed")}</p>
      ) : !list.data ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("code.worktrees.empty")}</p>
      ) : (
        <ul className="max-h-80 space-y-1 overflow-auto">
          {rows.map((row) => (
            <WorktreeLine
              key={row.path}
              row={row}
              checked={picked.has(row.path)}
              onToggle={(on) => toggle(row.path, on)}
              disabled={run.isPending}
            />
          ))}
        </ul>
      )}
      <div role="status" className="mt-3 space-y-1 text-xs">
        {preview ? (
          going.length === 0 ? (
            <p className="text-muted-foreground">{t("code.worktrees.previewNone")}</p>
          ) : (
            <p>{t("code.worktrees.previewSome", { n: going.length })}</p>
          )
        ) : null}
        {preview?.results
          .filter((r) => !r.would_remove)
          .map((r) => (
            <p key={r.path} className="text-muted-foreground">
              {folderName(r.path)}: {t(KEPT[r.kept] ?? "code.worktrees.kept.unknown")}
            </p>
          ))}
        {done ? (
          <p className="text-ok-foreground">
            {t("code.worktrees.removed", { n: removed.length, a: archived })}
          </p>
        ) : null}
        {removed
          .filter((r) => !r.branch_deleted)
          .map((r) => (
            <p key={r.path} className="text-muted-foreground">
              {r.branch}: {t("code.worktrees.branchKept")}
            </p>
          ))}
        {run.isError ? <p className="text-bad-foreground">{t("code.worktrees.failed")}</p> : null}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={close}>
          {t("common.close")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={picked.size === 0 || run.isPending}
          onClick={() => run.mutate({ paths: [...picked], dryRun: true })}
        >
          {t("code.worktrees.preview")}
        </Button>
        <Button
          size="sm"
          disabled={going.length === 0 || run.isPending}
          onClick={() => run.mutate({ paths: going, dryRun: false })}
        >
          {run.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {t("code.worktrees.remove", { n: going.length })}
        </Button>
      </div>
    </Dialog>
  );
}

function WorktreeLine({
  row,
  checked,
  onToggle,
  disabled,
}: {
  row: ProjectWorktree;
  checked: boolean;
  onToggle: (on: boolean) => void;
  disabled: boolean;
}) {
  const t = useT();
  const label = folderName(row.path);
  return (
    <li className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-surface-hover">
      {row.removable ? (
        <input
          type="checkbox"
          className="mt-0.5 h-3 w-3 accent-accent"
          aria-label={t("code.worktrees.select", { name: label })}
          checked={checked}
          disabled={disabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
      ) : (
        <span className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm" title={row.path}>
          {label}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {row.branch || t("code.worktrees.detachedHead")}
          {row.merged ? ` · ${t("code.worktrees.merged")}` : ""}
          {row.conversations.length
            ? ` · ${t("code.worktrees.conversations", { n: row.conversations.length })}`
            : ""}
          {row.untracked_count
            ? ` · ${t("code.worktrees.untracked", { n: row.untracked_count })}`
            : ""}
        </p>
        <p className={row.removable ? "text-xs text-ok-foreground" : "text-xs text-muted-foreground"}>
          {row.removable
            ? t("code.worktrees.removable")
            : t(KEPT[row.kept] ?? "code.worktrees.kept.unknown")}
        </p>
      </div>
    </li>
  );
}
