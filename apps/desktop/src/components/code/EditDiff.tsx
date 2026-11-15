import { DiffView } from "@/components/code/DiffView";
import { diffStat, LARGE_DIFF_LINES } from "@/components/code/stepSummary";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The diff an edit left behind. A large one starts folded to its header, "file +N/−M", so a long
 *  rewrite does not push the rest of the answer off screen; a small one is open. The detailed view
 *  opens every diff. The path opens the file; the counts line beneath it folds the diff. */
export function EditDiff({
  path,
  patch,
  detailed,
  onOpenFile,
  commentScope,
}: {
  path: string;
  patch: string;
  detailed: boolean;
  onOpenFile?: (path: string) => void;
  /** The conversation the edit belongs to and where this diff sits in it: when given, the lines take
   *  comments sent to that conversation. */
  commentScope?: { sessionId: string; origin: string };
}) {
  const t = useT();
  const { additions, deletions } = diffStat(patch);
  const large = additions + deletions > LARGE_DIFF_LINES;
  return (
    <div className="rounded-chip border border-border px-2.5 py-2 text-xs">
      {/* A SIBLING of the fold, not a child of its <summary>: a button inside a summary is two
          interactive things nested in one, and a screen reader announces the summary of a
          `details` inconsistently when it holds a control (axe: nested-interactive). The path opens
          the file; the line under it folds the diff. */}
      <button
        type="button"
        onClick={() => onOpenFile?.(path)}
        className={cn(
          "block min-w-0 max-w-full truncate rounded-chip font-mono text-accent-ink underline decoration-dotted",
          focusRing,
        )}
      >
        {path}
      </button>
      <details open={detailed || !large} className="mt-1.5">
        <summary className={cn("flex cursor-pointer items-center justify-between gap-2 rounded-chip", focusRing)}>
          <span className="shrink-0 font-mono text-muted-foreground">+{additions}/−{deletions}</span>
          <span className="shrink-0 text-muted-foreground">{t("code.chat.diff.show")}</span>
        </summary>
        <div className="mt-2">
          <DiffView patch={patch} commentable={commentScope ? { ...commentScope, file: path } : undefined} />
        </div>
      </details>
    </div>
  );
}
