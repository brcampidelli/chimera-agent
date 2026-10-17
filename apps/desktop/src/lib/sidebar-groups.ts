/** Which section of the Code sidebar each conversation files under, and in what order.
 *
 * The list used to group by the raw `workspace` string each conversation stored. That is right for
 * folders and wrong for git worktrees: an orchestrator running tasks in parallel gives each task its
 * own `git worktree add` folder, and on the owner's disk 22 of 26 sections were checkouts of ONE
 * repository, each holding one conversation with the same truncated title.
 *
 * Since A1 (#965) every row carries `project` — the main working tree of the repository its folder
 * is a checkout of, stamped by the server at each turn and read from git's own files. Grouping by
 * `project || workspace` merges exactly what git declares to be one repository, and nothing else: a
 * symlink still does not merge two folders (the objection the old comment raised), two clones of one
 * remote stay two projects, and a folder outside git — or a conversation from before the stamp whose
 * folder is gone — keeps its own section as before.
 *
 * Grouping is the default and can be switched off (`sidebar-prefs.ts`); off is the old list, byte for
 * byte: exact strings, no path normalisation.
 */

import type { CodeProject, CodeSessionMeta } from "@/lib/api";
import { basename } from "@/lib/projects";

/** The fields grouping reads. A server older than A1 sends none of the checkout ones, and every row
 *  then groups by its workspace — the list it had. */
export type GroupableSession = Pick<CodeSessionMeta, "workspace" | "updated_at"> &
  Partial<Pick<CodeSessionMeta, "project" | "project_name" | "checkout_kind">>;

export interface SidebarGroup<S extends GroupableSession = CodeSessionMeta> {
  /** The folder the section stands for: what its "+", rename, hide and delete act on. For a git
   *  project it is the main checkout, so "+" starts there rather than in some task's worktree. */
  key: string;
  /** Your name for it, else the repository's, else the folder's. "" for the no-project section. */
  label: string;
  /** The parent folder's name, only when another visible section has the same label. */
  hint: string;
  sessions: S[];
  /** Distinct linked worktrees that have a conversation here. 0 with grouping off. */
  worktrees: number;
}

export interface GroupOptions {
  /** File worktrees under their repository (the default). */
  byProject: boolean;
}

/** Slashes one way, no trailing separator, a Windows drive in lower case — the same reading
 *  `side/rules.ts` uses. Only with grouping on: off is the old exact-string list. The server stamps
 *  `project` from a resolved path while a registered row keeps the string it was added with, and
 *  `C:\X` against `c:/X/` must not become two sections of one folder. */
function comparable(path: string): string {
  const forward = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return /^[a-zA-Z]:/.test(forward) ? forward.toLowerCase() : forward;
}

/** The folder a path's sections are keyed by, and the lookups that decide it. */
function keyer(sessions: readonly GroupableSession[], rows: readonly CodeProject[], byProject: boolean) {
  const cmp = byProject ? comparable : (path: string) => path;
  const rowByPath = new Map(rows.map((row) => [cmp(row.path), row] as const));
  // A folder's project, learnt from any conversation stamped in it: a registered worktree row, the
  // folder you have open and an older unstamped conversation in the same folder all follow it.
  const projectOf = new Map<string, string>();
  if (byProject) {
    for (const s of sessions) if (s.project) projectOf.set(cmp(s.workspace), s.project);
  }
  const keyOf = (path: string): string => {
    const root = projectOf.get(cmp(path)) ?? path;
    // A registered row wins the spelling, so its alias, pin and hidden flag apply to the section.
    return rowByPath.get(cmp(root))?.path ?? root;
  };
  return { cmp, row: (path: string) => rowByPath.get(cmp(path)), keyOf };
}

/** The sections, top to bottom, and which one holds the folder that is open.
 *
 * Order: pinned first, then most recently used (newest conversation, or the row's `last_used_at`
 * stamped when a turn starts, whichever is later), then registered projects nobody used, in the
 * order they were added. A hidden row hides its folder's conversations and its section — and with
 * grouping on, hiding a project hides its worktrees with it. Except what is OPEN: hiding the folder
 * you are in must not make the screen lose track of where you are.
 */
export function groupSessions<S extends GroupableSession>(
  sessions: readonly S[],
  rows: readonly CodeProject[],
  current: string,
  { byProject }: GroupOptions,
): { groups: SidebarGroup<S>[]; currentKey: string } {
  const { cmp, row, keyOf } = keyer(sessions, rows, byProject);
  const currentKey = keyOf(current);
  const hidden = (path: string) => row(path)?.hidden === true;
  const groups = new Map<string, SidebarGroup<S>>();
  const recency = new Map<string, number>();
  const named = new Map<string, string>();
  const trees = new Map<string, Set<string>>();
  const open = (key: string) => {
    let group = groups.get(key);
    if (!group) {
      group = { key, label: "", hint: "", sessions: [], worktrees: 0 };
      groups.set(key, group);
      recency.set(key, 0);
    }
    return group;
  };
  for (const s of sessions) {
    if (hidden(s.workspace) && cmp(s.workspace) !== cmp(current)) continue;
    const key = byProject ? keyOf(s.project || s.workspace) : s.workspace;
    open(key).sessions.push(s);
    recency.set(key, Math.max(recency.get(key) ?? 0, s.updated_at));
    if (byProject && s.project_name && !named.has(key)) named.set(key, s.project_name);
    if (byProject && s.checkout_kind === "worktree") {
      const set = trees.get(key) ?? new Set<string>();
      set.add(cmp(s.workspace));
      trees.set(key, set);
    }
  }
  for (const r of rows) {
    const key = byProject ? keyOf(r.path) : r.path;
    open(key);
    const stamped = Date.parse(r.last_used_at ?? "");
    if (!Number.isNaN(stamped)) recency.set(key, Math.max(recency.get(key) ?? 0, stamped / 1000));
  }
  const visible = [...groups.values()]
    .filter((g) => g.key === currentKey || !hidden(g.key))
    .sort((a, b) => {
      const pinned = Number(row(b.key)?.pinned === true) - Number(row(a.key)?.pinned === true);
      return pinned || (recency.get(b.key) ?? 0) - (recency.get(a.key) ?? 0);
    });
  for (const g of visible) {
    g.label = g.key ? row(g.key)?.alias || named.get(g.key) || basename(g.key) : "";
    g.worktrees = trees.get(g.key)?.size ?? 0;
  }
  disambiguate(visible);
  return { groups: visible, currentKey };
}

/** Two visible sections that would read the same get their parent folder beside the name: two
 *  repositories both checked out as `app`, or an alias equal to another folder's name. */
function disambiguate(groups: SidebarGroup<GroupableSession>[]): void {
  const seen = new Map<string, number>();
  for (const g of groups) if (g.key) seen.set(g.label.toLowerCase(), (seen.get(g.label.toLowerCase()) ?? 0) + 1);
  for (const g of groups) {
    if (!g.key || (seen.get(g.label.toLowerCase()) ?? 0) < 2) continue;
    const parent = g.key.replace(/[/\\]+$/, "").replace(/[/\\][^/\\]*$/, "");
    g.hint = parent && parent !== g.key ? basename(parent) : "";
  }
}

/** The conversations in the order the sidebar lists them, top to bottom — what Ctrl+Tab walks.
 *
 * Built from the same grouping the list draws with, so the shortcut never visits them in an order the
 * person cannot see and never lands in a folder the sidebar hides. A folded section and "show more"
 * only decide which rows are DRAWN, never their order — and the row the screen has open is always
 * drawn, so the conversation Ctrl+Tab lands on is on screen even inside a folded section.
 */
export function orderedSessions<S extends GroupableSession>(
  sessions: readonly S[],
  rows: readonly CodeProject[],
  current: string,
  options: GroupOptions,
): S[] {
  return groupSessions(sessions, rows, current, options).groups.flatMap((g) => g.sessions);
}

/** Whether a conversation answers a search typed in the sidebar: its title, its branch, or the name
 *  of the section it is in. Case-insensitive, on the client — the list is already here. */
export function matchesSearch(
  session: Pick<CodeSessionMeta, "title"> & Partial<Pick<CodeSessionMeta, "branch">>,
  groupLabel: string,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [session.title, session.branch ?? "", groupLabel].some((field) => field.toLowerCase().includes(q));
}
