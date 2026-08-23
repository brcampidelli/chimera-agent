import { useState } from "react";
import { FolderTree, Search } from "lucide-react";

import { FileTree } from "@/components/editor/FileTree";
import { SearchPanel } from "@/components/editor/SearchPanel";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";

/**
 * The left slot of the editor screen: which folder, and what is in it.
 *
 * The workspace field is the same choice the chat screen makes and is stored in the same place
 * (`lib/workspace.ts`), so picking a folder on one screen is picking it on both. Two independent
 * "current project" values in one app is a bug that presents as the agent editing the wrong tree.
 *
 * Tree and search share the slot rather than stacking, because they answer the same question by
 * different routes — "where is the thing I want to open" — and only one of them is ever the answer.
 */
export function EditSidebar({
  workspace,
  onWorkspace,
  activePath,
  onOpen,
}: {
  workspace: string;
  onWorkspace: (value: string) => void;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  const [mode, setMode] = useState<"files" | "search">("files");

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-hairline p-2">
        <label className="sr-only" htmlFor="edit-workspace">
          {t("code.workspace")}
        </label>
        <input
          id="edit-workspace"
          value={workspace}
          onChange={(e) => onWorkspace(e.target.value)}
          placeholder={t("code.workspacePlaceholder")}
          className="field w-full px-2 text-xs"
        />
        <div className="mt-2 flex overflow-hidden rounded-chip border border-border">
          {(
            [
              ["files", FolderTree, "edit.tree.title"],
              ["search", Search, "edit.search.title"],
            ] as const
          ).map(([key, Icon, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setMode(key)}
              aria-pressed={mode === key}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 px-2 py-1 text-xs",
                "transition-colors duration-1 ease-out",
                focusRing,
                mode === key
                  ? "bg-accent/20 text-accent-ink"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {t(label)}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {mode === "files" ? (
          <FileTree workspace={workspace || null} activePath={activePath} onOpen={onOpen} />
        ) : (
          <SearchPanel
            workspace={workspace || null}
            activePath={activePath}
            // The line is dropped for now: the editor opens the file, and jumping to a line needs a
            // seam through the tab state that P1 did not build. Opening the right file is most of
            // the value and pretending otherwise would put a number in the URL nothing reads.
            onOpen={(path) => onOpen(path)}
          />
        )}
      </div>
    </div>
  );
}
