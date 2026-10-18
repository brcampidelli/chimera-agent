import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { getGitStatus } from "@/lib/api";
import { buildGitMarks, type GitMark, type GitMarks } from "@/lib/gitMarks";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The folder's git status as marks, from the cache the Changes card and the git panel share
 * (`["git-status", workspace]`), so the tree never holds a second opinion about the working tree.
 * Null outside a repository or before the first answer: no marks, not wrong ones.
 */
export function useGitMarks(workspace: string | null): { marks: GitMarks | null; changed: ReadonlyArray<{ path: string; mark: GitMark }> } {
  const git = useQuery({
    queryKey: ["git-status", workspace ?? ""],
    queryFn: () => getGitStatus(workspace),
  });
  return useMemo(() => {
    const files = git.data?.is_repo ? git.data.files : [];
    if (files.length === 0) return { marks: null, changed: [] };
    const marks = buildGitMarks(files);
    const changed = [...marks.files.entries()].map(([path, mark]) => ({ path, mark }));
    return { marks, changed };
  }, [git.data]);
}

const TONE: Record<GitMark, string> = {
  M: "text-warn-foreground",
  A: "text-ok-foreground",
  U: "text-ok-foreground",
  D: "text-bad-foreground",
};
const DOT: Record<GitMark, string> = {
  M: "bg-warn",
  A: "bg-ok",
  U: "bg-ok",
  D: "bg-bad",
};

/** A letter on a file, a dot on a folder. Colour is never the only signal: the letter is, and both
 *  carry the word for a screen reader and for hover. */
export function GitBadge({ mark, dir = false }: { mark: GitMark; dir?: boolean }) {
  const t = useT();
  const label = t(
    mark === "M"
      ? "edit.git.modified"
      : mark === "A"
        ? "edit.git.added"
        : mark === "U"
          ? "edit.git.untracked"
          : "edit.git.deleted",
  );
  return dir ? (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-git-mark={mark}
      className={cn("ml-auto h-1.5 w-1.5 shrink-0 rounded-full", DOT[mark])}
    />
  ) : (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-git-mark={mark}
      className={cn("ml-auto shrink-0 font-mono text-xs", TONE[mark])}
    >
      {mark}
    </span>
  );
}
