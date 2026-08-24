import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Braces,
  Check,
  CopyPlus,
  FolderGit2,
  FolderPlus,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import {
  deleteCodeProject,
  deleteCodeSession,
  forkCodeSession,
  getCodeSessionRaw,
  listCodeSessions,
  type CodeSessionMeta,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
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
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["code-sessions"], queryFn: listCodeSessions });
  // Local rather than server state: both are preferences about this interface, and neither has an
  // endpoint. Held in state so adding or renaming redraws without a reload.
  const [registered, setRegistered] = useState(readProjects);
  const [aliases, setAliases] = useState(readAliases);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [inspecting, setInspecting] = useState<CodeSessionMeta | null>(null);
  // What a confirmation is being asked about. One state for both kinds, because only one dialog can
  // be open — and holding the OBJECT rather than a boolean is what lets the dialog name the thing
  // and count it, instead of asking "are you sure?" about nothing in particular.
  const [confirming, setConfirming] = useState<
    { kind: "session"; session: CodeSessionMeta } | { kind: "project"; project: string; n: number } | null
  >(null);
  const groups = groupByProject(q.data ?? [], registered);

  const fork = useMutation({
    mutationFn: forkCodeSession,
    onSuccess: (branch) => {
      qc.invalidateQueries({ queryKey: ["code-sessions"] });
      // Straight into the branch. Forking and leaving the user in the parent would mean the next
      // thing they typed landed in the conversation they were trying to leave alone — which is the
      // one outcome duplicating exists to prevent.
      onResume(branch);
    },
  });
  const remove = useMutation({
    mutationFn: async (target: NonNullable<typeof confirming>) =>
      target.kind === "session"
        ? deleteCodeSession(target.session.id)
        : deleteCodeProject(target.project),
    // Closed on settle, not on success: a delete that failed leaves the row on screen, and a dialog
    // that stays open over it reads as "still working" for something that already stopped.
    onSettled: () => {
      setConfirming(null);
      qc.invalidateQueries({ queryKey: ["code-sessions"] });
    },
  });
  const raw = useQuery({
    queryKey: ["code-session-raw", inspecting?.id],
    queryFn: () => getCodeSessionRaw(inspecting?.id as string),
    enabled: inspecting !== null,
  });

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
    // `shrink-0`: this is a fixed 240px rail, not a column that negotiates. Letting it shrink meant
    // three columns competing for the same pixels and none of them winning cleanly.
    //
    // `max-h-40` only while STACKED. Below `lg` the row is a COLUMN, and `shrink-0` — right on the
    // horizontal axis, where this is a fixed rail — then means "do not shrink my HEIGHT". Stacked,
    // this and the file viewer ate the whole row and the conversation's `flex-1` resolved to zero:
    // measured at 1000x900 it had height 0 at y=1068, off a 900px window, and the shell scrolled to
    // 1424. The list scrolls inside itself already, so a cap costs only how many rows show at once.
    <aside className="flex max-h-40 min-h-0 w-60 shrink-0 flex-col border-r border-hairline lg:max-h-none">
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
                        ? "text-accent-ink"
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
                  {/* Also not for the default group: it is where conversations with no project
                      land, so "delete it" would mean deleting the ones nobody filed. */}
                  {project ? (
                    <button
                      type="button"
                      aria-label={t("code.projects.deleteOne", {
                        name: projectLabel(project, aliases),
                      })}
                      className="px-2 text-muted-foreground opacity-0 hover:text-bad-foreground focus:opacity-100 group-hover/project:opacity-100"
                      onClick={() =>
                        setConfirming({ kind: "project", project, n: sessions.length })
                      }
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  ) : null}
                </div>
              )}
              {sessions.map((session) => (
                // A row, not one big button: the two actions below are buttons themselves, and a
                // button inside a button is invalid markup that browsers resolve by dropping one of
                // them — usually the inner one, silently.
                <div key={session.id} className="group/session flex items-center">
                  <button
                    type="button"
                    onClick={() => onResume(session)}
                    title={session.title || t("code.sessions.untitled")}
                    className={cn(
                      "min-w-0 flex-1 truncate px-3 py-1 pl-8 text-left text-xs transition-colors duration-1 ease-out",
                      session.id === activeSession
                        ? "bg-accent/15 text-accent-ink"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {session.title || t("code.sessions.untitled")}
                  </button>
                  <button
                    type="button"
                    aria-label={t("code.sessions.forkOne", {
                      name: session.title || t("code.sessions.untitled"),
                    })}
                    disabled={fork.isPending}
                    className="px-1 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover/session:opacity-100"
                    onClick={() => fork.mutate(session.id)}
                  >
                    <CopyPlus className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("code.sessions.jsonOne", {
                      name: session.title || t("code.sessions.untitled"),
                    })}
                    className="px-2 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover/session:opacity-100"
                    onClick={() => setInspecting(session)}
                  >
                    <Braces className="h-3 w-3" />
                  </button>
                  {/* Deleting a conversation has existed on the server since the list did, and
                      reached the screen only as "Clear" — which acts on the conversation you have
                      OPEN. Every other row was permanent. */}
                  <button
                    type="button"
                    aria-label={t("code.sessions.deleteOne", {
                      name: session.title || t("code.sessions.untitled"),
                    })}
                    className="px-2 text-muted-foreground opacity-0 hover:text-bad-foreground focus:opacity-100 group-hover/session:opacity-100"
                    onClick={() => setConfirming({ kind: "session", session })}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      <Dialog
        open={inspecting !== null}
        onOpenChange={(next) => !next && setInspecting(null)}
        title={t("code.sessions.json")}
        description={inspecting?.title || undefined}
      >
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs text-muted-foreground">
          {/* Three states, not two. It read `data ? text : loading`, so a request that FAILED
              sat on "loading" for ever — and 404 here is ordinary: another window deleted the
              session, or it was pruned. Reproduced by deleting one and opening its JSON: a
              spinner-less "Carregando…" that never resolves, which reads as a hung app. */}
          {raw.isError
            ? t("code.sessions.jsonFailed")
            : raw.data
              ? readable(raw.data.text)
              : t("common.loading")}
        </pre>
        {raw.data ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("code.sessions.jsonBytes", { n: raw.data.bytes })}
          </p>
        ) : null}
      </Dialog>

      {/* One dialog for both, because only one can be open — and it NAMES what it is about.
          "Are you sure?" over an unnamed thing is how a person deletes the wrong row.

          The project body says the folder is untouched, out loud, because "delete the project" has
          an obvious wrong reading and the moment to correct it is before the click, not in a
          release note. */}
      <Dialog
        open={confirming !== null}
        onOpenChange={(next) => !next && setConfirming(null)}
        title={
          confirming?.kind === "project"
            ? t("code.projects.deleteTitle", { n: confirming.n })
            : t("code.sessions.deleteTitle", {
                name: confirming?.kind === "session"
                  ? confirming.session.title || t("code.sessions.untitled")
                  : "",
              })
        }
      >
        <p className="text-sm text-muted-foreground">
          {confirming?.kind === "project"
            ? t("code.projects.deleteBody")
            : t("code.sessions.deleteBody")}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirming(null)}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={remove.isPending}
            onClick={() => confirming && remove.mutate(confirming)}
          >
            {t("common.delete")}
          </Button>
        </div>
      </Dialog>
    </aside>
  );
}

/** The stored file, indented for reading — or verbatim when it will not parse.
 *
 * The server sends the bytes on disk, which are written without indentation and arrive as one very
 * long line. Indenting for display is a screen concern, and the fallback is the point rather than
 * politeness: the reason to open this at all is usually that something about a conversation is
 * wrong, and a pretty-printer that swallows a malformed file would hide exactly what was asked for.
 */
function readable(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
