import { DiffView } from "@/components/code/DiffView";
import { diffStat, LARGE_DIFF_LINES } from "@/components/code/stepSummary";
import { useT } from "@/lib/i18n";

/** The diff an edit left behind. A large one starts folded to its header, "file +N/−M", so a long
 *  rewrite does not push the rest of the answer off screen; a small one is open. The detailed view
 *  opens every diff. The header is the file: clicking it opens the file, not the fold. */
export function EditDiff({
  path,
  patch,
  detailed,
  onOpenFile,
}: {
  path: string;
  patch: string;
  detailed: boolean;
  onOpenFile?: (path: string) => void;
}) {
  const t = useT();
  const { additions, deletions } = diffStat(patch);
  const large = additions + deletions > LARGE_DIFF_LINES;
  return (
    <details open={detailed || !large} className="rounded-chip border border-border px-2.5 py-2 text-xs">
      <summary className="flex cursor-pointer items-center justify-between gap-2">
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onOpenFile?.(path);
          }}
          className="min-w-0 truncate font-mono text-accent-ink underline decoration-dotted"
        >
          {path}
        </button>
        <span className="shrink-0 font-mono text-muted-foreground">+{additions}/−{deletions}</span>
        <span className="shrink-0 text-muted-foreground">{t("code.chat.diff.show")}</span>
      </summary>
      <div className="mt-2">
        <DiffView patch={patch} />
      </div>
    </details>
  );
}
