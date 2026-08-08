import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, FolderGit2, FolderPlus, Pencil, Plus, X } from "lucide-react";

import { listCodeSessions, type CodeSessionMeta } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import {
  addProject,
  projectLabel,
  readAliases,
  readProjects,
  setAlias,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

/** Past conversations, filed under the project they were about.
 *
 * The shape is borrowed from every coding tool that got this right: your projects down the side,
 * each holding the conversations you had about it, and a button to start a new one. What it
 * replaced was a text field asking for a folder path — which made the screen ask "which directory?"
 * before it asked "what do you want done", and left every previous conversation unreachable.
 *
 * Grouping is by the raw `workspace` string the session stored, NOT by a resolved absolute path.
 * Resolving here would need the filesystem, would differ from what the session recorded, and would
 * silently merge two projects that happen to symlink to the same place — a tidier list that lies.
 */
function groupByProject(
  sessions: CodeSessionMeta[],
  registered: string[],
): [string, CodeSessionMeta[]][] {
  const groups = new Map<string, CodeSessionMeta[]>();
  for (const session of sessions) {
    const key = session.workspace;
    const list = groups.get(key);
    if (list) list.push(session);
    else groups.set(key, [session]);
  }
  // Registered projects come after, and only the ones no conversation already placed. Union, never
  // replace: a project you have talked about must not vanish from the list because you never got
  // round to registering it, and the ordering keeps the "most recently used" property below.
  for (const project of registered) {
    if (!groups.has(project)) groups.set(project, []);
  }
  // Insertion order = the order the server sent, which is newest-first. So the project you touched
  // most recently is at the top without a second sort deciding what "most recent project" means.
  return [...groups.entries()];
}

export function SessionSidebar({
  workspace,
  activeSession,
  onResume,
  onNew,
  onProject,
}: {
  workspace: string;
  activeSession: string | null;
  onResume: (session: CodeSessionMeta) => void;
  onNew: () => void;
  onProject: (workspace: string) => void;
}) {
  const t = useT();
  const q = useQuery({ queryKey: ["code-sessions"], queryFn: listCodeSessions });
  // Local rather than server state: both are preferences about this interface, and neither has an
  // endpoint. Held in state so adding or renaming redraws without a reload.
  const [registered, setRegistered] = useState(readProjects);
  const [aliases, setAliases] = useState(readAliases);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const groups = groupByProject(q.data ?? [], registered);

  function commitAdd() {
    const path = draft.trim();
    setAdding(false);
    setDraft("");
    if (!path) return;
    setRegistered(addProject(path));
    onProject(path); // adding a project is choosing it — the alternative is adding it and waiting
  }

  function commitRename() {
    if (renaming === null) return;
    setAliases(setAlias(renaming, nameDraft));
    setRenaming(null);
    setNameDraft("");
  }

  return (
    <aside className="flex min-h-0 w-60 flex-col border-r border-hairline">
      <div className="flex items-center gap-1 p-2">
        <Button size="sm" variant="ghost" className="flex-1 justify-start" onClick={onNew}>
          <Plus className="h-4 w-4" /> {t("code.sessions.new")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          title={t("code.projects.add")}
          aria-label={t("code.projects.add")}
          onClick={() => setAdding((on) => !on)}
        >
          <FolderPlus className="h-4 w-4" />
        </Button>
      </div>
      {adding ? (
        <form
          className="flex items-center gap-1 px-2 pb-2"
          onSubmit={(e) => {
            e.preventDefault();
            commitAdd();
          }}
        >
          <input
            autoFocus
            className="field h-7 min-w-0 flex-1 px-2 font-mono text-xs"
            placeholder={t("code.projects.pathPlaceholder")}
            aria-label={t("code.projects.add")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setAdding(false)}
          />
          <Button size="sm" type="submit" disabled={!draft.trim()}>
            <Check className="h-3.5 w-3.5" />
          </Button>
        </form>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {q.isLoading && groups.length === 0 ? null : groups.length === 0 ? (
          // An empty list says so. Rendering nothing would look identical to a list that failed to
          // load, and the two mean opposite things to someone wondering where their work went.
          <p className="px-3 py-2 text-xs text-muted-foreground">{t("code.sessions.empty")}</p>
        ) : (
          groups.map(([project, sessions]) => (
            <div key={project} className="group/project mb-2">
              {renaming === project ? (
                <form
                  className="flex items-center gap-1 px-2 py-1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    commitRename();
                  }}
                >
                  <input
                    autoFocus
                    className="field h-6 min-w-0 flex-1 px-1.5 text-xs"
                    aria-label={t("code.projects.rename")}
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Escape" && setRenaming(null)}
                  />
                  <button type="submit" aria-label={t("common.save")} className="text-accent">
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("common.cancel")}
                    className="text-muted-foreground"
                    onClick={() => setRenaming(null)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </form>
              ) : (
                <div className="flex items-center">
                  <button
                    type="button"
                    onClick={() => onProject(project)}
                    title={project || t("code.sessions.defaultProject")}
                    className={cn(
                      "flex min-w-0 flex-1 items-center gap-1.5 px-3 py-1 text-left text-xs font-semibold",
                      project === workspace
                        ? "text-accent"
                        : "text-foreground/70 hover:text-foreground",
                    )}
                  >
                    <FolderGit2 className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">
                      {project
                        ? projectLabel(project, aliases)
                        : t("code.sessions.defaultProject")}
                    </span>
                  </button>
                  {/* Renaming the default group would name the absence of a project. */}
                  {project ? (
                    <button
                      type="button"
                      aria-label={t("code.projects.renameOne", {
                        name: projectLabel(project, aliases),
                      })}
                      className="px-2 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover/project:opacity-100"
                      onClick={() => {
                        setNameDraft(aliases[project] ?? "");
                        setRenaming(project);
                      }}
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                  ) : null}
                </div>
              )}
              {sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => onResume(session)}
                  title={session.title || t("code.sessions.untitled")}
                  className={cn(
                    "block w-full truncate px-3 py-1 pl-8 text-left text-xs transition-colors",
                    session.id === activeSession
                      ? "bg-accent/15 text-accent"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {session.title || t("code.sessions.untitled")}
                </button>
              ))}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
