import type { ReactNode } from "react";
import { Search } from "lucide-react";

import { GitBadge, useGitMarks } from "@/components/editor/GitBadge";
import { fileIconFor } from "@/lib/fileIcons";
import { readRecent } from "@/lib/editorRecent";
import { shortcutLabel } from "@/lib/hotkeys";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

const CHANGED_SHOWN = 8;

function baseName(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}

function Row({ path, children, onOpen }: { path: string; children?: ReactNode; onOpen: (p: string) => void }) {
  const Icon = fileIconFor(baseName(path));
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(path)}
        title={path}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm text-muted-foreground",
          "transition-colors duration-1 ease-out hover:bg-surface-hover hover:text-foreground",
          focusRing,
        )}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="shrink-0 text-foreground">{baseName(path)}</span>
        <span className="min-w-0 flex-1 truncate text-xs">{path}</span>
        {children}
      </button>
    </li>
  );
}

/**
 * What the Editor shows with no file open.
 *
 * "No file open" with nothing to do about it sent people back to the tree to start over. This offers
 * the three ways back into work: the files you had open (per project), the files git says changed in
 * this folder (what an agent turn just touched), and the quick-open chord.
 */
export function EmptyEditor({
  workspace,
  onOpen,
  onQuickOpen,
}: {
  workspace: string | null;
  onOpen: (path: string) => void;
  onQuickOpen: () => void;
}) {
  const t = useT();
  const recent = readRecent(workspace);
  const { changed } = useGitMarks(workspace);
  // A deleted file has nothing to open.
  const openable = changed.filter((c) => c.mark !== "D");
  const shown = openable.slice(0, CHANGED_SHOWN);

  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto p-6">
      <div className="w-full max-w-md space-y-5">
        <div className="space-y-1 text-center">
          <p className="text-sm text-foreground">{t("edit.empty.title")}</p>
          <p className="text-xs text-muted-foreground">{t("edit.empty.hint")}</p>
        </div>
        <button
          type="button"
          onClick={onQuickOpen}
          className={cn(
            "flex w-full items-center gap-2 rounded-card border border-hairline px-3 py-2 text-sm text-muted-foreground",
            "transition-colors duration-1 ease-out hover:bg-surface-hover hover:text-foreground",
            focusRing,
          )}
        >
          <Search className="h-3.5 w-3.5" aria-hidden />
          <span className="flex-1 text-left">{t("edit.quickOpen.prompt")}</span>
          <kbd aria-hidden className="rounded-md px-1 text-xs ring-1 ring-hairline">
            {shortcutLabel("editorQuickOpen")}
          </kbd>
        </button>
        {recent.length > 0 && (
          <section aria-label={t("edit.empty.recent")}>
            <h3 className="mb-1 px-2 text-xs font-medium text-muted-foreground">{t("edit.empty.recent")}</h3>
            <ul>
              {recent.map((p) => (
                <Row key={p} path={p} onOpen={onOpen} />
              ))}
            </ul>
          </section>
        )}
        {shown.length > 0 && (
          <section aria-label={t("edit.empty.changed")}>
            <h3 className="mb-1 px-2 text-xs font-medium text-muted-foreground">{t("edit.empty.changed")}</h3>
            <ul>
              {shown.map((c) => (
                <Row key={c.path} path={c.path} onOpen={onOpen}>
                  <GitBadge mark={c.mark} />
                </Row>
              ))}
            </ul>
            {openable.length > shown.length && (
              <p className="px-2 pt-1 text-xs text-muted-foreground">
                {t("edit.empty.changedMore", { n: openable.length - shown.length })}
              </p>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
