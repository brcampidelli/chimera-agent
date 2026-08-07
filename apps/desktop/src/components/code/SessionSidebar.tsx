import { useQuery } from "@tanstack/react-query";
import { FolderGit2, Plus } from "lucide-react";

import { listCodeSessions, type CodeSessionMeta } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
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
function groupByProject(sessions: CodeSessionMeta[]): [string, CodeSessionMeta[]][] {
  const groups = new Map<string, CodeSessionMeta[]>();
  for (const session of sessions) {
    const key = session.workspace;
    const list = groups.get(key);
    if (list) list.push(session);
    else groups.set(key, [session]);
  }
  // Insertion order = the order the server sent, which is newest-first. So the project you touched
  // most recently is at the top without a second sort deciding what "most recent project" means.
  return [...groups.entries()];
}

/** The last path segment, which is what people call a project. Full path stays in the title. */
function projectName(workspace: string): string {
  const parts = workspace.split(/[/\\]/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : workspace;
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
  const groups = groupByProject(q.data ?? []);

  return (
    <aside className="flex min-h-0 w-60 flex-col border-r border-hairline">
      <div className="p-2">
        <Button size="sm" variant="ghost" className="w-full justify-start" onClick={onNew}>
          <Plus className="h-4 w-4" /> {t("code.sessions.new")}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {q.isLoading ? null : groups.length === 0 ? (
          // An empty list says so. Rendering nothing would look identical to a list that failed to
          // load, and the two mean opposite things to someone wondering where their work went.
          <p className="px-3 py-2 text-xs text-muted-foreground">{t("code.sessions.empty")}</p>
        ) : (
          groups.map(([project, sessions]) => (
            <div key={project} className="mb-2">
              <button
                type="button"
                onClick={() => onProject(project)}
                title={project || t("code.sessions.defaultProject")}
                className={cn(
                  "flex w-full items-center gap-1.5 px-3 py-1 text-left text-xs font-semibold",
                  project === workspace ? "text-accent" : "text-foreground/70 hover:text-foreground",
                )}
              >
                <FolderGit2 className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">
                  {project ? projectName(project) : t("code.sessions.defaultProject")}
                </span>
              </button>
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
