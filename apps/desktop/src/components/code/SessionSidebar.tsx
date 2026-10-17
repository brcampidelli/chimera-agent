import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, Check, ChevronRight, FolderPlus, Plus, X } from "lucide-react";

import {
  ApiError,
  archiveCodeSession,
  deleteCodeProject,
  deleteCodeSession,
  flagCodeProject,
  forgetCodeProject,
  forkCodeSession,
  getCodeSessionRaw,
  listArchivedCodeSessions,
  listCodeSessions,
  listRunningTurns,
  markCodeSessionDone,
  markCodeSessionSeen,
  registerCodeProject,
  unarchiveCodeSession,
  type CodeProject,
  type CodeSessionMeta,
  type CodeSessionState,
} from "@/lib/api";
import { ProjectHeader, type ExtraProjectAction } from "@/components/code/ProjectHeader";
import { SessionRow, type SessionRowActions } from "@/components/code/SessionRow";
import { SidebarToolbar, type StateFilter } from "@/components/code/SidebarToolbar";
import { HideRegionButton } from "@/components/shell/RegionToggle";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { aliasesOf, loadProjects } from "@/lib/projects";
import { groupSessions, matchesSearch, orderedSessions, type SidebarGroup } from "@/lib/sidebar-groups";
import { readCollapsed, readGroupWorktrees, writeCollapsed, writeGroupWorktrees } from "@/lib/sidebar-prefs";
import { usePendingApprovals } from "@/lib/usePendingApprovals";
import { cn } from "@/lib/utils";
import { readLastSession, writeLastSession } from "@/lib/workspace";

/** Rows a section shows before "show more". Five is what fits a 240px column several projects deep
 *  without the first project pushing every other one below the fold. */
const ROWS_PER_PROJECT = 5;

/** The conversations in the order the sidebar lists them, top to bottom — what Ctrl+Tab walks. Built
 *  from the same grouping the list draws with (`sidebar-groups.ts`), and reading the grouping switch
 *  at the moment of the keypress, so flipping it changes the walk with the list. */
export function sidebarSessionOrder(
  sessions: CodeSessionMeta[],
  rows: CodeProject[],
  current: string,
): CodeSessionMeta[] {
  return orderedSessions(sessions, rows, current, { byProject: readGroupWorktrees() });
}

/** Past conversations, filed under the project they were about.
 *
 * The shape is borrowed from every coding tool that got this right: your projects down the side,
 * each holding the conversations you had about it, a "+" to start a new one there, and the sections
 * you are not using folded out of the way. What it replaced was a text field asking for a folder path
 * — which made the screen ask "which directory?" before it asked "what do you want done".
 *
 * Which section a conversation is in is decided in `sidebar-groups.ts`: by repository, so a task's
 * worktree files under the project it belongs to, unless the person switches that off.
 */
export function SessionSidebar({
  workspace,
  activeSession,
  onResume,
  onNew,
  onNewIn,
  onProject,
  projectActions,
}: {
  workspace: string;
  activeSession: string | null;
  onResume: (session: CodeSessionMeta) => void;
  onNew: () => void;
  /** Start a new conversation in a project's folder. Without it, "+" switches and then starts one. */
  onNewIn?: (project: string) => void;
  onProject: (workspace: string) => void;
  /** More entries for a project's ⋯ menu, contributed by features that exist (worktree cleanup). */
  projectActions?: (group: SidebarGroup) => ExtraProjectAction[];
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
  /** The row's state: the server's, made current by the two polled sets. A pending approval
   *  outranks running because a blocked turn is necessarily still live until answered. */
  function stateOf(session: CodeSessionMeta): CodeSessionState {
    if (session.state === "blocked" || waitingIds.has(session.id)) return "blocked";
    if (runningIds.has(session.id) || session.running || session.state === "running")
      return "running";
    return session.state ?? "idle";
  }
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [query, setQuery] = useState("");
  const [byProject, setByProject] = useState(readGroupWorktrees);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(readCollapsed()));
  const [showingAll, setShowingAll] = useState<Set<string>>(() => new Set());
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
    | { kind: "session"; session: CodeSessionMeta }
    | { kind: "project"; project: string; label: string; workspaces: string[]; n: number }
    | null
  >(null);
  // One order for every view: the filter and the search narrow the full list rather than building
  // their own, so a pinned project stays where it was, recency is judged by all its conversations,
  // and a hidden folder stays hidden.
  const { groups: allGroups, currentKey } = groupSessions(q.data ?? [], rows, workspace, { byProject });
  const narrowed = (keep: (s: CodeSessionMeta, group: SidebarGroup) => boolean) =>
    allGroups
      .map((g): SidebarGroup => ({ ...g, sessions: g.sessions.filter((s) => keep(s, g)) }))
      .filter((g) => g.sessions.length > 0);
  const blockedCount = narrowed((s) => stateOf(s) === "blocked").reduce((n, g) => n + g.sessions.length, 0);
  const reviewCount = narrowed((s) => stateOf(s) === "review").reduce((n, g) => n + g.sessions.length, 0);
  const searching = query.trim() !== "";
  const narrowing = searching || stateFilter !== "all";
  const groups = narrowing
    ? narrowed(
        (s, g) =>
          (stateFilter === "all" || stateOf(s) === stateFilter) && matchesSearch(s, g.label, query),
      )
    : allGroups;
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

  const done = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) => markCodeSessionDone(id, done),
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
  // These writes return the whole list, which is why they return it: the sidebar takes the answer
  // it was given rather than asking again, so adding a project cannot briefly show the list without
  // it.
  const register = useMutation({
    mutationFn: ({ path, alias }: { path: string; alias?: string }) =>
      registerCodeProject(path, alias),
    onSuccess: (next) => qc.setQueryData(["code-projects"], next),
  });
  const hide = useMutation({
    mutationFn: (path: string) => flagCodeProject(path, { hidden: true }),
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
      // route that removes transcripts deleted nothing and left the row on screen.
      if (target.n === 0) {
        qc.setQueryData(["code-projects"], await forgetCodeProject(target.project));
        return;
      }
      // The server deletes by folder, and a project section can hold several: its main checkout
      // and each worktree that had a conversation. Every one of them, or the dialog's count lies.
      for (const folder of target.workspaces) {
        await deleteCodeProject(folder);
        writeLastSession(folder, null);
      }
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

  function toggleCollapsed(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      writeCollapsed([...next]);
      return next;
    });
  }

  function toggleGrouping(on: boolean) {
    writeGroupWorktrees(on);
    setByProject(on);
  }

  function askDeleteProject(key: string) {
    // The whole section, not what the filter shows: the server deletes every conversation of a
    // folder, so counting only the visible ones would understate what goes.
    const whole = allGroups.find((g) => g.key === key);
    const sessions = whole?.sessions ?? [];
    const workspaces = [...new Set([key, ...sessions.map((s) => s.workspace)])];
    setConfirming({ kind: "project", project: key, label: whole?.label || key, workspaces, n: sessions.length });
  }

  const rowActions: SessionRowActions = {
    onResume,
    onDone: (session, next) => done.mutate({ id: session.id, done: next }),
    onFork: (session) => fork.mutate(session.id),
    onInspect: setInspecting,
    onArchive: (session) => archive.mutate({ id: session.id, back: false }),
    onDelete: (session) => setConfirming({ kind: "session", session }),
    pending: { done: done.isPending, fork: fork.isPending, archive: archive.isPending },
  };

  /** The rows a section draws: all of them while searching or filtering (the point is to see what
   *  matched), otherwise the first five unless asked for more — and the open conversation always,
   *  even in a folded section, so the screen never loses the row you are in. */
  function drawnRows(group: SidebarGroup, folded: boolean): CodeSessionMeta[] {
    if (narrowing) return group.sessions;
    const isActive = (s: CodeSessionMeta) => s.id === activeSession;
    if (folded) return group.sessions.filter(isActive);
    if (showingAll.has(group.key)) return group.sessions;
    return group.sessions.filter((s, i) => i < ROWS_PER_PROJECT || isActive(s));
  }

  return (
    // `shrink-0`: this is a fixed rail, not a column that negotiates. `max-h-40` only while STACKED:
    // below `lg` the row is a COLUMN, and `shrink-0` then means "do not shrink my HEIGHT" — stacked,
    // this and the file viewer ate the whole row and the conversation resolved to zero height.
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

      <SidebarToolbar
        query={query}
        onQuery={setQuery}
        groupWorktrees={byProject}
        onGroupWorktrees={toggleGrouping}
        filter={stateFilter}
        onFilter={setStateFilter}
        blockedCount={blockedCount}
        reviewCount={reviewCount}
      />
      {archiveNote ? (
        <p role="alert" className="px-3 pb-2 text-xs text-bad-foreground">
          {archiveNote}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {narrowing && groups.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            {searching
              ? t("code.sessions.searchEmpty", { q: query.trim() })
              : t(stateFilter === "blocked" ? "code.sessions.filterWaitingEmpty" : "code.sessions.filterReviewEmpty")}
          </p>
        ) : q.isLoading && groups.length === 0 ? null : groups.length === 0 ? (
          // An empty list says so. Rendering nothing would look identical to a list that failed to
          // load, and the two mean opposite things to someone wondering where their work went.
          <p className="px-3 py-2 text-xs text-muted-foreground">{t("code.sessions.empty")}</p>
        ) : (
          groups.map((group) => {
            const folded = !narrowing && collapsed.has(group.key);
            const drawn = drawnRows(group, folded);
            const more = group.sessions.length - drawn.length;
            return (
              <div key={group.key} className="mb-1">
                {renaming === group.key ? (
                  <RenameForm
                    value={nameDraft}
                    onChange={setNameDraft}
                    onSubmit={commitRename}
                    onCancel={() => setRenaming(null)}
                  />
                ) : (
                  <ProjectHeader
                    group={group}
                    isOpen={group.key === currentKey}
                    collapsed={folded}
                    onToggle={() => toggleCollapsed(group.key)}
                    onNew={() => {
                      if (onNewIn) onNewIn(group.key);
                      else {
                        onProject(group.key);
                        onNew();
                      }
                    }}
                    onOpenProject={() => onProject(group.key)}
                    onRename={() => {
                      setNameDraft(aliases[group.key] ?? "");
                      setRenaming(group.key);
                    }}
                    onHide={() => hide.mutate(group.key)}
                    onDelete={() => askDeleteProject(group.key)}
                    extraActions={projectActions?.(group)}
                  />
                )}
                {drawn.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    state={stateOf(session)}
                    active={session.id === activeSession}
                    actions={rowActions}
                  />
                ))}
                {!narrowing && !folded && (more > 0 || (showingAll.has(group.key) && group.sessions.length > ROWS_PER_PROJECT)) ? (
                  <button
                    type="button"
                    onClick={() =>
                      setShowingAll((prev) => {
                        const next = new Set(prev);
                        if (next.has(group.key)) next.delete(group.key);
                        else next.add(group.key);
                        return next;
                      })
                    }
                    className={cn(
                      "w-full px-3 py-1 pl-9 text-left text-xs text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
                      focusRing,
                    )}
                  >
                    {more > 0 ? t("code.sessions.showMore", { n: more }) : t("code.sessions.showLess")}
                  </button>
                ) : null}
              </div>
            );
          })
        )}

        {/* Collapsed by default and asked for only when opened: the archive is where conversations
            go to be out of the way, and a list of them on every visit would undo that. */}
        <button
          type="button"
          aria-expanded={showArchived}
          onClick={() => setShowArchived((on) => !on)}
          className={cn(
            "flex w-full items-center gap-1 px-2 py-1 text-left text-xs text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
            focusRing,
          )}
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
              session, or it was pruned. */}
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
          an obvious wrong reading and the moment to correct it is before the click. */}
      <Dialog
        open={confirming !== null}
        onOpenChange={(next) => !next && setConfirming(null)}
        title={
          confirming?.kind === "project"
            ? confirming.n === 0
              ? t("code.projects.forgetTitle", { name: confirming.label })
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

/** Naming a project in place, where its header was. */
function RenameForm({
  value,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  return (
    <form
      className="flex items-center gap-1 px-2 py-1"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <input
        autoFocus
        className="field h-6 min-w-0 flex-1 px-1.5 text-xs"
        aria-label={t("code.projects.rename")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel()}
      />
      <button type="submit" aria-label={t("common.save")} className="text-accent">
        <Check className="h-3.5 w-3.5" />
      </button>
      <button type="button" aria-label={t("common.cancel")} className="text-muted-foreground" onClick={onCancel}>
        <X className="h-3.5 w-3.5" />
      </button>
    </form>
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
