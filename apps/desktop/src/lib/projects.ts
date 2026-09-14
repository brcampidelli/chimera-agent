/** The projects you work in, and what you call them.
 *
 * The sidebar groups conversations by project — but the only way a project could appear there was to
 * have already talked about it, because the group came from the conversations. So a project could
 * not be added before it was used, and the name was the last segment of its path: two repos checked
 * out as `frontend` read as the same project, and a folder called `app` names nothing.
 *
 * Both halves — the list and the names — now live on the server, under `CHIMERA_HOME`. Before, they
 * were in this browser's `localStorage`, which made "these are my projects" a statement about one
 * webview profile: clearing its storage lost the list, a reinstall started empty, and nothing
 * outside that one browser could read or seed it.
 *
 * That reverses an argument written here, and it is worth saying which one rather than quietly
 * deleting it: the aliases were client-side because "an alias is a preference about the interface
 * rather than a fact about the project". True, and it stops mattering once the list itself has to
 * outlive the interface — splitting them would leave half the answer portable, and the half left
 * behind is the half that makes a row recognisable.
 *
 * What stays local is the OLD data, kept as the migration source. It is read once, pushed to the
 * server, and then left alone rather than deleted: an older build reads it, and nothing here is
 * worth destroying to save two keys.
 *
 * Keyed by the workspace string exactly as it was stored, never a resolved path — same rule the
 * sidebar's grouping follows, and for the same reason: resolving needs a filesystem, would diverge
 * from what the conversations recorded, and would silently merge two projects that reach the same
 * directory through a symlink.
 */

import { listCodeProjects, registerCodeProject, type CodeProject } from "@/lib/api";

/** Where the list used to live. Read for migration; never written again. */
export const PROJECTS_KEY = "chimera:code:projects";
export const ALIASES_KEY = "chimera:code:projectNames";
/** Set once the old keys have been handed to the server, so the migration does not run every load. */
export const MIGRATED_KEY = "chimera:code:projectsMoved";

/** The projects this browser had stored before the list moved. Unknown shapes read as empty. */
export function legacyProjects(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(PROJECTS_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === "string" && !!p) : [];
  } catch {
    return [];
  }
}

/** The names this browser had stored, keyed by workspace. */
export function legacyAliases(): Record<string, string> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(ALIASES_KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(
      Object.entries(raw as Record<string, unknown>).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string" && !!entry[1],
      ),
    );
  } catch {
    return {};
  }
}

/** The registered projects, moving this browser's old list across first if it has not been.
 *
 * Migration failure is deliberately not swallowed into "done": the flag is set only after every
 * project is across, so a backend that was not up yet is retried on the next load. Re-registering is
 * idempotent on the path, so a migration that stopped halfway resumes without duplicating anything.
 */
export async function loadProjects(): Promise<CodeProject[]> {
  await migrateOnce();
  return listCodeProjects();
}

async function migrateOnce(): Promise<void> {
  if (readFlag()) return;
  const paths = legacyProjects();
  const names = legacyAliases();
  // A fresh install has nothing to move, and the flag is still set: retrying an empty migration on
  // every load is a request per load that can never do anything.
  for (const path of paths) await registerCodeProject(path, names[path]);
  writeFlag();
}

/** The names, as the sidebar wants them: keyed by workspace, absent when unnamed. */
export function aliasesOf(rows: CodeProject[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) if (row.alias) out[row.path] = row.alias;
  return out;
}

/** The last segment of a path, which is the best a machine can do without being told.
 *
 * Handles both separators because a Windows user and the paths a WSL checkout produces end up in the
 * same list, and drops trailing separators so `/a/b/` and `/a/b` do not read as different projects.
 */
export function basename(path: string): string {
  const trimmed = path.replace(/[/\\]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return (cut >= 0 ? trimmed.slice(cut + 1) : trimmed) || path;
}

/** What to call a project: your name for it, else the folder's. */
export function projectLabel(path: string, aliases: Record<string, string>): string {
  return aliases[path] || basename(path);
}

function readFlag(): boolean {
  try {
    return localStorage.getItem(MIGRATED_KEY) === "1";
  } catch {
    // Storage unavailable means the old keys are unreadable too, so there is nothing to migrate and
    // nothing to remember about having done it.
    return true;
  }
}

function writeFlag(): void {
  try {
    localStorage.setItem(MIGRATED_KEY, "1");
  } catch {
    // The migration will be attempted again next load. It is idempotent, so that costs requests
    // rather than correctness.
  }
}
