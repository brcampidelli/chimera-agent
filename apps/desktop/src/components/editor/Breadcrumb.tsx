import { ChevronRight } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { aliasesOf, basename, loadProjects, projectLabel } from "@/lib/projects";
import { useT } from "@/lib/i18n";

/**
 * Where the open file is: the project by its NAME, then the folders down to the file.
 *
 * The path used to sit in the footer, truncated from the end — which cut off exactly the part that
 * says which file it is, and began with a drive letter and a home folder that say nothing. The
 * project is named the way the conversation list names it (your alias, else the folder's name), and
 * its full path is on hover, where it is there for anyone who needs it and in nobody's way.
 */
export function Breadcrumb({ workspace, path }: { workspace: string | null; path: string }) {
  const t = useT();
  // Same key as the sidebar's, so the same cache: no extra request when the list is already loaded.
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects, enabled: !!workspace });
  const project = workspace ? projectLabel(workspace, aliasesOf(projects.data ?? [])) || basename(workspace) : null;
  const parts = path.split(/[/\\]/).filter(Boolean);
  return (
    <nav aria-label={t("edit.breadcrumb")} className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-hairline px-3 py-1 text-xs text-muted-foreground">
      {project && (
        <>
          <span className="shrink-0 font-medium text-foreground" title={workspace ?? undefined} data-testid="edit-project">
            {project}
          </span>
          <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />
        </>
      )}
      {parts.map((part, i) => (
        <span key={i} className="flex shrink-0 items-center gap-1">
          {i > 0 && <ChevronRight className="h-3 w-3" aria-hidden />}
          <span
            className={i === parts.length - 1 ? "text-foreground" : undefined}
            aria-current={i === parts.length - 1 ? "page" : undefined}
          >
            {part}
          </span>
        </span>
      ))}
    </nav>
  );
}
