import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, File, Folder } from "lucide-react";

import { getFsTree } from "@/lib/api";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";

/**
 * The workspace, one directory at a time.
 *
 * A tree existed on the chat screen once and was deliberately deleted (`189e3c7`): asking someone to
 * navigate to the file they want changed hands them back the work the agent is there to do. That
 * reasoning holds for a conversation and does not transfer here — on an EDITOR screen, finding the
 * file IS the work, and there is nothing to hand back to.
 *
 * `/api/fs/tree` returns one level per call and caps its entries, so a folder of forty thousand
 * files costs one bounded request rather than a walk. Each directory is its own query, which means
 * expanding is incremental and the cache survives collapsing and reopening.
 */

function Node({
  workspace,
  path,
  name,
  isDir,
  depth,
  activePath,
  onOpen,
}: {
  workspace: string | null;
  path: string;
  name: string;
  isDir: boolean;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = !isDir && path === activePath;

  return (
    <>
      <button
        type="button"
        onClick={() => (isDir ? setOpen((o) => !o) : onOpen(path))}
        aria-expanded={isDir ? open : undefined}
        aria-current={active ? "true" : undefined}
        title={path}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-sm",
          "transition-colors duration-1 ease-out",
          focusRing,
          active
            ? "bg-accent/15 text-accent"
            : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
        )}
        // Indentation is data, not design: it comes from how deep the node is, so it cannot be a
        // utility class without one class per possible depth.
        style={{ paddingLeft: `${depth * 12 + 6}px` }}
      >
        {isDir ? (
          open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0" />
          )
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        {isDir ? (
          <Folder className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <File className="h-3.5 w-3.5 shrink-0" />
        )}
        {/* truncate, not wrap: a long filename must not reflow the whole tree. */}
        <span className="truncate">{name}</span>
      </button>
      {isDir && open && (
        <Level workspace={workspace} path={path} depth={depth + 1} activePath={activePath} onOpen={onOpen} />
      )}
    </>
  );
}

function Level({
  workspace,
  path,
  depth,
  activePath,
  onOpen,
}: {
  workspace: string | null;
  path: string;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  const tree = useQuery({
    queryKey: ["fs-tree", workspace, path],
    queryFn: () => getFsTree(workspace, path),
  });

  if (tree.isLoading) {
    return (
      <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground" style={{ paddingLeft: `${depth * 12 + 6}px` }}>
        <Spinner />
      </div>
    );
  }
  if (tree.isError) {
    // A directory that cannot be read is a fact about that directory, not a reason to blank the
    // tree. The rest of the workspace stays usable.
    return (
      <div className="py-1 text-xs text-bad" style={{ paddingLeft: `${depth * 12 + 6}px` }}>
        {t("edit.tree.unreadable")}
      </div>
    );
  }

  const entries = tree.data?.entries ?? [];
  if (entries.length === 0) {
    return (
      <div className="py-1 text-xs text-muted-foreground" style={{ paddingLeft: `${depth * 12 + 6}px` }}>
        {t("edit.tree.empty")}
      </div>
    );
  }

  return (
    <>
      {entries.map((e) => (
        <Node
          key={e.path}
          workspace={workspace}
          path={e.path}
          name={e.name}
          isDir={e.is_dir}
          depth={depth}
          activePath={activePath}
          onOpen={onOpen}
        />
      ))}
      {/* The server caps a listing rather than paginating it. Saying so is the difference between a
          folder that looks small and a folder that IS small. */}
      {tree.data?.capped && (
        <div className="py-1 text-xs text-muted-foreground" style={{ paddingLeft: `${depth * 12 + 6}px` }}>
          {t("edit.tree.capped")}
        </div>
      )}
    </>
  );
}

export function FileTree({
  workspace,
  activePath,
  onOpen,
}: {
  workspace: string | null;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  return (
    <nav aria-label={t("edit.tree.title")} className="flex h-full flex-col overflow-y-auto p-2">
      <Level workspace={workspace} path="" depth={0} activePath={activePath} onOpen={onOpen} />
    </nav>
  );
}
