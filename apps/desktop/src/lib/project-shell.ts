/** Which projects the user has let the agent run commands in.
 *
 * Per project, and that is the whole point. Running `npm install` and the test suite is what
 * separates "wrote some files" from "built something that works" — and granting it for the folder
 * you are working in is a different decision from granting it for every folder you open next.
 *
 * The screen already had exactly one lever for this and it was global: `CHIMERA_REACH` in Settings,
 * which applies everywhere at once. So the honest choices were "no project may run commands" and
 * "every project may", and people who wanted the first for their documents folder and the second
 * for their code had to keep changing a setting.
 *
 * **Stored by absolute path.** Two projects with the same folder name are two projects, and the
 * path is what the backend is told. Nothing is inferred: a project not in this list has not been
 * granted anything, which is also what a fresh install, a cleared browser store and a new machine
 * all mean.
 *
 * The store is a convenience, not the enforcement. The server decides — `assemble_registry` opens
 * this only when the reach mounts the shell tools AND the request asks, and `CHIMERA_HOST_EXEC=deny`
 * still refuses regardless. Clearing this store cannot grant anything; it can only forget.
 */

export const SHELL_KEY = "chimera:code:shell-projects";

function read(): string[] {
  try {
    const raw = localStorage.getItem(SHELL_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === "string") : [];
  } catch {
    // A corrupt or unavailable store means nobody has granted anything — the safe read, and the
    // same answer a fresh install gives.
    return [];
  }
}

/** Whether the agent may run commands in this project. Absent, unreadable or blank means no. */
export function shellAllowed(workspace: string): boolean {
  if (!workspace) return false;
  return read().includes(workspace);
}

/** Grant or revoke for one project. Returns what the answer is afterwards. */
export function setShellAllowed(workspace: string, allowed: boolean): boolean {
  if (!workspace) return false;
  const atual = read().filter((p) => p !== workspace);
  const proximo = allowed ? [...atual, workspace] : atual;
  try {
    if (proximo.length) localStorage.setItem(SHELL_KEY, JSON.stringify(proximo));
    else localStorage.removeItem(SHELL_KEY);
  } catch {
    // Same reasoning as the theme and the workspace: a preference is never worth throwing over.
    // The grant simply will not survive the restart, which fails towards refusing.
  }
  return allowed;
}
