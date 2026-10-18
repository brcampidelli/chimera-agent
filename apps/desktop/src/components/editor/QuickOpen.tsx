import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/dialog";
import { findPaths } from "@/lib/api";
import { fileIconFor } from "@/lib/fileIcons";
import { readRecent } from "@/lib/editorRecent";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const SHOWN = 30;

function baseName(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}

/**
 * Ctrl+P: open a file by typing part of its name.
 *
 * The matching is the server's (`/api/fs/paths`, the same best-first fuzzy listing the composer's
 * `@` uses), so this and the `@` list agree on what "login" means. It lists names only; nothing is
 * read until one is chosen. With nothing typed it offers the files last opened.
 */
export function QuickOpen({
  open,
  workspace,
  onClose,
  onOpen,
}: {
  open: boolean;
  workspace: string | null;
  onClose: () => void;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  useEffect(() => {
    if (open) {
      setQuery("");
      setAt(0);
    }
  }, [open]);

  const q = query.trim();
  const found = useQuery({
    queryKey: ["quick-open", workspace ?? "", q],
    queryFn: () => findPaths(workspace, q, SHOWN),
    enabled: open && q !== "",
  });
  const paths =
    q === "" ? readRecent(workspace) : (found.data?.entries ?? []).filter((e) => !e.is_dir).map((e) => e.path);

  const choose = (path: string | undefined) => {
    if (!path) return;
    onClose();
    onOpen(path);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()} title={t("edit.quickOpen.title")}>
      <input
        autoFocus
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setAt(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setAt((i) => Math.min(i + 1, Math.max(paths.length - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setAt((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            choose(paths[at]);
          }
        }}
        placeholder={t("edit.quickOpen.placeholder")}
        aria-label={t("edit.quickOpen.placeholder")}
        role="combobox"
        aria-expanded
        aria-controls="quick-open-list"
        className="field w-full px-2 text-sm"
      />
      <ul id="quick-open-list" role="listbox" className="mt-2 max-h-72 overflow-y-auto">
        {paths.map((p, i) => {
          const Icon = fileIconFor(baseName(p));
          return (
            <li
              key={p}
              role="option"
              aria-selected={i === at}
              onMouseEnter={() => setAt(i)}
              onClick={() => choose(p)}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm",
                i === at ? "bg-accent/15 text-accent-ink" : "text-muted-foreground",
              )}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="shrink-0 text-foreground">{baseName(p)}</span>
              <span className="min-w-0 flex-1 truncate text-xs" title={p}>
                {p}
              </span>
            </li>
          );
        })}
        {paths.length === 0 && q !== "" && !found.isLoading && (
          <li className="px-2 py-1 text-xs text-muted-foreground">{t("edit.quickOpen.none")}</li>
        )}
      </ul>
    </Dialog>
  );
}
