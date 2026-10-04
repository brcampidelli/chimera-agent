import type { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";

/** Pieces the Memory tools share: where a fact applies, and what to refresh after a write. */

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Where a fact applies — or WILL apply, in a preview, which is where it matters most.
 *
 * Shown before a write and not only after: an import or a merge that files a fact "everywhere"
 * makes it part of every conversation in every folder, and the badge on the stored row arrived
 * only once that had already happened. `fromProject` is the Claude project a note came from when
 * no registered folder matches it — a note about one repository that would apply everywhere. */
export function ScopeBadge({ project, fromProject }: { project?: string | null; fromProject?: string }) {
  const t = useT();
  if (project) {
    return (
      <Badge tone="muted" title={project}>
        {folderName(project)}
      </Badge>
    );
  }
  if (fromProject) {
    return (
      <Badge tone="warn" title={t("memory.import.strayHint", { slug: fromProject })}>
        {t("memory.everywhere")}
      </Badge>
    );
  }
  return (
    <Badge tone="accent" title={t("memory.everywhereHint")}>
      {t("memory.everywhere")}
    </Badge>
  );
}

export function invalidateMemory(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["memory"] });
  qc.invalidateQueries({ queryKey: ["memory-layers"] });
  qc.invalidateQueries({ queryKey: ["memory-profile"] });
}
