import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitBranch, Loader2 } from "lucide-react";

import { getGitDiff, getGitStatus, gitCommit } from "@/lib/api";
import type { GitFile } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { DiffView } from "@/components/code/DiffView";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Git for the workspace the agent is editing.
 *
 * Moved out of the Code screen unchanged. It lived there because the workspace path lived there,
 * and it was the largest of the five panels competing for one column — the conversation, which is
 * what the screen is FOR, was being laid out at zero height. Now that the chosen root persists,
 * any screen can ask for it, so this can sit where reviewing changes actually belongs.
 */

/** One changed-file row in the git panel: a clickable name (toggles its diff), its porcelain status
 *  code, and a checkbox that selects it for the explicit-path commit. */
function GitRow({
  file,
  checked,
  active,
  onToggleCheck,
  onSelect,
}: {
  file: GitFile;
  checked: boolean;
  active: boolean;
  onToggleCheck: () => void;
  onSelect: () => void;
}) {
  const code = `${file.x === " " ? "·" : file.x}${file.y === " " ? "·" : file.y}`;
  return (
    <div
      className={cn(
        "flex items-center gap-2 px-2 py-1 text-xs",
        active ? "bg-accent/10" : "hover:bg-surface-hover",
      )}
    >
      <input
        type="checkbox"
        className="h-3 w-3 shrink-0 accent-accent"
        checked={checked}
        onChange={onToggleCheck}
      />
      <button
        onClick={onSelect}
        title={file.path}
        className={cn(
          "min-w-0 flex-1 truncate text-left font-mono",
          active ? "text-accent" : "text-foreground/80 hover:text-foreground",
        )}
      >
        {file.path}
      </button>
      <span className="shrink-0 font-mono text-xs text-muted-foreground">{code}</span>
    </div>
  );
}

/** The git panel: real `git status` grouped by staged / modified / untracked, a per-file diff on
 *  click, and a commit box that stages the EXPLICITLY selected paths (never `git add -A`). When the
 *  folder isn't a git repo (or git is missing), an honest empty-state invites `git init`. */
export function GitPanel({ workspace }: { workspace: string }) {
  const t = useT();
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
  });
  const status = statusQ.data;
  const files = useMemo(() => status?.files ?? [], [status]);
  const staged = files.filter((f) => f.staged);
  const modified = files.filter((f) => !f.staged && !f.untracked);
  const untracked = files.filter((f) => f.untracked);

  const [message, setMessage] = useState("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<{ path: string; staged: boolean } | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitErr, setCommitErr] = useState(false);
  const [commitHash, setCommitHash] = useState<string | null>(null);

  // Default-select the modified + untracked paths whenever the changed-file set changes.
  const filesKey = files.map((f) => `${f.path}:${f.staged}`).join("\n");
  useEffect(() => {
    const next: Record<string, boolean> = {};
    for (const f of files) next[f.path] = !f.staged; // modified/untracked default-checked
    setChecked(next);
    setSelected(null);
    setCommitHash(null);
    setCommitErr(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filesKey]);

  const diffQ = useQuery({
    queryKey: ["git-diff", workspace, selected?.path, selected?.staged],
    queryFn: () => getGitDiff(workspace || null, selected!.path, selected!.staged),
    enabled: selected !== null,
  });

  const selectedPaths = files.filter((f) => checked[f.path]).map((f) => f.path);

  async function commit() {
    if (!message.trim() || selectedPaths.length === 0 || committing) return;
    setCommitting(true);
    setCommitErr(false);
    setCommitHash(null);
    try {
      const res = await gitCommit(workspace || null, message.trim(), selectedPaths);
      if (res.ok) {
        setCommitHash(res.commit);
        setMessage("");
        await qc.invalidateQueries({ queryKey: ["git-status", workspace] });
        void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      } else {
        setCommitErr(true);
      }
    } catch {
      setCommitErr(true);
    } finally {
      setCommitting(false);
    }
  }

  function toggle(path: string) {
    setChecked((prev) => ({ ...prev, [path]: !prev[path] }));
  }
  function pick(path: string, isStaged: boolean) {
    setSelected((prev) => (prev?.path === path && prev.staged === isStaged ? null : { path, staged: isStaged }));
  }

  function group(label: string, list: GitFile[], isStaged: boolean) {
    if (list.length === 0) return null;
    return (
      <div>
        <div className="px-2 pt-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        {list.map((f) => (
          <GitRow
            key={`${isStaged ? "s" : "w"}:${f.path}`}
            file={f}
            checked={!!checked[f.path]}
            active={selected?.path === f.path && selected.staged === isStaged}
            onToggleCheck={() => toggle(f.path)}
            onSelect={() => pick(f.path, isStaged)}
          />
        ))}
      </div>
    );
  }

  return (
    <section className="border-t border-hairline">
      <div className="flex items-center gap-2 px-4 pt-2.5 text-accent">
        <GitBranch className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.git.title")}</h2>
        {status?.is_repo ? (
          <span className="font-mono text-xs text-muted-foreground">
            {t("code.git.branch")}: {status.branch || "—"}
          </span>
        ) : null}
      </div>

      {statusQ.isLoading ? (
        <div className="flex justify-center py-4 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : !status?.is_repo ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("code.git.notRepo")}</p>
      ) : files.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("code.git.clean")}</p>
      ) : (
        <div className="flex flex-col gap-2 px-2 py-2 lg:flex-row lg:items-start">
          <div className="min-w-0 flex-1 rounded-chip bg-surface-2 py-1">
            {group(t("code.git.staged"), staged, true)}
            {group(t("code.git.modified"), modified, false)}
            {group(t("code.git.untracked"), untracked, false)}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <input
              className="field h-9 px-3 text-xs"
              placeholder={t("code.git.commitMsg")}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={committing}
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!message.trim() || selectedPaths.length === 0 || committing}
                onClick={() => void commit()}
              >
                {committing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                {t("code.git.commit")} ({selectedPaths.length})
              </Button>
              {commitHash ? (
                <span className="font-mono text-xs text-ok">
                  {t("code.git.committed")} {commitHash}
                </span>
              ) : null}
              {commitErr ? <span className="text-xs text-bad">{t("code.git.commitError")}</span> : null}
            </div>
          </div>
        </div>
      )}

      {selected ? (
        <div className="px-4 pb-3">
          {diffQ.isLoading ? (
            <div className="py-2 text-xs text-muted-foreground">…</div>
          ) : diffQ.data?.patch ? (
            <DiffView patch={diffQ.data.patch} />
          ) : (
            <p className="text-xs text-muted-foreground">{t("code.noDiff")}</p>
          )}
        </div>
      ) : null}

      <p className="px-4 pb-2 text-xs text-muted-foreground">{t("code.git.gitNote")}</p>
    </section>
  );
}
