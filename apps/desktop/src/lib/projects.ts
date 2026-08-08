/** The projects you work in, and what you call them.
 *
 * The sidebar has always grouped conversations by project — but the only way a project could appear
 * there was to have already talked about it, because the group came from the conversations. So you
 * could not add a project before using it, and the name was the last segment of its path: two repos
 * checked out as `frontend` read as the same project, and a folder called `app` names nothing.
 *
 * Two things are stored, and they are deliberately separate:
 *
 * - **the list** is the set of projects you have added, so a project can exist with no conversations
 *   in it. A project you have talked about is implied by its conversations and does not have to be
 *   here — the two are unioned, never replaced, so nothing disappears from the sidebar because it
 *   was not registered.
 * - **the aliases** are what you call each one. Client-side on purpose: there is no rename endpoint
 *   anywhere, and an alias is a preference about the interface rather than a fact about the project,
 *   so it belongs beside the theme and the chosen workspace rather than in the agent's data.
 *
 * Keyed by the workspace string exactly as it was stored, never a resolved path — same rule the
 * sidebar's grouping follows, and for the same reason: resolving needs a filesystem, would diverge
 * from what the conversations recorded, and would silently merge two projects that reach the same
 * directory through a symlink.
 *
 * Same discipline as `workspace.ts` and `theme.ts`: storage can be unavailable, and a preference is
 * never worth throwing over.
 */

export const PROJECTS_KEY = "chimera:code:projects";
export const ALIASES_KEY = "chimera:code:projectNames";

/** The registered projects, in the order they were added. Unknown shapes read as empty. */
export function readProjects(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(PROJECTS_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === "string" && !!p) : [];
  } catch {
    return [];
  }
}

/** Add a project. Idempotent, and a blank path is not a project. */
export function addProject(path: string): string[] {
  const value = path.trim();
  if (!value) return readProjects();
  const next = readProjects();
  if (!next.includes(value)) next.push(value);
  write(PROJECTS_KEY, next);
  return next;
}

/** Forget a project, and its alias with it.
 *
 * Removing it from the list does NOT remove its conversations: they still exist on the server and
 * the sidebar still groups them, so the project reappears there — as a project you have talked about
 * rather than one you registered. Deleting conversations is a different act with a different button,
 * and quietly performing it here would make "tidy up my list" destroy work.
 */
export function removeProject(path: string): string[] {
  const next = readProjects().filter((p) => p !== path);
  write(PROJECTS_KEY, next);
  const names = readAliases();
  if (path in names) {
    delete names[path];
    write(ALIASES_KEY, names);
  }
  return next;
}

/** Every alias, keyed by workspace. */
export function readAliases(): Record<string, string> {
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

/** Name a project, or clear the name by passing an empty one.
 *
 * Clearing removes the key rather than storing "", so "no alias" round-trips as absence — the same
 * distinction `writeWorkspace` makes, and for the same reason: an empty string reads identically to
 * a missing one but is a different statement, and the fallback name depends on telling them apart.
 */
export function setAlias(path: string, alias: string): Record<string, string> {
  const names = readAliases();
  const value = alias.trim();
  if (value) names[path] = value;
  else delete names[path];
  write(ALIASES_KEY, names);
  return names;
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

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Same reasoning as the theme and the workspace: the choice will not survive a restart.
  }
}
