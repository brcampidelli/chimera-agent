import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  Braces,
  Check,
  ChevronRight,
  CopyPlus,
  FolderGit2,
  FolderPlus,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import {
  ApiError,
  archiveCodeSession,
  deleteCodeProject,
  deleteCodeSession,
  forgetCodeProject,
  forkCodeSession,
  getCodeSessionRaw,
  listArchivedCodeSessions,
  listCodeSessions,
  listRunningTurns,
  markCodeSessionSeen,
  registerCodeProject,
  unarchiveCodeSession,
  type CodeProject,
  type CodeSessionMeta,
  type CodeSessionState,
} from "@/lib/api";
import { HideRegionButton } from "@/components/shell/RegionToggle";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import { aliasesOf, loadProjects, projectLabel, sidebarOrder } from "@/lib/projects";
import { usePendingApprovals } from "@/lib/usePendingApprovals";
import { cn } from "@/lib/utils";
import { readLastSession, writeLastSession } from "@/lib/workspace";

/** How each state reads on a row: a dot, and the words a screen reader and a tooltip give it.
 *
 *  Idle draws nothing — an indicator at zero is noise, the rule `PendingApprovals` follows too. The
 *  colours are the status tokens: a question waiting is a warning, a failed turn is bad, edits to
 *  look at are the accent drawn hollow, so "something to see" never reads as "something wrong". */
const STATE_DOT: Record<Exclude<CodeSessionState, "idle">, { label: string; dot: string }> = {
  running: { label: "code.sessions.running", dot: "bg-accent" },
  waiting: { label: "code.sessions.state.waiting", dot: "bg-warn" },
  failed: { label: "code.sessions.state.failed", dot: "bg-bad" },
  review: { label: "code.sessions.state.review", dot: "border border-accent" },
};

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
  rows: CodeProject[],
  current: string,
): [string, CodeSessionMeta[]][] {
  const groups = new Map<string, CodeSessionMeta[]>();
  for (const session of sessions) {
    const key = session.workspace;
    const list = groups.get(key);
    if (list) list.push(session);
    else groups.set(key, [session]);
  }
  // Registered projects join the ones conversations placed — union, never replace: a project you
  // have talked about must not vanish from the list because you never got round to registering it.
  // The ORDER is `sidebarOrder`'s: pinned first, then most recently used, hidden ones left out.
  return sidebarOrder(sessions, rows, current).map((key) => [key, groups.get(key) ?? []]);
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
  // Which conversations have a turn running, asked often because the answer is kept in memory on the
  // server and is one small list. A turn keeps running when the screen that started it goes away, so
  // this is the only way a person who moved to another conversation learns that one is still working.
  const running = useQuery({
    queryKey: ["code-turns-running"],
    queryFn: listRunningTurns,
    refetchInterval: 4000,
  });
  const runningIds = new Set((running.data ?? []).map((turn) => turn.session_id));
  // The questions waiting for the owner, read from the cache the status bar already polls — no
  // second timer. A question arrives in the middle of a turn, when nothing else would refresh the
  // list, so this is what makes "waiting" show while it is true rather than after the turn.
  const approvals = usePendingApprovals({ poll: false });
  const waitingIds = new Set(
    (approvals.data ?? []).map((question) => question.session_id).filter(Boolean),
  );
  // When either set changes the list is stale: a task started in a new conversation has no file
  // until the agent finishes, so it is not in the list yet; one that ended has its final title,
  // count and state; one whose question was answered is no longer waiting.
  const liveKey = `${[...runningIds].sort().join(",")}|${[...waitingIds].sort().join(",")}`;
  const lastLiveKey = useRef<string | null>(null);
  useEffect(() => {
    if (running.data === undefined) return;
    if (lastLiveKey.current !== null && lastLiveKey.current !== liveKey) {
      void qc.invalidateQueries({ queryKey: ["code-sessions"] });
    }
    lastLiveKey.current = liveKey;
  }, [running.data, liveKey, qc]);
  /** The row's state: the server's, made current by the two polled sets. A question waiting
   *  outranks the turn that asked it, or "waiting" could never show. */
  function stateOf(session: CodeSessionMeta): CodeSessionState {
    if (session.state === "waiting" || waitingIds.has(session.id)) return "waiting";
    if (runningIds.has(session.id) || session.running || session.state === "running")
      return "running";
    return session.state ?? "idle";
  }
  const [onlyWaiting, setOnlyWaiting] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [archiveNote, setArchiveNote] = useState("");
  // Server state since the list stopped being a property of this browser profile. `loadProjects`
  // carries the one-time migration of whatever this webview had stored, so a running install keeps
  // its projects instead of meeting an empty sidebar after an update.
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects });
  const rows = projects.data ?? [];
  const aliases = aliasesOf(rows);
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
  // One order for both views: the filter narrows the full list rather than building its own, so a
  // pinned project stays where it was, recency is judged by all its conversations (not only the
  // waiting ones), and a hidden folder stays hidden.
  const allGroups = groupByProject(q.data ?? [], rows, workspace);
  // The filter shows only conversations and leaves out the empty projects you registered: "what is
  // waiting for me" has no answer in a project with no conversation.
  const waitingGroups = allGroups
    .map(([key, sessions]): [string, CodeSessionMeta[]] => [
      key,
      sessions.filter((s) => stateOf(s) === "waiting"),
    ])
    .filter(([, sessions]) => sessions.length > 0);
  // Counted from what the filter can show, so the chip never promises a conversation that sits in
  // a hidden folder and then answers "nothing waiting".
  const waitingCount = waitingGroups.reduce((n, [, sessions]) => n + sessions.length, 0);
  const groups = onlyWaiting ? waitingGroups : allGroups;
  const archived = useQuery({
    queryKey: ["code-sessions", "archived"],
    queryFn: () => listArchivedCodeSessions(),
    enabled: showArchived,
  });

  // The conversation on screen is being looked at, so its last edits are no longer "to review". On
  // opening it, and again when a turn of it ends while it is open (the list refreshes then and the
  // row reads "review" for the turn the person just watched). Only while the row says so, so a
  // conversation with nothing unseen costs no request.
  const activeReview =
    activeSession !== null &&
    (q.data ?? []).some((s) => s.id === activeSession && stateOf(s) === "review");
  useEffect(() => {
    if (!activeSession || !activeReview) return;
    markCodeSessionSeen(activeSession)
      .then(() => qc.invalidateQueries({ queryKey: ["code-sessions"] }))
      .catch(() => {
        // A badge that stays one refresh longer is not worth an error on screen.
      });
  }, [activeSession, activeReview, qc]);

  const archive = useMutation({
    mutationFn: ({ id, back }: { id: string; back: boolean }) =>
      back ? unarchiveCodeSession(id) : archiveCodeSession(id),
    onMutate: () => setArchiveNote(""),
    // A 409 is the server refusing to hide work in progress or a question waiting; the row stays,
    // and says why rather than appearing to ignore the click.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409)
        setArchiveNote(t("code.sessions.archiveRefused"));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["code-sessions"] }),
  });

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
  // Both writes return the whole list, which is why they return it: the sidebar takes the answer
  // it was given rather than asking again, so adding a project cannot briefly show the list without
  // it.
  const register = useMutation({
    mutationFn: ({ path, alias }: { path: string; alias?: string }) =>
      registerCodeProject(path, alias),
    onSuccess: (next) => qc.setQueryData(["code-projects"], next),
  });
  const remove = useMutation({
    mutationFn: async (target: NonNullable<typeof confirming>) => {
      if (target.kind === "session") {
        await deleteCodeSession(target.session.id);
        // Not the one its project reopens any more: an unknown id opens empty under the old id.
        if (readLastSession(target.session.workspace) === target.session.id)
          writeLastSession(target.session.workspace, null);
        return;
      }
      // A project with no conversations has nothing to delete BUT the bookmark. Sending it to the
      // route that removes transcripts deleted nothing and left the row on screen — a Delete that
      // does nothing, on precisely the rows this list now makes ordinary: the ones you added and
      // have not worked in yet.
      if (target.n === 0) {
        qc.setQueryData(["code-projects"], await forgetCodeProject(target.project));
        return;
      }
      await deleteCodeProject(target.project);
      writeLastSession(target.project, null);
    },
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
    register.mutate({ path });
    onProject(path); // adding a project is choosing it — the alternative is adding it and waiting
  }

  function commitRename() {
    if (renaming === null) return;
    register.mutate({ path: renaming, alias: nameDraft.trim() });
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
    <aside className="flex max-h-40 min-h-0 w-full shrink-0 flex-col border-r border-hairline lg:max-h-none">
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
        <HideRegionButton side="left" />
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

      {/* Only while something is waiting, or while the filter is on so it can be turned off. */}
      {waitingCount > 0 || onlyWaiting ? (
        <div className="px-2 pb-2">
          <button
            type="button"
            aria-pressed={onlyWaiting}
            onClick={() => setOnlyWaiting((on) => !on)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs transition-colors duration-1 ease-out",
              onlyWaiting
                ? "border-warn/40 bg-warn/15 text-warn-foreground"
                : "border-hairline text-muted-foreground hover:text-foreground",
            )}
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-warn" aria-hidden />
            {t("code.sessions.filterWaiting", { n: waitingCount })}
          </button>
        </div>
      ) : null}
      {archiveNote ? (
        <p role="alert" className="px-3 pb-2 text-xs text-bad-foreground">
          {archiveNote}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {onlyWaiting && groups.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            {t("code.sessions.filterWaitingEmpty")}
          </p>
        ) : q.isLoading && groups.length === 0 ? null : groups.length === 0 ? (
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
              {sessions.map((session) => {
                const state = stateOf(session);
                const dot = state === "idle" ? null : STATE_DOT[state];
                const name = session.title || t("code.sessions.untitled");
                return (
                  // A row, not one big button: the two actions below are buttons themselves, and a
                  // button inside a button is invalid markup that browsers resolve by dropping one of
                  // them — usually the inner one, silently.
                  <div key={session.id} className="group/session flex items-center">
                    <button
                      type="button"
                      onClick={() => onResume(session)}
                      title={name}
                      className={cn(
                        "min-w-0 flex-1 truncate px-3 py-1 pl-8 text-left text-xs transition-colors duration-1 ease-out",
                        session.id === activeSession
                          ? "bg-accent/15 text-accent-ink"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {dot ? (
                        <span
                          role="status"
                          aria-label={t(dot.label)}
                          title={t(dot.label)}
                          className={cn(
                            "mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle",
                            dot.dot,
                          )}
                        />
                      ) : null}
                      {name}
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
                    {/* Not offered while it works or waits: the server refuses those, and the
                        archive is a collapsed section where that state would go unseen. */}
                    {state === "running" || state === "waiting" ? null : (
                      <button
                        type="button"
                        aria-label={t("code.sessions.archiveOne", { name })}
                        disabled={archive.isPending}
                        className="px-1 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover/session:opacity-100"
                        onClick={() => archive.mutate({ id: session.id, back: false })}
                      >
                        <Archive className="h-3 w-3" />
                      </button>
                    )}
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
                );
              })}
            </div>
          ))
        )}

        {/* Collapsed by default and asked for only when opened: the archive is where conversations
            go to be out of the way, and a list of them on every visit would undo that. */}
        <button
          type="button"
          aria-expanded={showArchived}
          onClick={() => setShowArchived((on) => !on)}
          className="flex w-full items-center gap-1.5 px-3 py-1 text-left text-xs text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground"
        >
          <ChevronRight
            aria-hidden
            className={cn(
              "h-3.5 w-3.5 shrink-0 transition-transform duration-1 ease-out",
              showArchived && "rotate-90",
            )}
          />
          {t("code.sessions.archived")}
        </button>
        {showArchived ? (
          archived.data && archived.data.length === 0 ? (
            <p className="px-3 py-1 pl-8 text-xs text-muted-foreground">
              {t("code.sessions.archivedEmpty")}
            </p>
          ) : (
            (archived.data ?? []).map((session) => {
              const name = session.title || t("code.sessions.untitled");
              return (
                <div key={session.id} className="group/session flex items-center">
                  <button
                    type="button"
                    onClick={() => onResume(session)}
                    title={name}
                    className={cn(
                      "min-w-0 flex-1 truncate px-3 py-1 pl-8 text-left text-xs transition-colors duration-1 ease-out",
                      session.id === activeSession
                        ? "bg-accent/15 text-accent-ink"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {name}
                  </button>
                  <button
                    type="button"
                    aria-label={t("code.sessions.unarchiveOne", { name })}
                    disabled={archive.isPending}
                    className="px-2 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover/session:opacity-100"
                    onClick={() => archive.mutate({ id: session.id, back: true })}
                  >
                    <ArchiveRestore className="h-3 w-3" />
                  </button>
                </div>
              );
            })
          )
        ) : null}
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
            ? confirming.n === 0
              ? t("code.projects.forgetTitle", {
                  name: projectLabel(confirming.project, aliases),
                })
              : t("code.projects.deleteTitle", { n: confirming.n })
            : t("code.sessions.deleteTitle", {
                name: confirming?.kind === "session"
                  ? confirming.session.title || t("code.sessions.untitled")
                  : "",
              })
        }
      >
        <p className="text-sm text-muted-foreground">
          {confirming?.kind === "project"
            ? confirming.n === 0
              ? t("code.projects.forgetBody")
              : t("code.projects.deleteBody")
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
